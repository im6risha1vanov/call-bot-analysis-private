// Одно меню директора на обеих его страницах: аналитика (/director) и работа отдела (/?work).
// Пункт «a:...» — раздел аналитики, «w:...» — раздел работы отдела. Раздел своей страницы
// открывается на месте (onOpen), чужой — переходом на другую страницу с #раздел.
(() => {
  const ICON = {
    team: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    mgr: '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/>',
    pipe: '<path d="M3 4h18l-7 8v6l-4 2v-8z"/>',
    calls: '<path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2"/>',
    callbacks: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    report: '<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5M10 13h6M10 17h6"/>',
    olga: '<path d="M4 14v-2a8 8 0 0 1 16 0v2"/><rect x="3" y="14" width="4" height="6" rx="1"/><rect x="17" y="14" width="4" height="6" rx="1"/>',
    leads: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
    client: '<path d="M4 20V9l8-5 8 5v11"/><path d="M9 20v-6h6v6"/>',
    train: '<path d="M3 17l6-6 4 4 8-8"/><path d="M14 7h7v7"/>',
    ask: '<path d="M4 5h16v11H9l-5 4z"/><path d="M10 9a2 2 0 1 1 2 2v1M12 14h.01"/>',
    queue: '<path d="M4 6h16M4 12h10M4 18h7"/><path d="m16 15 3 3-3 3"/>',
    tasks: '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M8 3v4M16 3v4M4 10h16"/>',
    people: '<circle cx="9" cy="8" r="3.5"/><path d="M3 20c0-3.5 2.7-5.5 6-5.5s6 2 6 5.5M17 4v6M14 7h6"/>',
    robot: '<rect x="5" y="8" width="14" height="11" rx="3"/><path d="M12 4v4M9 13h.01M15 13h.01M9.5 16.5h5"/>',
    pen: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13.5 6.5 4 4"/>',
    key: '<circle cx="8" cy="15" r="4"/><path d="m11 12 9-9M17 6l3 3M15 8l2 2"/>',
    source: '<circle cx="11" cy="11" r="6"/><path d="m20 20-4.5-4.5M11 8v6M8 11h6"/>',
    more: '<circle cx="5" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="19" cy="12" r="1.3"/>',
  };
  // bar — пункт нижней полоски на телефоне; more — под «Ещё»; label — подпись группы внутри «Ещё».
  const ITEMS = [
    {id: 'a:team', name: 'Сводка', icon: 'team', bar: true},
    {id: 'w:staff', name: 'Сотрудники', icon: 'people', bar: true},
    {id: 'w:deals', name: 'Сделки', icon: 'pipe'},
    {id: 'a:calls', name: 'Звонки', icon: 'calls', bar: true},
    {id: 'a:callbacks', name: 'Перезвоны', icon: 'callbacks', bar: true},
    {id: 'w:reports', name: 'Отчёты', icon: 'report'},
    {id: 'a:olga', name: 'Колл-центр · Ольга', icon: 'olga', more: true},
    {id: 'a:leads', name: 'База лидов', icon: 'leads', more: true},
    {id: 'w:clients', name: 'Клиенты', icon: 'client', more: true},
    {id: 'w:sources', name: 'Источники', icon: 'source', more: true},
    {id: 'w:training', name: 'Тренировки', icon: 'train', more: true},
    {id: 'w:ask', name: 'Вопрос по отделу', icon: 'ask', more: true},
    {id: 'w:team', name: 'Очереди', icon: 'queue', more: true, label: 'Работа менеджеров'},
    {id: 'w:tasks', name: 'Перезвоны в CRM', icon: 'tasks', more: true},
    {id: 'w:robot', name: 'Робот', icon: 'robot', more: true, label: 'Настройки'},
    {id: 'w:prompts', name: 'Промты', icon: 'pen', more: true},
    {id: 'w:access', name: 'Доступы', icon: 'key', more: true},
  ];
  // Разделы работы отдела, которые открываются из меню не напрямую (очередь менеджера из «Очередей»).
  // «Менеджеры» аналитики объединены с «Сотрудниками»: старая ссылка ведёт в карточки сотрудников.
  const ALIAS = {'w:queue': 'w:team', 'w:leads': 'w:team', 'w:calls': 'w:team', 'w:stats': 'w:team', 'a:mgr': 'w:staff', 'w:me': 'w:staff', 'a:pipe': 'w:deals'};
  const svg = k => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON[k]}</svg>`;
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const store = {
    get() { try { return localStorage.getItem('dm-more') === '1'; } catch (e) { return false; } },
    set(v) { try { localStorage.setItem('dm-more', v ? '1' : '0'); } catch (e) { /* без хранилища */ } },
  };
  let opts = null, active = null;

  const href = id => id.startsWith('a:') ? '/director#' + id.slice(2) : '/?work#' + id.slice(2);
  const item = (p, cls = '') => `<a class="dm-item ${cls}" href="${href(p.id)}" data-dm="${p.id}">${svg(p.icon)}<span>${p.name}</span></a>`;
  const out = () => opts.test ? '<a href="/test-exit">Сменить вход</a>' : '<a href="/logout">Выйти</a>';

  function moreList() {
    return ITEMS.filter(p => p.more).map(p => (p.label ? `<div class="dm-label">${p.label}</div>` : '') + item(p)).join('');
  }

  function mount(o) {
    opts = o;
    const side = document.querySelector(o.side);
    const open = store.get();
    side.insertAdjacentHTML('beforeend', `<nav class="dm-side" aria-label="Разделы">
      ${ITEMS.filter(p => !p.more).map(p => item(p)).join('')}
      <button class="dm-item dm-more" aria-expanded="${open}">${svg('more')}<span>Ещё</span></button>
      <div class="dm-box"${open ? '' : ' hidden'}>${moreList()}</div></nav>
      <div class="dm-foot"><span>${esc(o.name || 'Директор')}${o.test ? ' · просмотр' : ''}</span>${out()}</div>`);
    document.body.insertAdjacentHTML('beforeend', `<nav class="dm-bar" aria-label="Разделы">
      ${ITEMS.filter(p => p.bar).map(p => item(p)).join('')}
      <button class="dm-item dm-sheet-open">${svg('more')}<span>Ещё</span></button></nav>
      <div class="dm-sheet" hidden><div class="dm-sheet-in"><div class="dm-sheet-head"><b>Разделы</b><button class="dm-x" aria-label="Закрыть">×</button></div>
      ${ITEMS.filter(p => !p.bar && !p.more).map(p => item(p)).join('')}${moreList()}<div class="dm-sheet-foot">${out()}</div></div></div>`);
    document.body.classList.add('dm');
    document.addEventListener('click', e => {
      const a = e.target.closest('[data-dm]');
      if (a) {
        const id = a.dataset.dm;
        sheet(false);
        if (id.slice(0, 2) === o.here + ':' && !e.metaKey && !e.ctrlKey) { e.preventDefault(); o.onOpen(id.slice(2)); }
        return;
      }
      if (e.target.closest('.dm-more')) { const box = document.querySelector('.dm-box'); setMore(box.hidden); store.set(!box.hidden); }
      else if (e.target.closest('.dm-sheet-open')) sheet(true);
      else if (e.target.closest('.dm-x') || e.target.classList.contains('dm-sheet')) sheet(false);
    });
    if (active) setActive(active);
  }

  function setMore(show) {
    const box = document.querySelector('.dm-box'), btn = document.querySelector('.dm-more');
    if (!box) return;
    box.hidden = !show;
    btn.setAttribute('aria-expanded', String(show));
  }

  function sheet(show) { const s = document.querySelector('.dm-sheet'); if (s) s.hidden = !show; }

  function setActive(id) {
    active = ALIAS[id] || id;
    if (!opts) return;
    const mark = (el, on) => on ? el.setAttribute('aria-current', 'page') : el.removeAttribute('aria-current');
    document.querySelectorAll('[data-dm]').forEach(a => mark(a, a.dataset.dm === active));
    const p = ITEMS.find(x => x.id === active);
    if (p && p.more) setMore(true);  // открытый раздел из «Ещё» виден в меню
    const bar = document.querySelector('.dm-sheet-open');
    if (bar) mark(bar, !!p && !p.bar);
  }

  window.DirMenu = {mount, setActive};
})();
