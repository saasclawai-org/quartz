# Quartz Wallet Extension

MetaMask-style browser wallet for Quartz — MV3, ~250 lines of core logic, zero network requests.

## What it does
- Holds your Quartz wallet (12-word seed, PIN-encrypted in `chrome.storage.local` — same AES-GCM vault format as the web wallet)
- Injects `window.quartz` into every page (EIP-1193-flavoured: `quartz.request({method, params})`)
- Any site can request `quartz_accounts` (connect) or `quartz_signMessage` (sign) — each requires a click in an approval popup that shows origin + address + message
- The private key never leaves the extension. The extension itself makes **zero network requests** — it's a pure signer; the page posts the signature to the node itself

## Install (developer mode — pre-store)
1. Download & unzip this folder
2. `chrome://extensions` → enable **Developer mode** (top right)
3. **Load unpacked** → select the unzipped `extension/` folder
4. Click the 🔮 icon → create or import a wallet

## Try it
https://quartzchain.net/demos/login.html — the "🧩 Sign in with the Quartz extension" button appears automatically when the extension is installed.

## Site API
```js
const accounts = await quartz.request({ method: 'quartz_accounts' });
// → [{ address: 'Q…' }]        (approval required first time per site)

const r = await quartz.request({ method: 'quartz_signMessage', params: { message: challenge } });
// → { address, public_key, signature, message }   (approval required every time)
```

## Notes / honest limits
- First interaction with a site = two approvals (connect, then sign) — same as MetaMask
- Unlocked keys live in service-worker memory only; the worker idles out after ~30 s → auto-lock
- Approved-site list is remembered; no per-site revoke UI yet (clear extension data to reset)
- Chrome/Edge (MV3 `world: "MAIN"`). Firefox 128+ should work but is untested
- Not on the Web Store — that needs a developer account and review; load-unpacked only for now
