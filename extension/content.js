// content.js — ISOLATED world bridge: page (MAIN) ⇄ postMessage ⇄ here ⇄ chrome.runtime ⇄ background.
// The page's request id (d.id) MUST round-trip all the way back — it is how the
// page matches async approval results to its pending promises.

window.addEventListener('message', (ev) => {
  if (ev.source !== window) return;
  const d = ev.data;
  if (!d || d.source !== 'quartz-inpage') return;

  const type = d.method === 'accounts' ? 'QZ_CONNECT' : d.method === 'signMessage' ? 'QZ_SIGN' : null;
  if (!type) {
    window.postMessage({ source: 'quartz-extension', id: d.id, error: 'unknown method' }, window.location.origin);
    return;
  }

  chrome.runtime.sendMessage({ type, reqId: d.id, message: d.params?.message }, (resp) => {
    // Sync answers: approved-site accounts list, or errors. Signing goes
    // async: user must click Approve in the popup window; result arrives
    // later as a QZ_RESULT message carrying this same reqId.
    if (chrome.runtime.lastError) {
      window.postMessage({ source: 'quartz-extension', id: d.id, error: chrome.runtime.lastError.message }, window.location.origin);
    } else if (resp && resp.error) {
      window.postMessage({ source: 'quartz-extension', id: d.id, error: resp.error }, window.location.origin);
    } else if (resp && resp.accounts) {
      window.postMessage({ source: 'quartz-extension', id: d.id, result: resp.accounts }, window.location.origin);
    }
    // else: async — QZ_RESULT will arrive
  });
});

chrome.runtime.onMessage.addListener((msg) => {
  if (msg?.type === 'QZ_RESULT') {
    window.postMessage(
      msg.error
        ? { source: 'quartz-extension', id: msg.id, error: msg.error }
        : { source: 'quartz-extension', id: msg.id, result: msg.result },
      window.location.origin);
  }
});
