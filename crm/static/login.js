// Тратит одноразовый токен из ссылки бота и открывает CRM.
(async () => {
  const token = decodeURIComponent(location.pathname.split('/').pop() || '');
  history.replaceState(null, '', '/in/');
  const msg = document.getElementById('msg'), sub = document.getElementById('sub');
  try {
    const r = await fetch('/api/login', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CRM': '1'},
                                         body: JSON.stringify({token}), credentials: 'same-origin'});
    if (r.ok) { location.replace('/'); return; }
    const data = await r.json().catch(() => ({}));
    msg.textContent = 'Не получилось войти';
    sub.textContent = data.error || 'Получите новую ссылку в боте командой /crm.';
  } catch (e) {
    msg.textContent = 'Нет связи с сервером';
    sub.textContent = 'Проверьте интернет и откройте ссылку ещё раз.';
  }
})();
