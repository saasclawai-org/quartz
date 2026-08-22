// background.js — MV3 service worker. Holds the vault (chrome.storage.local,
// PIN-encrypted, same blob format as the web wallet) and the unlocked key
// (memory only — dies with the worker, which auto-locks the wallet).
// Makes ZERO network requests: pure signer. The page does its own verify POST.

importScripts('lib/nacl.min.js', 'lib/wordlist.js', 'lib/quartz-crypto.js', 'vault.js');

const VAULT_KEY = 'quartz_vault_v1';
const SITES_KEY = 'quartz_sites_v1';

let unlocked = null;   // { words, privateKey: Uint8Array, publicKey, address }
let pending = null;    // { id, kind: 'connect'|'sign', origin, message, tabId }

const hex = (u8) => Array.from(u8, b => b.toString(16).padStart(2, '0')).join('');

async function getVault() {
  return (await chrome.storage.local.get(VAULT_KEY))[VAULT_KEY] || null;
}

async function getSites() {
  return (await chrome.storage.local.get(SITES_KEY))[SITES_KEY] || {};
}

async function setPending(p) {
  pending = p;
  if (p) await chrome.storage.session.set({ pending: p });
  else await chrome.storage.session.remove('pending');
}

// Recover pending if the service worker restarted mid-approval
chrome.storage.session.get('pending').then((s) => { if (s?.pending) pending = s.pending; }).catch(() => {});

function openApproval() {
  chrome.windows.create({
    url: chrome.runtime.getURL('approve.html'),
    type: 'popup', width: 420, height: 500,
  });
}

