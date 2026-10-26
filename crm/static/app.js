// Averon CRM: ядро страницы. Без сборки и внешних библиотек: помощники, окно, меню, переходы и общие детали
// (переключатели, цифры, график, выдвижная панель). Страницы лежат в соседних файлах и пишут себя в VIEWS.
'use strict';

const S = {me: null, page: null, arg: null, f: {period: '7', ext: ''}, shown: {}, busy: false, dial: null, today: null};
const VIEWS = {};
const root = document.getElementById('root');
const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;

// ---------- helpers ----------
const ESC = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'};
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ESC[c]);
const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];

// Политика безопасности страницы не пускает атрибут style, поэтому шаблоны пишут data-css,
// а здесь он переносится в стиль элемента (так браузер разрешает).
const applyCss = el => { el.style.cssText = el.dataset.css + ';' + el.style.cssText; el.removeAttribute('data-css'); };
new MutationObserver(list => list.forEach(m => m.addedNodes.forEach(n => {
  if (n.nodeType !== 1) return;
  if (n.dataset?.css != null) applyCss(n);
  n.querySelectorAll('[data-css]').forEach(applyCss);
}))).observe(document.documentElement, {childList: true, subtree: true});

function phone(n) {
  const d = String(n || '').replace(/\D/g, '');
  if (d.length !== 11) return n || '';
  return `+7 ${d.slice(1, 4)} ${d.slice(4, 7)}-${d.slice(7, 9)}-${d.slice(9)}`;
}
const fmt = n => Math.round(n || 0).toLocaleString('ru-RU');
const rub = v => fmt(v) + ' ₽';
const pctTxt = v => (Math.round(v * 10) / 10).toFixed(v < 10 && v % 1 ? 1 : 0).replace('.', ',') + '%';
const pctOf = (a, b) => b ? pctTxt(a / b * 100) : '—';
function dur(sec) { sec = sec || 0; return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`; }
function hm(sec) { const m = Math.round((sec || 0) / 60); return m >= 60 ? `${Math.floor(m / 60)} ч ${String(m % 60).padStart(2, '0')} мин` : `${m} мин`; }
function isoToday() {
  try { return S.today || new Date().toLocaleDateString('sv-SE', {timeZone: 'Europe/Moscow'}); } catch (e) { return new Date().toISOString().slice(0, 10); }
}
function addDays(iso, n) {
  const d = new Date(iso + 'T12:00:00Z'); d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}
function nextWorkday(iso, n = 1) {
  let d = iso; let left = n;
  while (left > 0) { d = addDays(d, 1); const w = new Date(d + 'T12:00:00Z').getUTCDay(); if (w !== 0 && w !== 6) left--; }
  return d;
}
function ddmm(iso) { if (!iso) return ''; const [, m, d] = iso.slice(0, 10).split('-'); return `${d}.${m}`; }
const WD = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб'];
const MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
function dayLong(iso) { const d = new Date(iso + 'T12:00:00Z'); return `${WD[d.getUTCDay()]}, ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`; }
function when(at) { // '2026-10-17T14:05' -> 'сегодня 14:05' / 'вчера 14:05' / '17.10 14:05'
  if (!at) return '';
  const today = isoToday(), day = at.slice(0, 10), t = at.slice(11, 16);
  if (day === today) return `сегодня ${t}`;
  if (day === addDays(today, -1)) return `вчера ${t}`;
  return `${ddmm(day)} ${t}`;
}
const fmtWhen = v => v ? new Date(v).toLocaleString('ru-RU', {day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Moscow'}) : '—';
function dueText(item) {
  const today = isoToday();
  let d = item.due_date === today ? 'сегодня' : item.due_date === addDays(today, 1) ? 'завтра' : ddmm(item.due_date);
  if (item.due_time) d += ' ' + item.due_time;
  if (item.estimate) d += ' (примерно)';
  return d;
}
function plural(n, one, few, many) {
  const m10 = Math.abs(n) % 10, m100 = Math.abs(n) % 100;
  return m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20) ? few : many;
}
function heights(el) { $$('[data-h]', el).forEach(x => { x.style.height = x.dataset.h + '%'; }); }
const initials = name => String(name || '?').trim().split(/\s+/).map(w => w[0]).join('').slice(0, 2).toUpperCase() || '?';
const store = {
  get(k, d) { try { return JSON.parse(localStorage.getItem(k) || 'null') ?? d; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* без хранилища */ } },
};

// ---------- сервер ----------
async function api(path, opts = {}) {
  const init = {method: opts.method || 'GET', credentials: 'same-origin', headers: {}};
  if (opts.body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(opts.body);
  }
  if (init.method !== 'GET') init.headers['X-CRM'] = '1';
  let r;
  try { r = await fetch(path, init); } catch (e) { throw new Error('Нет связи с сервером. Проверьте интернет.'); }
  const data = await r.json().catch(() => ({}));
  if (r.status === 401) { showLogin(data.error); throw new Error(data.error || 'Нужно войти.'); }
  if (!r.ok) throw new Error(data.error || 'Ошибка сервера.');
  return data;
}
const qs = obj => { const p = new URLSearchParams(); Object.entries(obj).forEach(([k, v]) => { if (v !== '' && v != null) p.set(k, v); }); const s = p.toString(); return s ? '?' + s : ''; };

// ---------- иконки ----------
const ICON = {
  home: '<path d="M4 11.5 12 5l8 6.5V19a1 1 0 0 1-1 1h-4.5v-5h-5v5H5a1 1 0 0 1-1-1z"/>',
  phone: '<path d="M6.5 4h3l1.5 4-2 1.3a10 10 0 0 0 5.7 5.7l1.3-2 4 1.5v3a2 2 0 0 1-2 2A15.5 15.5 0 0 1 4.5 6a2 2 0 0 1 2-2z"/>',
  clock: '<circle cx="12" cy="12" r="8"/><path d="M12 8v4.5l3 2"/>',
  deal: '<rect x="4" y="7" width="16" height="12" rx="2"/><path d="M9 7V5.5A1.5 1.5 0 0 1 10.5 4h3A1.5 1.5 0 0 1 15 5.5V7M4 12h16"/>',
  team: '<circle cx="9" cy="9" r="3.2"/><path d="M3.5 19a5.5 5.5 0 0 1 11 0"/><circle cx="17" cy="9.5" r="2.5"/><path d="M16 14.2a4.5 4.5 0 0 1 4.5 4.8"/>',
  head: '<path d="M5 14v-2a7 7 0 0 1 14 0v2"/><rect x="3.5" y="13" width="4" height="6" rx="1.5"/><rect x="16.5" y="13" width="4" height="6" rx="1.5"/><path d="M18.5 19c0 1.2-1.5 2-4 2"/>',
  bot: '<rect x="5" y="8" width="14" height="11" rx="3"/><path d="M12 4v4M9.5 13h.01M14.5 13h.01M9.5 16.5h5"/>',
  dots: '<path d="M6 12h.01M12 12h.01M18 12h.01"/>',
  chev: '<path d="m9 6 6 6-6 6"/>',
  back: '<path d="m15 6-6 6 6 6"/>',
  queue: '<path d="M4 6h16M4 12h10M4 18h7"/><path d="m16 15 4 3-4 3"/>',
  person: '<circle cx="12" cy="8" r="3.5"/><path d="M5 20a7 7 0 0 1 14 0"/>',
  source: '<circle cx="11" cy="11" r="6"/><path d="m20 20-4.5-4.5M11 8v6M8 11h6"/>',
  report: '<path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5M9 13h7M9 17h5"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M5.6 18.4l1.8-1.8M16.6 7.4l1.8-1.8"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4-4"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  in: '<path d="M17 7 7 17M7 9v8h8"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6 7 7M17 17l1.4 1.4M5.6 18.4 7 17M17 7l1.4-1.4"/>',
  building: '<path d="M5 20V5a1 1 0 0 1 1-1h8a1 1 0 0 1 1 1v15M15 9h3a1 1 0 0 1 1 1v10M3 20h18M8.5 8h3M8.5 12h3M8.5 16h3"/>',
  out: '<path d="M14 4h5v16h-5M10 8l-4 4 4 4M6 12h10"/>',
  flow: '<rect x="3.5" y="4" width="7" height="5" rx="1.5"/><rect x="13.5" y="15" width="7" height="5" rx="1.5"/><path d="M7 9v3.5a2 2 0 0 0 2 2h4.5"/>',
};
const svg = (k, cls = 'ico') => `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICON[k] || ''}</svg>`;
const ARROW = {up: '<svg viewBox="0 0 12 12"><path d="M6 2.5 10 8H2z"/></svg>', down: '<svg viewBox="0 0 12 12"><path d="M6 9.5 2 4h8z"/></svg>'};
const TICK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path class="tick" d="M5 12.5l4.5 4.5L19 7.5"/></svg>';

// ---------- маленькие детали ----------
let toastTimer = null;
function toast(text, err = false) {
  $('.toast')?.remove();
  const el = document.createElement('div');
  el.className = 'toast' + (err ? ' err' : '');
  el.setAttribute('role', 'status');
  el.innerHTML = (err ? '' : TICK) + `<span>${esc(text)}</span>`;
  document.body.appendChild(el);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.remove(), err ? 6000 : 3200);
}
const help = text => `<button type="button" class="help" data-help="${esc(text)}" aria-label="Пояснение">?</button>`;
document.addEventListener('click', e => {
  const b = e.target.closest('.help');
  const open = $('.pop');
  if (open && (!b || open.dataset.for !== b.dataset.help)) open.remove();
  if (!b || (open && open.dataset.for === b.dataset.help)) return;
  e.stopPropagation();
  const pop = document.createElement('div');
  pop.className = 'pop'; pop.dataset.for = b.dataset.help; pop.textContent = b.dataset.help;
  document.body.appendChild(pop);
  const r = b.getBoundingClientRect();
  pop.style.left = Math.max(8, Math.min(innerWidth - pop.offsetWidth - 8, r.left + scrollX - 10)) + 'px';
  pop.style.top = (r.bottom + scrollY + 8) + 'px';
}, true);
const emptyCard = (text, i = 0) => `<div class="card empty blk" data-css="--i:${i}">${text}</div>`;
const errCard = msg => `<div class="page"><div class="card empty blk">${esc(msg)}</div></div>`;
function moreBlock(title, hint, inner, i = 0, open = false) {
  return `<details class="more blk" data-css="--i:${i}"${open ? ' open' : ''}><summary>${svg('chev')}<span>${esc(title)}${hint ? ` <span class="hint">${esc(hint)}</span>` : ''}</span></summary><div class="more-in">${inner}</div></details>`;
}
// Кнопка превращается в «готово» с галочкой.
function doneBtn(btn, text) { btn.classList.add('done'); btn.innerHTML = TICK + esc(text); btn.disabled = true; }
function busy(btn, on) { if (!btn) return; btn.disabled = on; btn.classList.toggle('busy', on); }
const ava = name => `<span class="ava">${esc(initials(name))}</span>`;
function plainItems(n, one, few, many) { return `${fmt(n)} ${plural(n, one, few, many)}`; }

// ---------- переключатель с бегунком ----------
function seg(name, opts, val, label = '') {
  return `<div class="seg" role="group" aria-label="${esc(label || name)}" data-seg="${esc(name)}"><span class="thumb"></span>${opts.map(([v, l, c]) =>
    `<button type="button" data-v="${esc(v)}" aria-pressed="${String(v) === String(val)}">${esc(l)}${c != null && c !== '' ? `<span class="c">${esc(c)}</span>` : ''}</button>`).join('')}</div>`;
}
function moveThumb(s, instant) {
  const b = s.querySelector('[aria-pressed="true"]'), t = s.querySelector('.thumb');
  if (!t) return;
  if (!b) { t.style.width = '0'; return; }
  if (instant) t.style.transition = 'none';
  t.style.width = b.offsetWidth + 'px'; t.style.transform = `translateX(${b.offsetLeft}px)`;
  if (instant) { void t.offsetWidth; t.style.transition = ''; }
}
// handlers: {имя: value => ...}; бегунок едет сразу, страница перерисовывает данные сама.
function bindSeg(scope, handlers) {
  $$('.seg', scope).forEach(s => {
    moveThumb(s, true);
    s.addEventListener('click', e => {
      const b = e.target.closest('button'); if (!b || b.getAttribute('aria-pressed') === 'true') return;
      $$('button', s).forEach(x => x.setAttribute('aria-pressed', String(x === b)));
      moveThumb(s);
      handlers[s.dataset.seg]?.(b.dataset.v);
    });
  });
}
addEventListener('resize', () => $$('.seg').forEach(s => moveThumb(s, true)));
const tabsHtml = (items, cur) => `<div class="tabs" role="tablist">${items.map(([k, t, c]) =>
  `<button type="button" role="tab" data-tab="${esc(k)}" aria-selected="${k === cur}">${esc(t)}${c ? ` <span class="count">${esc(c)}</span>` : ''}</button>`).join('')}</div>`;

// ---------- цифры: плитки, стрелки, плавный счёт ----------
function fmtKind(v, kind) {
  if (kind === 'pct') return pctTxt(v).replace('%', '<small>%</small>');
  if (kind === 'rub') return fmt(v) + '<small> ₽</small>';
  if (kind === 'time') { const m = Math.round(v / 60); return m >= 60 ? `${Math.floor(m / 60)}<small> ч </small>${String(m % 60).padStart(2, '0')}<small> мин</small>` : `${m}<small> мин</small>`; }
  return fmt(v);
}
// pp: сравниваем доли в процентных пунктах. Без прошлого периода стрелки нет.
function delta(cur, prev, pp = false) {
  if (!prev && !(pp && cur)) return '';
  let d, txt;
  if (pp) { d = (cur - prev) * 100; txt = Math.abs(d).toFixed(1).replace('.', ',') + ' п.п.'; }
  else { d = (cur - prev) / prev * 100; txt = Math.abs(Math.round(d)) + '%'; }
  const cls = Math.abs(d) < (pp ? .3 : 1) ? 'flat' : d > 0 ? 'up' : 'down';
  return `<span class="delta ${cls}">${cls === 'flat' ? '≈ ' : ARROW[cls]}${txt}</span>`;
}
function kpiHtml(k, i) {
  const start = S.shown[k.key] ?? (reduce ? k.value : 0);
  return `<div class="kpi blk" data-css="--i:${i}"><span class="lab">${esc(k.label)}${k.help ? ' ' + help(k.help) : ''}</span>
    <span class="val" data-k="${esc(k.key)}" data-to="${k.value}" data-kind="${k.kind || 'n'}">${fmtKind(start, k.kind)}</span>
    ${k.delta || k.vs ? `<span class="foot">${k.delta || ''}${k.vs ? `<span>${esc(k.vs)}</span>` : ''}</span>` : ''}
    ${k.sub ? `<span class="foot">${k.sub}</span>` : ''}</div>`;
}
function countUp(scope) {
  $$('.val[data-to]', scope).forEach(el => {
    const k = el.dataset.k, to = +el.dataset.to, kind = el.dataset.kind;
    const from = S.shown[k] ?? 0;
    S.shown[k] = to;
    const show = v => { el.innerHTML = fmtKind(v, kind); };
    if (reduce || from === to) { show(to); return; }
    const t0 = performance.now(), len = 650;
    (function step(t) { const p = Math.min(1, (t - t0) / len), e = 1 - Math.pow(1 - p, 3); show(from + (to - from) * e); if (p < 1) requestAnimationFrame(step); })(t0);
  });
}
function skeleton(view, kind = 'page') {
  const body = kind === 'table' ? '<div class="sk" data-css="height:56px"></div><div class="sk" data-css="height:420px"></div>'
    : kind === 'cards' ? '<div class="sk" data-css="height:180px"></div><div class="sk" data-css="height:180px"></div>'
      : '<div class="sk-kpis">' + '<div class="sk"></div>'.repeat(4) + '</div><div class="sk" data-css="height:300px"></div><div class="sk" data-css="height:220px"></div>';
  view.innerHTML = `<div class="page" aria-busy="true">${body}</div>`;
}
// Скелет показываем, только если сервер думает дольше 150 мс: иначе страница мигает.
async function loading(view, promise, kind) {
  const t = setTimeout(() => skeleton(view, kind), 150);
  try { return await promise; } finally { clearTimeout(t); }
}

// ---------- график: полосы на одной оси времени ----------
// bands: [{key, label, color, cls:'b1'|'b2'}]; data: [{x, label, future, ...значения}]; tipRows(d) — строки подсказки.
function niceMax(v) { v = Math.max(v, 2); const p = Math.pow(10, Math.floor(Math.log10(v))); for (const f of [1, 2, 4, 6, 8, 10]) { const m = f * p; if (m >= v && (m / 2) % 1 === 0) return m; } return 10 * p; }
function drawChart(box, data, {bands, tipRows, aria}) {
  if (!box) return;
  const n = data.length;
  if (!n) { box.innerHTML = '<div class="empty">Пока нет данных.</div>'; return; }
  const gap = n > 20 ? 2 : n > 10 ? 4 : 8;
  const cols = `repeat(${n},minmax(0,1fr))`;
  const every = n > 20 ? 5 : n > 12 ? 2 : 1;
  const maxes = bands.map(b => niceMax(Math.max(...data.map(d => d[b.key] || 0))));
  const ticks = max => `<span data-css="top:0">${fmt(max)}</span><span data-css="top:50%">${fmt(max / 2)}</span>`;
  box.style.setProperty('--gap', gap + 'px');
  box.innerHTML = bands.map((b, k) => `<span></span><div class="ch-cap"${k ? ' data-css="margin-top:8px"' : ''}><i data-css="background:${b.color}"></i>${esc(b.label)}</div>
    <div class="ch-ticks">${ticks(maxes[k])}</div><div class="ch-band ${b.cls || (k ? 'b2' : 'b1')}" data-css="grid-template-columns:${cols}">${data.map((d, j) => d.future
      ? `<span class="bar future" data-css="--j:${j}"></span>`
      : `<span class="bar" data-css="--v:${(d[b.key] || 0) / maxes[k]};--j:${j};--minh:${d[b.key] > 0 ? 2 : 0}px;background:${b.color}"></span>`).join('')}</div>`).join('') +
    `<span></span><div class="ch-x" data-css="grid-template-columns:${cols}">${data.map((d, j) => `<span>${(n - 1 - j) % every === 0 ? esc(d.x) : ''}</span>`).join('')}</div>
    <div class="ch-hl"></div><div class="tip" role="status"></div>`;
  box.setAttribute('role', 'img');
  if (aria) box.setAttribute('aria-label', aria);
  const band = box.querySelector('.ch-band'), hl = box.querySelector('.ch-hl'), tip = box.querySelector('.tip');
  const at = x => { const r = band.getBoundingClientRect(); x -= r.left; return x < 0 || x > r.width ? -1 : Math.min(n - 1, Math.floor(x / (r.width / n))); };
  const show = j => {
    if (j < 0 || data[j].future) { box.classList.remove('hov'); return; }
    const d = data[j], br = band.getBoundingClientRect(), cr = box.getBoundingClientRect();
    const w = br.width / n, left = br.left - cr.left + j * w;
    hl.style.left = (left - 1) + 'px'; hl.style.width = (w + 2) + 'px'; hl.style.top = (band.offsetTop - 6) + 'px';
    tip.innerHTML = `<b>${esc(d.label)}</b>` + tipRows(d).map(([l, c, v]) => `<div><span>${c ? `<i data-css="background:${c}"></i>` : ''}${esc(l)}</span><em>${v}</em></div>`).join('');
    const tw = tip.offsetWidth;
    let tl = left + w / 2 + 12; if (tl + tw > cr.width) tl = left + w / 2 - tw - 12; if (tl < 0) tl = 0;
    tip.style.left = tl + 'px'; tip.style.top = (band.offsetTop + 4) + 'px';
    box.classList.add('hov');
  };
  box.onpointermove = e => show(at(e.clientX));
  box.onpointerdown = e => show(at(e.clientX));
  box.onpointerleave = () => box.classList.remove('hov');
}
// Горизонтальные полоски: [[подпись, число, доля 0..1]].
const hbars = (rows, color) => rows.length ? `<div class="hb">${rows.map(([t, v, share], k) =>
  `<span>${t}</span><em>${v}</em><span class="bar-h"><i data-css="--w:${Math.max(2, share * 100)}%;--k:${k}${color ? ';--c:' + color : ''}"></i></span>`).join('')}</div>` : '';

// Tab и Shift+Tab ходят по кругу внутри окна, а не уходят на страницу под ним.
const FOCUSABLE = 'a[href],summary,button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),audio[controls],[tabindex]:not([tabindex="-1"])';
function trapTab(box, e) {
  if (e.key !== 'Tab') return;
  // checkVisibility отсекает и кнопки внутри свёрнутого <details>, у них offsetParent не пустой
  const shown = el => (el.checkVisibility ? el.checkVisibility() : el.offsetParent !== null);
  const items = $$(FOCUSABLE, box).filter(el => shown(el) || el === document.activeElement);
  if (!items.length) return;
  const first = items[0], last = items[items.length - 1];
  if (!box.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
  else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
}

// ---------- выдвижная панель ----------
let lastFocus = null;
function openDrawer({title = '', sub = '', avatar = '', html = '', wide = false}) {
  lastFocus = document.activeElement;
  const dr = $('#drawer'), scrim = $('#scrim');
  dr.className = 'drawer' + (wide ? ' wide' : '');
  dr.innerHTML = `<div class="dr-h">${avatar ? ava(avatar) : ''}<div data-css="min-width:0"><h2 id="dr-title">${esc(title)}</h2>${sub ? `<span class="hint">${sub}</span>` : ''}</div><span class="sp"></span>
      <button class="x" type="button" id="dr-x" aria-label="Закрыть">${svg('x', '')}</button></div><div class="dr-b">${html}</div>`;
  $$('.dr-b > *', dr).forEach((el, i) => el.style.setProperty('--i', i));
  requestAnimationFrame(() => { dr.classList.add('open'); scrim.classList.add('open'); setTimeout(() => $('#dr-x')?.focus({preventScroll: true}), 60); });
  $('#dr-x').onclick = closeDrawer;
  return $('.dr-b', dr);
}
function closeDrawer() {
  const dr = $('#drawer'); if (!dr || !dr.classList.contains('open')) return;
  dr.classList.remove('open'); $('#scrim').classList.remove('open');
  $$('audio', dr).forEach(a => a.pause());
  if (lastFocus && document.contains(lastFocus)) lastFocus.focus({preventScroll: true});
}
const drawerOpen = () => $('#drawer')?.classList.contains('open');

// Окно по центру (на телефоне снизу). Закрывается крестиком .rvm-x, фоном и Escape.
function modal(html, wide = false) {
  const veil = document.createElement('div'); veil.className = 'rvm-veil';
  const box = document.createElement('div'); box.className = 'rvm' + (wide ? ' rvm-wide' : ''); box.setAttribute('role', 'dialog'); box.setAttribute('aria-modal', 'true');
  box.innerHTML = html;
  const opener = document.activeElement;
  const title = $('h2', box);
  if (title) { title.id = title.id || 'rvm-t' + Date.now(); box.setAttribute('aria-labelledby', title.id); }
  document.body.append(veil, box);
  setTimeout(() => { if (!box.contains(document.activeElement)) ($('.rvm-x', box) || $(FOCUSABLE, box))?.focus({preventScroll: true}); }, 0);
  const close = () => {
    $$('audio', box).forEach(a => a.pause()); veil.remove(); box.remove(); document.removeEventListener('keydown', onKey, true);
    if (opener && document.contains(opener)) opener.focus({preventScroll: true}); // фокус туда, откуда окно открыли
  };
  const onKey = e => {
    if ($$('.rvm').pop() !== box) return; // закрывается и держит фокус только верхнее окно
    if (e.key === 'Escape') { e.stopImmediatePropagation(); close(); } else trapTab(box, e);
  };
  document.addEventListener('keydown', onKey, true);
  veil.addEventListener('click', close);
  box.addEventListener('click', e => { if (e.target.closest('.rvm-x')) close(); });
  box.close = close;
  return box;
}
const modalHead = (title, sub = '', chip = '') => `<header class="rvm-head"><div>${chip}<h2>${esc(title)}</h2>${sub ? `<div class="rvm-mut">${sub}</div>` : ''}</div><button class="rvm-x" aria-label="Закрыть">×</button></header>`;

// Подтверждение в своём окне: true, если нажали главную кнопку.
function askConfirm(text, ok = 'Да', danger = false) {
  return new Promise(resolve => {
    let answered = false;
    const box = modal(`${modalHead(text)}<div class="rvm-body"><div class="row"><button class="btn ${danger ? 'danger' : 'primary'}" type="button" data-yes>${esc(ok)}</button>
      <button class="btn" type="button" data-no>Отмена</button></div></div>`);
    const done = v => { if (answered) return; answered = true; box.close(); resolve(v); };
    $('[data-yes]', box).onclick = () => done(true);
    $('[data-no]', box).onclick = () => done(false);
    new MutationObserver((_, obs) => { if (!box.isConnected) { obs.disconnect(); done(false); } }).observe(document.body, {childList: true});
    $('[data-yes]', box).focus();
  });
}

function linkBox(out, link, note) {
  out.innerHTML = `<div class="linkbox"><input readonly value="${esc(link)}" aria-label="Ссылка"><button class="btn small">Скопировать</button></div>
    ${note ? `<p class="hint">${esc(note)}</p>` : ''}`;
  const btn = $('button', out);
  btn.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(link); doneBtn(btn, 'Скопировано'); } catch (e) { $('input', out).select(); }
  });
}

// ---------- разделы и меню ----------
const PAGES = {
  summary: {name: 'Сводка', icon: 'home', roles: ['director']},
  queue: {name: 'Очередь', icon: 'queue', roles: ['manager', 'director']},
  calls: {name: 'Звонки', icon: 'phone', roles: ['director', 'manager']},
  callbacks: {name: 'Перезвоны', icon: 'clock', roles: ['director', 'manager']},
  deals: {name: 'Сделки', icon: 'deal', roles: ['director', 'manager']},
  staff: {name: 'Сотрудники', icon: 'team', roles: ['director']},
  me: {name: 'Моя карточка', icon: 'person', roles: ['manager']},
  callcenter: {name: 'Колл-центр', icon: 'head', roles: ['director', 'manager']},
  robot: {name: 'Робот', icon: 'bot', roles: ['director', 'client']},
  sources: {name: 'Источники', icon: 'source', roles: ['director', 'manager']},
  reports: {name: 'Отчёты', icon: 'report', roles: ['director', 'manager']},
  auto: {name: 'Автоматизации', icon: 'flow', roles: ['director']},
  settings: {name: 'Настройки', icon: 'gear', roles: ['director']},
  cab: {name: 'Кабинет', icon: 'home', roles: ['client']},
  companies: {name: 'Компании', icon: 'building', roles: ['director']},
};
const MENU = {
  director: {nav: ['summary', 'calls', 'callbacks', 'deals', 'staff', 'callcenter', 'robot'], more: ['sources', 'reports', 'auto', 'settings', 'companies'], tabs: ['summary', 'calls', 'callbacks', 'deals']},
  manager: {nav: ['queue', 'callbacks', 'deals', 'calls', 'me'], more: ['callcenter', 'sources', 'reports'], tabs: ['queue', 'callbacks', 'deals', 'calls']},
  client: {nav: ['cab', 'robot'], more: [], tabs: ['cab', 'robot']},
};
const HOME = {director: 'summary', manager: 'queue', client: 'cab'};
const ROLE_NAME = {manager: 'менеджер', director: 'директор', client: 'клиент'};
// Старые адреса разделов (закладки, ссылки из Telegram и со страницы /director) ведут на новые.
const ALIAS = {
  team: 'summary', a: 'summary', onboarding: 'summary', tasks: 'callbacks', contracts: 'deals/contracts', pipe: 'deals',
  leads: 'sources/base', training: 'staff', mgr: 'staff', ask: 'reports', prompts: 'settings/prompts', access: 'settings/access',
  clients: 'callcenter', olga: 'callcenter', cabint: 'cab', cabcb: 'cab', cabdays: 'cab', more: '',
};
// Модули компании (crm/company.py): без модуля раздела нет в меню, а его адреса сервер закрывает сам.
const hasModule = m => !S.me.modules || S.me.modules.includes(m);
const PAGE_MODULE = {callcenter: 'callcenter', robot: 'robot', sources: 'sources', auto: 'automations'};
const allowed = p => !!PAGES[p] && PAGES[p].roles.includes(S.me.role) && (!PAGE_MODULE[p] || hasModule(PAGE_MODULE[p]))
  && (p !== 'companies' || !!S.me.platform) && (p !== 'callcenter' || S.me.role !== 'manager' || S.me.callcenter_mode === 'db')
  && (p !== 'robot' || S.me.role !== 'client' || S.me.robot);
function resolve(page, arg) {
  page = String(page || '').replace(/^[aw]:/, '');
  if (page === 'stats') page = S.me.role === 'manager' ? 'me' : 'summary';
  if (page in ALIAS) { const [p, a] = ALIAS[page].split('/'); page = p; arg = arg || a || null; }
  if (!allowed(page)) return [HOME[S.me.role], null];
  return [page, arg || null];
}
const menu = () => {
  const m = MENU[S.me.role];
  return {nav: m.nav.filter(allowed), more: m.more.filter(allowed), tabs: m.tabs.filter(allowed)};
};

function showLogin() {
  root.innerHTML = `<form class="login panel" autocomplete="on">
    <div class="logo"><i></i>Averon <b>CRM</b></div>
    <h1>Вход в CRM</h1>
    <label class="field">Логин<input name="username" autocomplete="username" autocapitalize="none" spellcheck="false" required></label>
    <label class="field">Пароль<input name="password" type="password" autocomplete="current-password" required></label>
    <p class="login-err bad-text" hidden></p>
    <button class="btn primary" type="submit">Войти</button>
    <p class="muted">Логин и пароль выдаёт руководитель.</p></form>`;
  const form = $('form', root), err = $('.login-err', form);
  $('[name="username"]', form).focus();
  form.addEventListener('submit', async e => {
    e.preventDefault();
    const btn = $('button', form);
    busy(btn, true); err.hidden = true;
    try {
      await api('/api/login', {method: 'POST', body: {login: form.username.value, password: form.password.value}});
      location.reload();
    } catch (ex) { busy(btn, false); err.textContent = ex.message; err.hidden = false; }
  });
}

let moreOpen = false;
const exitLink = () => S.me.support ? '<a class="ghost" href="/support-exit">Вернуться к себе</a>'
  : S.me.test ? '<a class="ghost" href="/test-exit">Сменить вход</a>' : '<button class="ghost" type="button" data-logout>Выйти</button>';
function shell() {
  const m = menu();
  moreOpen = m.more.includes(S.page);
  root.innerHTML = `<div class="app">
    <aside class="side">
      <div class="logo"><i></i><span>Averon <b>CRM</b></span></div>
      <nav class="nav" id="nav" aria-label="Разделы"></nav>
      ${window.gptButton && hasModule('gpt') ? gptButton() : ''}
      <div class="side-foot"><span>${esc(S.me.name || '')} · ${esc(ROLE_NAME[S.me.role] || '')}${S.me.ext ? ', доб. ' + esc(S.me.ext) : ''}</span>
        <div class="row"><button class="ghost" type="button" data-theme-btn>${themeLabel()}</button>
        ${exitLink()}</div></div>
    </aside>
    <div class="main">
      ${S.me.support ? `<div class="banner"><b>Просмотр компании «${esc(S.me.company || '')}»</b><span>Глазами директора, только смотреть.</span><a href="/support-exit">Вернуться к себе</a></div>`
        : S.me.test ? `<div class="banner"><b>Просмотр · ${esc(ROLE_NAME[S.me.role] || '')}</b><span>Только смотреть: звонки и изменения работают под своим логином.</span><a href="/test-exit">Сменить вход</a></div>` : ''}
      <header class="top"><div class="top-in" id="top"></div></header>
      <main class="content" id="view"></main>
    </div></div>
    <nav class="tabbar" id="tabbar" aria-label="Разделы"></nav>
    <div class="scrim" id="scrim"></div>
    <aside class="drawer" id="drawer" role="dialog" aria-modal="true" aria-labelledby="dr-title"></aside>`;
  $('#scrim').onclick = closeDrawer;
  if (window.gptShell && hasModule('gpt')) { GPT.open = false; GPT.items = null; gptShell(); } // блокировщик рекламы мог не пустить файл помощника: CRM работает и без него
}
function navButton(id) {
  const p = PAGES[id];
  return `<button type="button" data-go="${id}"${S.page === id ? ' aria-current="page"' : ''}>${svg(p.icon)}<span>${esc(p.name)}</span><span class="count" data-count="${id}">${S.counts?.[id] || ''}</span></button>`;
}
function renderNav() {
  const m = menu();
  if (m.nav.length + m.more.length < 2) { $('#nav').innerHTML = ''; $('#tabbar').innerHTML = ''; $('#tabbar').hidden = true; return; }
  $('#nav').innerHTML = m.nav.map(navButton).join('') + (m.more.length ? `<button type="button" class="more" aria-expanded="${moreOpen}">${svg('dots')}<span>Ещё</span>${svg('chev', 'chev')}</button>
    <div class="sub${moreOpen ? ' open' : ''}"><div>${m.more.map(id => `<button type="button" data-go="${id}"${S.page === id ? ' aria-current="page"' : ''}>${esc(PAGES[id].name)}</button>`).join('')}</div></div>` : '');
  const rest = [...m.nav, ...m.more].filter(id => !m.tabs.includes(id));
  const inTabs = m.tabs.includes(S.page);
  $('#tabbar').hidden = false;
  $('#tabbar').innerHTML = m.tabs.map(id => `<button type="button" data-go="${id}"${S.page === id ? ' aria-current="page"' : ''}>${svg(PAGES[id].icon)}${esc(PAGES[id].name)}<span class="count" data-count="${id}">${S.counts?.[id] || ''}</span></button>`).join('') +
    (rest.length ? `<button type="button" data-sheet${inTabs ? '' : ' aria-current="page"'}>${svg('dots')}Ещё</button>` : '');
}
function moreSheet() {
  const m = menu();
  const rest = [...m.nav, ...m.more].filter(id => !m.tabs.includes(id));
  openDrawer({title: 'Ещё', html: `<div class="sheet-list">${rest.map(id => `<button type="button" data-go="${id}"${S.page === id ? ' aria-current="page"' : ''}>${svg(PAGES[id].icon)}<span>${esc(PAGES[id].name)}</span>${svg('chev')}</button>`).join('')}</div>
    <div class="row"><button class="ghost" type="button" data-theme-btn>${themeLabel()}</button>${exitLink()}</div>
    <p class="hint">${esc(S.me.name || '')} · ${esc(ROLE_NAME[S.me.role] || '')}</p>`});
}
function setCount(page, n) {
  S.counts = S.counts || {};
  S.counts[page] = n || '';
  $$(`[data-count="${page}"]`).forEach(el => { el.textContent = n ? String(n) : ''; });
}

// Шапка страницы: заголовок, «назад», фильтры. Страница вызывает её сама, когда знает, что показать.
function setHead({title = '', sub = '', filters = '', back = null, actions = ''} = {}) {
  const top = $('#top');
  top.innerHTML = `<div class="title-row">${back ? `<button class="back" type="button" aria-label="Назад">${svg('back')}</button>` : ''}
    <h1>${esc(title || PAGES[S.page]?.name || '')}</h1>${sub ? `<span class="sub-t">${sub}</span>` : ''}<span class="sp"></span>${actions}</div>
    <div class="bar-f">${filters}</div>`;
  if (back) $('.back', top).onclick = back;
  return top;
}

// ---------- переходы ----------
let navSeq = 0;
async function go(target, arg = null, opts = {}) {
  const [page, a] = resolve(target, arg);
  const same = page === S.page;
  S.page = page; S.arg = a;
  if (!same) S.shown = {};
  if (opts.push !== false) {
    const hash = '#' + page + (a && !String(a).startsWith('import:') ? '/' + a : '');
    if (location.hash !== hash) history[same || opts.replace ? 'replaceState' : 'pushState'](null, '', hash);
  }
  store.set('crm-page', page);
  if (!same && window.gptPageChanged && hasModule('gpt')) gptPageChanged();
  if (m().more.includes(page)) moreOpen = true;
  renderNav();
  closeDrawer();
  $('.pop')?.remove();
  setHead();
  const old = $('#view');
  const view = document.createElement('main');
  view.className = 'content'; view.id = 'view';
  old.replaceWith(view);
  const seq = ++navSeq;
  if (!same && !opts.keepScroll) scrollTo({top: 0});
  try {
    await VIEWS[page](view, a);
  } catch (e) {
    if (seq === navSeq) view.innerHTML = errCard(e.message);
  }
}
const m = menu;
// Перерисовать текущую страницу без смены адреса и прокрутки.
const reload = () => go(S.page, S.arg, {push: false, keepScroll: true});

document.addEventListener('click', e => {
  const more = e.target.closest('.nav .more');
  if (more) { moreOpen = !moreOpen; more.setAttribute('aria-expanded', moreOpen); more.nextElementSibling.classList.toggle('open', moreOpen); return; }
  const g = e.target.closest('[data-go]');
  if (g) {
    e.preventDefault();
    if ('fext' in g.dataset) { S.f.ext = g.dataset.fext; saveFilters(); }
    go(g.dataset.go, g.dataset.arg || null); return;
  }
  if (e.target.closest('[data-sheet]')) { moreSheet(); return; }
  if (e.target.closest('[data-theme-btn]')) { nextTheme(); return; }
  if (e.target.closest('[data-logout]')) { api('/api/logout', {method: 'POST', body: {}}).catch(() => {}).then(() => showLogin()); }
});
addEventListener('keydown', e => {
  if (e.key === 'Escape' && $('.pop')) { $('.pop').remove(); return; }
  if (!drawerOpen() || $('.rvm')) return;
  if (e.key === 'Escape') closeDrawer(); else trapTab($('#drawer'), e);
});

// ---------- тема ----------
const THEMES = [['', 'Тема: авто'], ['light', 'Тема: светлая'], ['dark', 'Тема: тёмная']];
function applyTheme(v) { if (v) document.documentElement.setAttribute('data-theme', v); else document.documentElement.removeAttribute('data-theme'); }
function themeLabel() { const v = store.get('crm-theme', ''); return (THEMES.find(t => t[0] === v) || THEMES[0])[1]; }
function nextTheme() {
  const i = THEMES.findIndex(t => t[0] === store.get('crm-theme', ''));
  const [v, label] = THEMES[(i + 1) % THEMES.length];
  store.set('crm-theme', v); applyTheme(v);
  $$('[data-theme-btn]').forEach(b => { b.textContent = label; });
}
applyTheme(store.get('crm-theme', ''));

// Цифры на кнопках меню: перезвоны на сегодня и просроченные, у менеджера ещё очередь.
async function refreshCounts() {
  if (S.me.role === 'client') return;
  try {
    const cb = await api('/api/callbacks');
    S.today = cb.today || S.today;
    setCount('callbacks', cb.items.filter(t => t.due_date <= cb.today).length);
  } catch (e) { /* счётчик подождёт */ }
  if (S.me.role === 'manager') {
    try { setCount('queue', (await api('/api/queue')).items.length); } catch (e) { /* счётчик подождёт */ }
  }
}

// ---------- старт ----------
function fromHash() {
  let asked = '';
  try { asked = decodeURIComponent(location.hash.slice(1)); } catch (e) { /* испорченная ссылка */ }
  const cut = asked.indexOf('/');
  return cut < 0 ? [asked, null] : [asked.slice(0, cut), asked.slice(cut + 1)];
}
document.addEventListener('DOMContentLoaded', async () => {
  try {
    S.me = await api('/api/me');
  } catch (e) {
    if (!S.me) showLogin(e.message);
    return;
  }
  const saved = store.get('crm-f', {});
  if (saved.period) S.f.period = saved.period;
  if (S.me.role === 'director' && S.me.team.some(p => p.ext === saved.ext)) S.f.ext = saved.ext;
  const [page, arg] = fromHash();
  S.page = page && resolve(page, arg)[0];
  shell();
  go(page || store.get('crm-page', '') || HOME[S.me.role], arg, {replace: true});
  addEventListener('popstate', () => { const [p, a] = fromHash(); go(p || HOME[S.me.role], a, {push: false}); });
  refreshCounts();
  if (window.gptFirstVisit && hasModule('gpt')) gptFirstVisit();
  // Раз в минуту обновляем счётчики, а очередь менеджера, если он ничего не заполняет.
  setInterval(() => {
    if (document.hidden) return;
    refreshCounts();
    if (S.page === 'queue' && !S.busy && !S.dial && !drawerOpen() && !$('.outform:not(.hidden)') && !$('.rvm')) reload();
  }, 60000);
});
const saveFilters = () => store.set('crm-f', S.f);
