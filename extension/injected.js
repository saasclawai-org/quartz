// injected.js — runs in the page's MAIN world at document_start.
// Defines window.quartz (EIP-1193-flavoured). The private key NEVER enters
// this world: everything tunnels via postMessage to the content script,
// which talks to the extension background. Signing needs an approval click.

(() => {
  if (window.quartz) return;

  let nextId = 1;
  const pending = new Map();

  function send(method, params) {
    const id = nextId++;
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      window.postMessage({ source: 'quartz-inpage', id, method, params }, window.location.origin);
      setTimeout(() => {
        if (pending.has(id)) {
          pending.delete(id);
          reject(new Error('Quartz: request timed out (extension locked or closed?)'));
        }
      }, 120000);
    });
  }

  window.quartz = {
    isQuartz: true,
    /** quartz.request({method, params})
     *  methods: quartz_accounts → [{address}]
     *           quartz_signMessage {message} → {address, public_key, signature, message}
     */
    request: ({ method, params }) => {
      if (method === 'quartz_accounts') return send('accounts', {});
      if (method === 'quartz_signMessage') {
        if (!params || typeof params.message !== 'string') {
          return Promise.reject(new Error('Quartz: params.message required'));
        }
        return send('signMessage', params);
      }
      return Promise.reject(new Error(`Quartz: unknown method ${method}`));
    },
    // convenience sugar
    login: (challenge) => send('signMessage', { message: challenge }),
    getAddress: async () => (await send('accounts', {}))[0]?.address,
  };

  window.addEventListener('message', (ev) => {
    if (ev.source !== window) return;
    const d = ev.data;
    if (!d || d.source !== 'quartz-extension') return;
    const p = pending.get(d.id);
    if (!p) return;
    pending.delete(d.id);
    if (d.error) p.reject(new Error(d.error));
    else p.resolve(d.result);
  });

  window.dispatchEvent(new Event('quartz#initialized'));
})();
