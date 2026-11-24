// Тестовая ссылка: выбор входа (директор, менеджер, клиент).
(async () => {
  const key = decodeURIComponent(location.pathname.split('/').pop() || '');
  const err = document.getElementById('err');
  const fill = (sel, items, label) => {
    const el = document.getElementById(sel);
    el.innerHTML = '';
    if (!items.length) { el.add(new Option('Список пуст', '')); return; }
    items.forEach(i => el.add(new Option(label(i), i.ext || i)));
  };
  try {
    const r = await fetch('/api/test/options?key=' + encodeURIComponent(key), {credentials: 'same-origin'});
    if (!r.ok) throw new Error('Ссылка не действует.');
    const o = await r.json();
    fill('ext', o.managers, m => `${m.name} · доб. ${m.ext}`);
    fill('client', o.clients, c => c);
  } catch (e) { err.textContent = e.message; }
  document.querySelectorAll('[data-role]').forEach(b => b.addEventListener('click', async () => {
    const role = b.dataset.role;
    const body = {key, role, ext: document.getElementById('ext').value, client: document.getElementById('client').value};
    b.disabled = true;
    try {
      const r = await fetch('/api/test/login', {method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', 'X-CRM': '1'}, body: JSON.stringify(body)});
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error || 'Не получилось войти.');
      location.href = d.go;
    } catch (e) { err.textContent = e.message; b.disabled = false; }
  }));
})();
