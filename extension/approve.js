// extracted from approve.html — MV3 extension CSP forbids inline scripts
const $ = id => document.getElementById(id);
let locked = false;

// keepalive port so the MV3 service worker survives while this window is open
const port = chrome.runtime.connect({ name: 'approve-keepalive' });

function finish(icon, text) {
  $('body').style.display = 'none';
  $('done').style.display = 'block';
  $('doneicon').textContent = icon;
  $('donetext').textContent = text;
}

function render(p, unlocked, address) {
  if (!p) { finish('✅', 'No pending request'); return; }
  $('origin').textContent = p.origin;
  $('addr').textContent = address || '—';
  locked = !unlocked;
  if (p.kind === 'connect') {
    $('connectnote').style.display = 'block';
  } else {
    $('msgcard').style.display = 'block';
    $('msg').textContent = p.message || '';
  }
  if (locked) $('pin').style.display = 'block';
}

chrome.runtime.sendMessage({ type: 'QZ_GET_PENDING' }, (s) => { render(s.pending, s.unlocked, s.address); });

$('approve').onclick = async () => {
  $('err').textContent = '';
  if (locked && $('pin').value) {
    const r = await chrome.runtime.sendMessage({ type: 'QZ_UNLOCK', pin: $('pin').value });
    if (r?.error) { $('err').textContent = r.error; return; }
  }
  const r2 = await chrome.runtime.sendMessage({ type: 'QZ_DECIDE', approve: true });
  if (r2?.error) { $('err').textContent = r2.error; return; }
  finish('✍️', r2.signed ? 'Signed and delivered to the site.' : r2.connected ? 'Connected.' : 'Done.');
};

$('reject').onclick = async () => {
  await chrome.runtime.sendMessage({ type: 'QZ_DECIDE', approve: false });
  finish('🚫', 'Rejected.');
};