// extracted from popup.html — MV3 extension CSP forbids inline scripts
const $ = id => document.getElementById(id);

async function refresh() {
  const s = await chrome.runtime.sendMessage({ type: 'QZ_STATUS' });
  if (!s.initialized) { $('onboard').style.display = 'block'; $('main').style.display = 'none'; return; }
  $('onboard').style.display = 'none';
  $('main').style.display = 'block';
  $('addr').textContent = s.address || '—';
  const b = $('statebadge');
  b.className = 'badge ' + (s.unlocked ? 'ok' : 'lock');
  b.textContent = s.unlocked ? 'unlocked' : 'locked';
  $('unlockpin').style.display = s.unlocked ? 'none' : 'block';
  $('unlockbtn').style.display = s.unlocked ? 'none' : 'block';
  $('lockbtn').style.display = s.unlocked ? 'block' : 'none';
}

$('go').onclick = async () => {
  $('err').textContent = '';
  if ($('pin').value.length < 4) { $('err').textContent = 'PIN too short'; return; }
  if ($('pin').value !== $('pin2').value) { $('err').textContent = 'PINs differ'; return; }
  const type = $('words').value.trim() ? 'QZ_ONBOARD' : 'QZ_CREATE';
  const r = await chrome.runtime.sendMessage({ type, words: $('words').value, pin: $('pin').value });
  if (r?.error) { $('err').textContent = r.error; return; }
  if (r.words) {
    $('newwallet').style.display = 'block';
    $('seedwords').textContent = r.words.join(' ');
  }
  $('words').value = ''; $('pin').value = ''; $('pin2').value = '';
  refresh();
};

$('unlockbtn').onclick = async () => {
  const r = await chrome.runtime.sendMessage({ type: 'QZ_UNLOCK', pin: $('unlockpin').value });
  $('unlockpin').value = '';
  if (r?.error) alert(r.error);
  refresh();
};

$('lockbtn').onclick = async () => {
  await chrome.runtime.sendMessage({ type: 'QZ_LOCK' });
  refresh();
};

refresh();