async function deliver(id, tabId, result, error) {
  await setPending(null);
  try {
    await chrome.tabs.sendMessage(tabId, { type: 'QZ_RESULT', id, result, error });
  } catch (e) { /* tab gone — nothing to deliver */ }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    switch (msg?.type) {

      case 'QZ_STATUS': {
        const vault = await getVault();
        sendResponse({ initialized: !!vault, unlocked: !!unlocked,
                       address: unlocked?.address || vault?.address || null });
        return;
      }

      // ---- from content script (sender.origin is the TRUE origin) ----
      case 'QZ_CONNECT': {
        const vault = await getVault();
        if (!vault) { sendResponse({ error: 'No wallet — open the extension popup to create one' }); return; }
        if (sender.origin.startsWith('chrome-extension:')) {
          sendResponse({ error: 'extension pages cannot connect' }); return;
        }
        const sites = await getSites();
        if (sites[sender.origin]) {
          sendResponse({ accounts: [{ address: unlocked?.address || vault.address }] });
          return;
        }
        await setPending({ id: msg.reqId, kind: 'connect', origin: sender.origin, tabId: sender.tab.id });
        openApproval();
        sendResponse({ pendingId: pending.id });
        return;
      }

      case 'QZ_SIGN': {
        const vault = await getVault();
        if (!vault) { sendResponse({ error: 'No wallet — open the extension popup to create one' }); return; }
        if (sender.origin.startsWith('chrome-extension:')) {
          sendResponse({ error: 'extension pages cannot sign' }); return;
        }
        const sites = await getSites();
        if (!sites[sender.origin]) {
          await setPending({ id: msg.reqId, kind: 'connect-first', origin: sender.origin,
                             message: msg.message, tabId: sender.tab.id });
          openApproval();
          sendResponse({ pendingId: pending.id });
          return;
        }
        await setPending({ id: msg.reqId, kind: 'sign', origin: sender.origin,
                           message: msg.message, tabId: sender.tab.id });
        openApproval();
        sendResponse({ pendingId: pending.id });
        return;
      }

      // ---- from popup / approve pages (trusted extension pages) ----
      case 'QZ_ONBOARD': {
        if (unlocked || (await getVault())) { sendResponse({ error: 'A wallet already exists' }); return; }
        const words = msg.words.trim().split(/\s+/);
        if (words.length !== 12) { sendResponse({ error: 'Need exactly 12 words' }); return; }
        if (!msg.pin || msg.pin.length < 4) { sendResponse({ error: 'PIN must be 4+ chars' }); return; }
        try {
          await loadCrypto();
          const w = await importQuartzWallet(words);   // throws on bad checksum
          const encrypted = await encryptSeed(msg.pin, w.words, w.privateKey);
          const vault = { address: w.address,
                          publicKey: Array.from(w.publicKey),
                          encrypted_seed: encrypted };
          await chrome.storage.local.set({ [VAULT_KEY]: vault });
          unlocked = { words: w.words, privateKey: w.privateKey, publicKey: w.publicKey, address: w.address };
          sendResponse({ ok: true, address: w.address });
        } catch (e) {
          sendResponse({ error: 'Invalid seed phrase (checksum failed?)' });
        }
        return;
      }

      case 'QZ_CREATE': {
        if (unlocked || (await getVault())) { sendResponse({ error: 'A wallet already exists' }); return; }
        if (!msg.pin || msg.pin.length < 4) { sendResponse({ error: 'PIN must be 4+ chars' }); return; }
        try {
          await loadCrypto();
          const w = await createQuartzWallet();
          const encrypted = await encryptSeed(msg.pin, w.words, w.privateKey);
          await chrome.storage.local.set({ [VAULT_KEY]: {
            address: w.address, publicKey: Array.from(w.publicKey), encrypted_seed: encrypted } });
          unlocked = { words: w.words, privateKey: w.privateKey, publicKey: w.publicKey, address: w.address };
          sendResponse({ ok: true, address: w.address, words: w.words });
        } catch (e) {
          sendResponse({ error: 'create failed: ' + (e?.message || e) });
        }
        return;
      }

      case 'QZ_UNLOCK': {
        const vault = await getVault();
        if (!vault) { sendResponse({ error: 'No wallet' }); return; }
        try {
          const dec = await decryptSeed(msg.pin, vault.encrypted_seed);
          unlocked = { words: dec.words, privateKey: Uint8Array.from(dec.privateKey),
                       publicKey: Uint8Array.from(vault.publicKey), address: vault.address };
          sendResponse({ ok: true, address: vault.address });
        } catch (e) { sendResponse({ error: 'Wrong PIN' }); }
        return;
      }

      case 'QZ_LOCK': {
        unlocked = null;
        sendResponse({ ok: true });
        return;
      }

      case 'QZ_GET_PENDING': {
        const vault = await getVault();
        sendResponse({ pending, unlocked: !!unlocked,
                       address: unlocked?.address || vault?.address || null });
        return;
      }

      case 'QZ_DECIDE': {
        if (!pending) { sendResponse({ error: 'nothing pending' }); return; }
        const p = pending;

        if (!msg.approve) {
          await deliver(p.id, p.tabId, null, 'User rejected the request');
          sendResponse({ ok: true });
          return;
        }

        if (p.kind === 'connect' || p.kind === 'connect-first') {
          const sites = await getSites();
          sites[p.origin] = Date.now();
          await chrome.storage.local.set({ [SITES_KEY]: sites });

          if (p.kind === 'connect') {
            // plain connect: answer the page directly
            await deliver(p.id, p.tabId, [{ address: unlocked?.address || (await getVault()).address }], null);
            sendResponse({ ok: true, connected: true });
          } else {
            // connect-first: fall through to signing after unlock check
            p.kind = 'sign';
            await setPending(p);
            const vault = await getVault();
            if (!unlocked) { sendResponse({ ok: true, needPin: true }); return; }
            await doSign(p);
            sendResponse({ ok: true, signed: true });
          }
          return;
        }

        // kind === 'sign'
        if (!unlocked) { sendResponse({ error: 'locked' }); return; }
        await doSign(p);
        sendResponse({ ok: true, signed: true });
        return;
      }
    }
    sendResponse({ error: 'unknown type' });
  })();
  return true; // async response
});

async function doSign(p) {
  // nacl.sign wants the 64-byte secretKey; the vault holds the 32-byte seed
  const kp = nacl.sign.keyPair.fromSeed(unlocked.privateKey);
  const sig = nacl.sign.detached(new TextEncoder().encode(p.message), kp.secretKey);
  const result = { address: unlocked.address,
                   public_key: hex(unlocked.publicKey),
                   signature: hex(sig), message: p.message };
  await deliver(p.id, p.tabId, result, null);
}
