// vault.js — PIN-encrypted vault, same format as the web wallet:
// blob = base64( salt[16] || iv[12] || AES-GCM(PBKDF2(pin, 100k).encrypt(JSON{words,privateKey})) )

async function derivePinKey(pin, salt) {
  const keyMaterial = await crypto.subtle.importKey(
    'raw', new TextEncoder().encode(pin),
    { name: 'PBKDF2' }, false, ['deriveKey']);
  return crypto.subtle.deriveKey(
    { name: 'PBKDF2', salt, iterations: 100000, hash: 'SHA-256' },
    keyMaterial, { name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);
}

async function encryptSeed(pin, words, privateKeyBytes) {
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const key = await derivePinKey(pin, salt);
  const payload = { words, privateKey: Array.from(privateKeyBytes) };
  const ct = await crypto.subtle.encrypt(
    { name: 'AES-GCM', iv }, key, new TextEncoder().encode(JSON.stringify(payload)));
  const combined = new Uint8Array(16 + 12 + ct.byteLength);
  combined.set(salt, 0); combined.set(iv, 16); combined.set(new Uint8Array(ct), 28);
  let bin = '';
  for (const b of combined) bin += String.fromCharCode(b);
  return btoa(bin);
}

async function decryptSeed(pin, encryptedB64) {
  const combined = Uint8Array.from(atob(encryptedB64), c => c.charCodeAt(0));
  const salt = combined.slice(0, 16), iv = combined.slice(16, 28), ct = combined.slice(28);
  const key = await derivePinKey(pin, salt);
  const dec = await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, key, ct);
  return JSON.parse(new TextDecoder().decode(dec));
}
