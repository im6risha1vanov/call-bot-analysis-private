// CRM менеджера Averon. Без сборки и внешних библиотек: страница и API на нашем сервере.
'use strict';

const S = {me: null, ext: null, page: 'queue', queue: null, pos: 0, dial: null, busy: false, leadFilter: null, statsDays: 7};
const root = document.getElementById('root');

// ---------- helpers ----------
const ESC = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'};
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ESC[c]);
const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];

function phone(n) {
  const d = String(n || '').replace(/\D/g, '');
  if (d.length !== 11) return n || '';
  return `+7 ${d.slice(1, 4)} ${d.slice(4, 7)}-${d.slice(7, 9)}-${d.slice(9)}`;
}
function dur(sec) { sec = sec || 0; return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`; }
function hm(sec) { const m = Math.round((sec || 0) / 60); return m >= 60 ? `${Math.floor(m / 60)} ч ${m % 60} мин` : `${m} мин`; }
function isoToday() { return S.queue?.today || new Date().toISOString().slice(0, 10); }
function addDays(iso, n) {
  const d = new Date(iso + 'T12:00:00'); d.setDate(d.getDate() + n);
  return d.toISOString().slice(0, 10);
}
function nextWorkday(iso, n = 1) {
  let d = iso; let left = n;
  while (left > 0) { d = addDays(d, 1); const w = new Date(d + 'T12:00:00').getDay(); if (w !== 0 && w !== 6) left--; }
  return d;
}
function ddmm(iso) { if (!iso) return ''; const [y, m, d] = iso.slice(0, 10).split('-'); return `${d}.${m}`; }
function when(at) { // '2026-10-17T14:05' -> '17.10 14:05'
  if (!at) return '';
  const today = isoToday();
  const day = at.slice(0, 10);
  const t = at.slice(11, 16);
  if (day === today) return `сегодня ${t}`;
  if (day === addDays(today, -1)) return `вчера ${t}`;
  return `${ddmm(day)} ${t}`;
}
function dueText(item) {
  const today = isoToday();
  let d = item.due_date === today ? 'сегодня' : item.due_date === addDays(today, 1) ? 'завтра' : ddmm(item.due_date);
  if (item.due_time) d += ' ' + item.due_time;
  if (item.estimate) d += ' (примерно)';
  return d;
}
function heights(el) { $$('[data-h]', el).forEach(x => { x.style.height = x.dataset.h + '%'; }); }

let toastTimer = null;
function toast(text, err = false) {
  let el = $('.toast');
  if (!el) { el = document.createElement('div'); document.body.appendChild(el); }
  el.className = 'toast' + (err ? ' err' : '');
  el.textContent = text;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.remove(), err ? 6000 : 3000);
}

async function api(path, opts = {}) {
  const url = new URL(path, location.origin);
  if (S.ext && S.me?.privileged && !url.searchParams.has('ext') && opts.scoped !== false) url.searchParams.set('ext', S.ext);
  const init = {method: opts.method || 'GET', credentials: 'same-origin', headers: {}};
  if (opts.body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify({...opts.body, ...(S.me?.privileged && S.ext ? {ext: S.ext} : {})});
  }
  if (init.method !== 'GET') init.headers['X-CRM'] = '1';
  let r;
  try { r = await fetch(url, init); } catch (e) { throw new Error('Нет связи с сервером. Проверьте интернет.'); }
  const data = await r.json().catch(() => ({}));
  if (r.status === 401) { showLogin(data.error); throw new Error(data.error || 'Нужно войти.'); }
  if (!r.ok) throw new Error(data.error || 'Ошибка сервера.');
  return data;
}

// ---------- icons ----------
const ICON = {
  queue: '<path d="M4 6h16M4 12h10M4 18h7"/><path d="m16 15 4 3-4 3"/>',
  tasks: '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M8 3v4M16 3v4M4 10h16"/>',
  leads: '<path d="M4 5h16v14H4z"/><path d="M8 9h8M8 13h8M8 17h5"/>',
  calls: '<path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2"/>',
  stats: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  team: '<circle cx="9" cy="8" r="3"/><path d="M3 20a6 6 0 0 1 12 0"/><circle cx="17" cy="9" r="2.5"/><path d="M15 14.5a5 5 0 0 1 6 5.5"/>',
  client: '<path d="M4 21V8l8-5 8 5v13"/><path d="M9 21v-6h6v6"/>',
  report: '<path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5M9 13h7M9 17h5"/>',
  train: '<path d="M4 18 10 12l4 4 6-8"/><path d="M15 8h5v5"/>',
  ask: '<path d="M4 5h16v11H9l-5 4z"/><path d="M10 9.5a2 2 0 1 1 2.5 1.9c-.4.2-.5.5-.5.9M12 14h.01"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="m11 12 9-9M17 6l3 3M15 8l2 2"/>',
  people: '<circle cx="12" cy="8" r="3.5"/><path d="M5 20a7 7 0 0 1 14 0"/><path d="M19 4v4M17 6h4"/>',
  robot: '<rect x="5" y="8" width="14" height="11" rx="3"/><path d="M12 4v4M9 13h.01M15 13h.01M9.5 16.5h5"/>',
  pen: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13.5 6.5 4 4"/>',
  deal: '<path d="M3 4h18l-7 8v6l-4 2v-8z"/>',
  doc: '<path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5M9 12h7M9 16h7M9 20h3"/>',
  star: '<path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z"/>',
  source: '<circle cx="11" cy="11" r="6"/><path d="m20 20-4.5-4.5M11 8v6M8 11h6"/>',
};
const svg = k => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON[k]}</svg>`;

const STAFF = ['manager', 'director'];
const PAGES = [
  {id: 'team', name: 'Команда', icon: 'team', roles: ['director']},
  {id: 'queue', name: 'Очередь', icon: 'queue', roles: STAFF},
  {id: 'tasks', name: 'Перезвоны', icon: 'tasks', roles: STAFF},
  {id: 'leads', name: 'База', icon: 'leads', roles: STAFF},
  {id: 'sources', name: 'Источники', icon: 'source', roles: STAFF},
  {id: 'deals', name: 'Сделки', icon: 'deal', roles: STAFF},
  {id: 'contracts', name: 'Договоры', icon: 'doc', roles: STAFF},
  {id: 'calls', name: 'Звонки', icon: 'calls', roles: STAFF},
  {id: 'stats', name: 'Итоги', icon: 'stats', roles: STAFF},
  {id: 'reports', name: 'Отчёты', icon: 'report', roles: STAFF},
  {id: 'training', name: 'Тренировки', icon: 'train', roles: STAFF},
  {id: 'ask', name: 'Вопрос по отделу', icon: 'ask', roles: STAFF},
  {id: 'clients', name: 'Клиенты', icon: 'client', roles: ['director']},
  {id: 'staff', name: 'Сотрудники', icon: 'people', roles: ['director']},
  {id: 'me', name: 'Моя карточка', icon: 'people', roles: ['manager']},
  {id: 'robot', name: 'Робот', icon: 'robot', roles: ['director', 'client']},
  {id: 'prompts', name: 'Промты', icon: 'pen', roles: ['director']},
  {id: 'access', name: 'Доступы', icon: 'key', roles: ['director']},
  {id: 'cab', name: 'Сводка', icon: 'stats', roles: ['client']},
  {id: 'cabint', name: 'Заинтересованные', icon: 'star', roles: ['client']},
  {id: 'cabcb', name: 'Перезвоны', icon: 'tasks', roles: ['client']},
  {id: 'cabdays', name: 'По дням', icon: 'calls', roles: ['client']},
];
const HOME = {manager: 'queue', director: 'team', client: 'cab'};
const ROLE_NAME = {manager: 'Менеджер', director: 'Директор', client: 'Клиент'};
const allowed = p => p.roles.includes(S.me.role);
const OUT_CLASS = {meeting: 'o-good', proposal: 'o-good', callback: 'o-mid', interested: 'o-mid',
                   no_answer: '', not_interested: 'o-bad', bad_number: 'o-bad'};
const OUT_ORDER = ['no_answer', 'callback', 'meeting', 'proposal', 'interested', 'not_interested', 'bad_number'];
const OPTIONAL_DATE = new Set(['proposal', 'interested']);

// ---------- shell ----------
function showLogin(reason) {
  root.innerHTML = `<div class="login panel">
    <div class="logo"><i></i>Averon <b>CRM</b></div>
    <h1>Вход в CRM</h1>
    <p class="muted">${esc(reason || 'Откройте CRM по своей ссылке.')}</p></div>`;
}

function shell() {
  const pages = PAGES.filter(allowed);
  // У директора одно меню на обеих его страницах (static/dirmenu.js).
  const director = S.me.role === 'director' && !!window.DirMenu;
  const people = S.me.privileged ? `<label class="field"><select id="pick-ext" aria-label="Менеджер">${
    S.me.team.map(p => `<option value="${esc(p.ext)}"${p.ext === S.ext ? ' selected' : ''}>${esc(p.name)} · ${esc(p.ext)}</option>`).join('')
  }</select></label>` : '';
  root.innerHTML = `<div class="app">
    <aside class="side">
      <div class="logo"><i></i>Averon <b>CRM</b></div>
      ${director ? '' : `<nav class="nav">${pages.map(p => `<button data-page="${p.id}">${svg(p.icon)}<span>${p.name}</span><span class="count" id="count-${p.id}"></span></button>`).join('')}</nav>
      <div class="side-foot"><span>${esc(S.me.name)}${S.me.ext ? ' · доб. ' + esc(S.me.ext) : ''}</span>
        ${S.me.test ? '<a class="btn small" href="/test-exit">Сменить вход</a>' : '<button id="logout">Выйти</button>'}</div>`}
    </aside>
    <div class="main">
      ${S.me.test ? `<div class="banner"><b>Просмотр · ${esc(ROLE_NAME[S.me.role] || '')}</b><span>Только смотреть: звонки и изменения работают по личной ссылке.</span><a href="/test-exit">Сменить вход</a></div>` : ''}
      <header class="top"><h1 id="title"></h1><span class="sp"></span>${people}</header>
      <main class="content" id="view"></main>
    </div></div>`;
  $$('.nav button').forEach(b => b.addEventListener('click', () => openPage(b.dataset.page)));
  if (director) DirMenu.mount({side: '.side', here: 'w', name: S.me.name, test: S.me.test, onOpen: openPage});
  if ($('#logout')) $('#logout').addEventListener('click', async () => { await api('/api/logout', {method: 'POST', body: {}}).catch(() => {}); showLogin(); });
  const pick = $('#pick-ext');
  if (pick) pick.addEventListener('change', () => { S.ext = pick.value; S.queue = null; S.pos = 0; S.dial = null; save(); go(S.page); });
}

// Переход из меню: раздел открывается с начала (список сотрудников, а не последняя карточка).
function openPage(page) { S.person = null; S.personTab = null; go(page); }

function save() { try { localStorage.setItem('crm', JSON.stringify({page: S.page, ext: S.ext})); } catch (e) { /* без хранилища */ } }
function load() { try { return JSON.parse(localStorage.getItem('crm') || '{}'); } catch (e) { return {}; } }

async function go(page) {
  if (!PAGES.some(p => p.id === page && allowed(p))) page = HOME[S.me.role] || 'queue';
  S.page = page; save();
  $$('.nav button').forEach(b => b.setAttribute('aria-current', b.dataset.page === page ? 'page' : 'false'));
  if (window.DirMenu) DirMenu.setActive('w:' + page);
  $('#title').textContent = PAGES.find(p => p.id === page).name;
  const view = $('#view');
  view.innerHTML = '<div class="empty">Загружаю…</div>';
  try {
    await VIEWS[page](view);
  } catch (e) {
    view.innerHTML = `<div class="panel empty">${esc(e.message)}</div>`;
  }
}

// ---------- work card: звонок и итог ----------
// item: {kind, id, number, title, note, label, ...}; onDone вызывается после сохранения итога.
function workCard(el, item, onDone, extra = '') {
  const canDial = S.me.can_dial;
  const chip = item.kind === 'task'
    ? `<span class="chip ${item.late ? 'late' : item.due_date === isoToday() ? 'today' : ''}">${esc(item.label)} · ${esc(dueText(item))}${item.late ? ' · просрочен' : ''}</span>`
    : item.kind === 'lead'
      ? `<span class="chip ${item.status === 'no_answer' ? 'warn' : 'acc'}">${item.status === 'no_answer' ? `Не дозвонились · попытка ${item.attempts + 1}` : 'Новый номер'}</span>`
      : '';
  el.innerHTML = `<div class="panel work">
    <div class="who">${chip}${item.source ? `<span class="chip">${esc(item.source === 'crm' ? 'из CRM' : item.source === 'review' ? 'из разбора' : item.source)}</span>` : ''}${extra}</div>
    <div class="phone"><a href="tel:+${esc(item.number)}">${esc(phone(item.number))}</a></div>
    ${item.title ? `<div class="title">${esc(item.title)}</div>` : ''}
    ${item.note ? `<div class="note">${esc(item.note)}</div>` : ''}
    <div class="actions">
      <button class="btn primary big" data-act="dial" ${canDial ? '' : 'disabled'}>📞 Позвонить</button>
      <button class="btn" data-act="copy">Скопировать номер</button>
    </div>
    ${canDial ? '' : `<div class="ringing">${S.me.readonly ? 'Тестовый вход: звонок и итог работают во входе самого менеджера.' : 'Звонить можно только со своего добавочного в Mango. Руководитель видит очередь менеджера, но звонит менеджер.'}</div>`}
    <div class="ringing hidden" id="ring"></div>
    <div class="outs${S.me.readonly ? ' hidden' : ''}">
      <h3>Итог звонка</h3>
      <div class="outgrid">${OUT_ORDER.map(k => `<button data-out="${k}" class="${OUT_CLASS[k]}" aria-pressed="false">${esc(S.me.outcomes[k])}</button>`).join('')}</div>
      <div class="outform hidden" id="outform">
        <div class="quick" id="quick"></div>
        <label class="field">Дата<input type="date" id="o-date" min="${isoToday()}"></label>
        <label class="field">Время<input type="time" id="o-time" step="300"></label>
        <label class="field wide">Комментарий<textarea id="o-comment" maxlength="1000" placeholder="Что важно помнить к следующему звонку"></textarea></label>
        <div class="row wide"><button class="btn primary" id="o-save">Сохранить и дальше</button><button class="btn" id="o-cancel">Отмена</button></div>
      </div>
    </div>
  </div>
  <div class="panel"><h2>История клиента</h2><div class="hist" id="hist"><div class="muted">Загружаю…</div></div></div>`;

  let chosen = null;
  const form = $('#outform', el), ring = $('#ring', el);
  $('[data-act="copy"]', el).addEventListener('click', async () => {
    try { await navigator.clipboard.writeText('+' + item.number); toast('Номер скопирован'); } catch (e) { toast(phone(item.number)); }
  });
  $('[data-act="dial"]', el).addEventListener('click', async ev => {
    const b = ev.currentTarget;
    b.disabled = true;
    ring.className = 'ringing'; ring.textContent = 'Отправляю команду в Mango…';
    try {
      const res = await api('/api/dial', {method: 'POST', body: {number: item.number}, scoped: false});
      S.dial = {id: res.dial_id, number: item.number};
      ring.textContent = 'Сейчас зазвонит ваш софтфон Mango. Снимите трубку, и Mango наберёт клиента. После разговора отметьте итог ниже.';
    } catch (e) {
      ring.className = 'ringing err'; ring.textContent = e.message;
    } finally {
      setTimeout(() => { b.disabled = false; }, 8000);
    }
  });
  $$('[data-out]', el).forEach(btn => btn.addEventListener('click', () => {
    chosen = btn.dataset.out;
    $$('[data-out]', el).forEach(x => x.setAttribute('aria-pressed', String(x === btn)));
    const dated = S.me.needs_date.includes(chosen) || OPTIONAL_DATE.has(chosen);
    form.classList.remove('hidden');
    $('#o-date', el).parentElement.classList.toggle('hidden', !dated);
    $('#o-time', el).parentElement.classList.toggle('hidden', !dated);
    const quick = $('#quick', el);
    quick.innerHTML = dated ? [['Завтра', 1], ['Через 2 дня', 2], ['Через неделю', 7]].map(([t, n]) =>
      `<button type="button" data-days="${n}">${t}</button>`).join('') : '';
    $$('[data-days]', quick).forEach(q => q.addEventListener('click', () => { $('#o-date', el).value = addDays(isoToday(), +q.dataset.days); }));
    if (!dated && chosen === 'no_answer') saveOutcome();
    else $('#o-comment', el).focus();
  }));
  $('#o-cancel', el).addEventListener('click', () => {
    chosen = null; form.classList.add('hidden');
    $$('[data-out]', el).forEach(x => x.setAttribute('aria-pressed', 'false'));
  });
  $('#o-save', el).addEventListener('click', saveOutcome);

  async function saveOutcome() {
    if (!chosen || S.busy) return;
    const dueDate = $('#o-date', el).value || null;
    if (S.me.needs_date.includes(chosen) && !dueDate) { toast('Выберите дату', true); return; }
    S.busy = true;
    try {
      await api('/api/outcome', {method: 'POST', body: {
        number: item.number, outcome: chosen, comment: $('#o-comment', el).value,
        due_date: dueDate, due_time: $('#o-time', el).value || null,
        dial_id: S.dial && S.dial.number === item.number ? S.dial.id : null}});
      S.dial = null;
      toast(chosen === 'no_answer' ? 'Не дозвонились: номер вернётся в очередь через 2 часа' : 'Итог сохранён');
      onDone && onDone(chosen);
    } catch (e) {
      toast(e.message, true);
    } finally {
      S.busy = false;
    }
  }
  loadHistory(item.number, $("#hist", el));
}

// Запись звонка из Mango: кнопка превращается в плеер, файл отдаёт наш сервер.
const listenBtn = c => c.review ? `<button class="btn small listen" data-review="${c.id}">Подробный разбор${c.rec ? ' и запись' : ''}</button>`
  : c.rec ? `<button class="btn small listen" data-rec="${c.id}">▶ Прослушать</button>` : '';
document.addEventListener('click', e => {
  const b = e.target.closest('button[data-rec]');
  if (!b) return;
  e.stopPropagation();
  const a = document.createElement('audio');
  a.controls = true; a.preload = 'auto'; a.className = 'rec';
  a.src = '/api/recording/' + encodeURIComponent(b.dataset.rec);
  a.addEventListener('click', ev => ev.stopPropagation());
  a.addEventListener('error', async () => {
    let text = 'Запись не загрузилась.';
    try { const r = await fetch(a.src, {credentials: 'same-origin'}); text = (await r.json()).error || text; } catch (err) { /* остаётся общий текст */ }
    const m = document.createElement('div'); m.className = 'muted'; m.textContent = text; a.replaceWith(m);
  });
  b.replaceWith(a);
  a.play().catch(() => { /* браузер мог запретить автозапуск: плеер остаётся с кнопкой */ });
}, true);

async function loadHistory(number, box) {
  let card;
  try { card = await api('/api/card?number=' + encodeURIComponent(number)); } catch (e) { box.innerHTML = `<div class="muted">${esc(e.message)}</div>`; return; }
  const events = [];
  for (const c of card.calls) {
    const rv = c.review;
    const cls = !c.seconds ? 'miss' : rv?.success ? 'ok' : rv?.chance === 'hot' ? 'hot' : '';
    const head = `${when(c.at)} · ${c.incoming ? 'входящий' : 'исходящий'} · ${c.seconds ? dur(c.seconds) : 'не дозвонились'} · ${esc(c.who)}`;
    const text = rv ? (rv.manager_text || rv.rop_line || '') : '';
    events.push({at: c.at || '', html: `<div class="h ${cls}"><div class="hd">${head}</div>${text ? `<div class="bd">${esc(text)}</div>` : ''}${listenBtn(c)}</div>`});
  }
  for (const o of card.outcomes) {
    const due = o.due_date ? ` · на ${ddmm(o.due_date)}${o.due_time ? ' ' + o.due_time : ''}` : '';
    events.push({at: o.at || '', html: `<div class="h mark"><div class="hd">${when(o.at)} · отметка в CRM · ${esc(o.who || '')}</div><div class="bd"><b>${esc(o.label)}</b>${esc(due)}${o.comment ? '\n' + esc(o.comment) : ''}</div></div>`});
  }
  events.sort((a, b) => b.at.localeCompare(a.at));
  const tasks = card.tasks.map(t => `<div class="h hot"><div class="hd">Открытый ${esc(t.label.toLowerCase())} · ${esc(dueText(t))} · ${esc(t.who || '')}</div>${t.note ? `<div class="bd">«${esc(t.note)}»</div>` : ''}</div>`).join('');
  box.innerHTML = tasks + (events.map(e => e.html).join('') || (tasks ? '' : '<div class="muted">С этим номером ещё не говорили.</div>'));
}

function drawer(item, onDone) {
  const veil = document.createElement('div'); veil.className = 'veil';
  const d = document.createElement('div'); d.className = 'drawer'; d.setAttribute('role', 'dialog'); d.setAttribute('aria-modal', 'true');
  d.innerHTML = '<button class="btn small close">Закрыть</button><div class="stack" id="dw"></div>';
  document.body.append(veil, d);
  const close = () => { veil.remove(); d.remove(); document.removeEventListener('keydown', onKey); };
  const onKey = e => { if (e.key === 'Escape') close(); };
  document.addEventListener('keydown', onKey);
  veil.addEventListener('click', close);
  $('.close', d).addEventListener('click', close);
  workCard($('#dw', d), item, k => { close(); onDone && onDone(k); });
}

// ---------- views ----------
const VIEWS = {};

VIEWS.queue = async view => {
  const q = await api('/api/queue');
  S.queue = q;
  if (S.pos >= q.items.length) S.pos = 0;
  setCount('queue', q.items.length);
  if (!q.items.length) {
    view.innerHTML = `<div class="panel empty"><p><b>Очередь пуста.</b></p><p>${q.later ? `Ещё ${q.later} ${plural(q.later, 'номер', 'номера', 'номеров')} вернутся позже: перезвоны на другие дни и недозвоны через 2 часа.` : 'Добавьте номера на вкладке «База», и они появятся здесь.'}</p>
      <button class="btn primary" id="to-leads">Добавить номера</button></div>`;
    $('#to-leads').addEventListener('click', () => go('leads'));
    return;
  }
  const item = q.items[S.pos];
  const tasksN = q.items.filter(i => i.kind === 'task').length;
  view.innerHTML = `<div class="kpis">
      <div class="kpi"><b>${q.items.length}</b><span>в очереди сейчас</span></div>
      <div class="kpi"><b>${tasksN}</b><span>перезвоны на сегодня и просроченные</span></div>
      <div class="kpi"><b>${q.items.length - tasksN}</b><span>номера из базы</span></div>
      <div class="kpi"><b>${q.later}</b><span>вернутся позже</span></div></div>
    <div class="grid2"><div class="stack" id="work"></div>
    <div class="panel"><h2>Дальше по очереди <span class="muted">· ${q.items.length}</span></h2><div class="list" id="next"></div></div></div>`;
  workCard($('#work'), item, () => { S.queue.items.splice(S.pos, 1); go('queue'); },
    `<button class="btn small" id="skip">Пропустить ▸</button>`);
  $('#skip').addEventListener('click', () => { S.pos = (S.pos + 1) % q.items.length; S.dial = null; go('queue'); });
  $('#next').innerHTML = q.items.slice(0, 200).map((it, i) => listItem(it, i, i === S.pos)).join('');
  $$('#next .item').forEach(b => b.addEventListener('click', () => { S.pos = +b.dataset.i; S.dial = null; go('queue'); window.scrollTo(0, 0); }));
};

function listItem(it, i, current) {
  const right = it.kind === 'task'
    ? `<span class="chip ${it.late ? 'late' : 'today'}">${esc(dueText(it))}</span>`
    : `<span class="chip ${it.status === 'no_answer' ? 'warn' : ''}">${it.status === 'no_answer' ? 'повтор ' + (it.attempts + 1) : 'новый'}</span>`;
  return `<button class="item" data-i="${i}" aria-current="${current}"><div class="main-l">
    <div class="t num">${esc(phone(it.number))}</div><div class="s">${esc(it.title || it.note || (it.kind === 'task' ? it.label : it.source) || '')}</div></div>
    <div class="r">${right}</div></button>`;
}

function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  return m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20) ? few : many;
}
function setCount(page, n) { const el = $('#count-' + page); if (el) el.textContent = n ? String(n) : ''; }

VIEWS.tasks = async view => {
  const data = await api('/api/tasks');
  const today = data.today;
  const groups = [['Просрочено', t => t.due_date < today], ['Сегодня', t => t.due_date === today], ['Дальше', t => t.due_date > today]];
  setCount('tasks', data.items.filter(t => t.due_date <= today).length);
  if (!data.items.length) { view.innerHTML = '<div class="panel empty">Открытых перезвонов нет. Они появляются из разборов звонков и из итогов в CRM.</div>'; return; }
  view.innerHTML = `<div class="panel">${groups.map(([name, test]) => {
    const items = data.items.filter(test);
    return items.length ? `<div class="group">${name} · ${items.length}</div><div class="list">${items.map(t => `
      <div class="item"><div class="main-l"><div class="t"><span class="num">${esc(phone(t.number))}</span>${t.title ? ' · ' + esc(t.title) : ''}</div>
      <div class="s">${esc(t.label)} · ${esc(dueText(t))}${t.note ? ' · «' + esc(t.note) + '»' : ''}</div></div>
      <div class="row"><button class="btn small primary" data-open="${t.id}">Открыть</button>
      ${S.me.readonly ? '' : `<button class="btn small" data-move="${t.id}">Перенести</button>
      <button class="btn small" data-done="${t.id}">Выполнено</button>
      <button class="btn small danger" data-cancel="${t.id}">Отменить</button>`}</div></div>`).join('')}</div>` : '';
  }).join('')}</div>`;
  const byId = Object.fromEntries(data.items.map(t => [t.id, t]));
  $$('[data-open]', view).forEach(b => b.addEventListener('click', () => drawer(byId[b.dataset.open], () => go('tasks'))));
  const change = async (id, body, msg) => {
    try { await api(`/api/tasks/${id}`, {method: 'POST', body}); toast(msg); go('tasks'); } catch (e) { toast(e.message, true); }
  };
  $$('[data-done]', view).forEach(b => b.addEventListener('click', () => change(b.dataset.done, {action: 'done'}, 'Отмечен как выполненный')));
  $$('[data-cancel]', view).forEach(b => b.addEventListener('click', () => {
    if (confirm('Отменить перезвон?')) change(b.dataset.cancel, {action: 'cancelled'}, 'Перезвон отменён');
  }));
  $$('[data-move]', view).forEach(b => b.addEventListener('click', () => {
    const row = b.closest('.item');
    if ($('.movebox', row)) return;
    const box = document.createElement('div'); box.className = 'row movebox';
    box.innerHTML = `<input type="date" min="${today}" value="${nextWorkday(today)}" aria-label="Новая дата"><input type="time" step="300" aria-label="Время"><button class="btn small primary">Сохранить</button>`;
    row.querySelector('.main-l').appendChild(box);
    $('button', box).addEventListener('click', () => {
      const [d, t] = $$('input', box).map(x => x.value);
      change(b.dataset.move, {action: 'move', due_date: d, due_time: t || null}, `Перенесён на ${ddmm(d)}`);
    });
  }));
};

VIEWS.leads = async view => {
  const data = await api('/api/leads' + (S.leadFilter ? '?status=' + S.leadFilter : ''));
  const order = ['new', 'no_answer', 'callback', 'interested', 'proposal', 'meeting', 'not_interested', 'bad_number'];
  view.innerHTML = `<div class="grid2">
    <div class="panel"><h2>Номера в базе <span class="muted">· ${data.total}</span></h2>
      <div class="filters"><button data-f="" aria-pressed="${!S.leadFilter}">Все · ${data.total}</button>${order.filter(k => data.counts[k]).map(k =>
        `<button data-f="${k}" aria-pressed="${S.leadFilter === k}">${esc(data.labels[k])} · ${data.counts[k]}</button>`).join('')}</div>
      ${data.items.length ? `<div class="scroll"><table class="tbl"><thead><tr><th>Номер</th><th>Клиент</th><th class="hide-m">Источник</th><th>Статус</th><th></th></tr></thead><tbody>
      ${data.items.map(l => `<tr class="click" data-id="${l.id}"><td class="num">${esc(phone(l.number))}</td><td>${esc(l.title || '')}${l.note ? `<div class="muted">${esc(l.note)}</div>` : ''}</td>
        <td class="hide-m">${esc(l.source)}</td><td><span class="chip ${l.status === 'new' ? 'acc' : ['meeting', 'proposal'].includes(l.status) ? 'good' : ['not_interested', 'bad_number'].includes(l.status) ? 'bad' : 'warn'}">${esc(l.label)}${l.status === 'no_answer' ? ' · ' + l.attempts : ''}</span></td>
        <td>${S.me.readonly ? '' : `<button class="btn small danger" data-del="${l.id}" aria-label="Удалить номер">✕</button>`}</td></tr>`).join('')}
      </tbody></table></div>${data.items.length >= 500 ? '<p class="muted">Показаны последние 500.</p>' : ''}` : '<div class="empty">Здесь пока пусто. Вставьте номера справа.</div>'}
    </div>
    <div class="panel${S.me.readonly ? ' hidden' : ''}"><h2>Добавить номера</h2>
      <p class="muted">По одному на строку. Можно сразу с именем и компанией через точку с запятой: <span class="num">89991234567; Иван; ООО Ромашка</span>. Повторы и номера, которые уже есть, пропущу.</p>
      <label class="field">Номера<textarea id="l-text" rows="10" placeholder="+7 999 123-45-67; Иван Петров; ООО Ромашка"></textarea></label>
      <label class="field">Откуда база<input id="l-src" maxlength="80" list="l-srcs" placeholder="например, ГЦК 17.10"><datalist id="l-srcs">${data.sources.map(s => `<option value="${esc(s)}">`).join('')}</datalist></label>
      <div class="row"><button class="btn primary" id="l-add">Добавить в очередь</button></div>
      <div id="l-res" class="muted"></div>
    </div></div>`;
  $$('[data-f]', view).forEach(b => b.addEventListener('click', () => { S.leadFilter = b.dataset.f || null; go('leads'); }));
  const byId = Object.fromEntries(data.items.map(l => [l.id, l]));
  $$('tr[data-id]', view).forEach(tr => tr.addEventListener('click', e => {
    if (e.target.closest('[data-del]')) return;
    drawer(byId[tr.dataset.id], () => go('leads'));
  }));
  $$('[data-del]', view).forEach(b => b.addEventListener('click', async () => {
    if (!confirm('Удалить номер из базы?')) return;
    try { await api('/api/leads/' + b.dataset.del, {method: 'DELETE'}); toast('Удалён'); go('leads'); } catch (e) { toast(e.message, true); }
  }));
  if (!S.me.readonly) $('#l-add').addEventListener('click', async () => {
    const text = $('#l-text').value;
    if (!text.trim()) { toast('Вставьте номера', true); return; }
    try {
      const r = await api('/api/leads', {method: 'POST', body: {text, source: $('#l-src').value}});
      const parts = [`Добавлено: ${r.added}`];
      if (r.duplicates) parts.push(`повторы и уже были в базе: ${r.duplicates}`);
      if (r.invalid) parts.push(`не распознано: ${r.invalid}`);
      toast(parts.join(', '));
      S.leadFilter = null;
      await go('leads');
      if (r.invalid) $('#l-res').textContent = 'Не распознаны: ' + r.invalid_samples.join(' | ');
    } catch (e) { toast(e.message, true); }
  });
};

VIEWS.calls = async view => {
  const data = await api('/api/calls?days=7');
  if (!data.items.length) { view.innerHTML = '<div class="panel empty">За 7 дней звонков нет.</div>'; return; }
  view.innerHTML = `<div class="panel"><h2>Мои звонки за 7 дней <span class="muted">· ${data.items.length}</span></h2><div class="scroll"><table class="tbl">
    <thead><tr><th>Когда</th><th>Номер</th><th class="n">Длит.</th><th>Разбор</th><th>Запись</th></tr></thead><tbody>
    ${data.items.map(c => {
      const rv = c.review;
      const mark = !c.seconds ? '<span class="chip bad">не дозвонились</span>' : rv ? `<span class="chip ${rv.success ? 'good' : rv.chance === 'hot' ? 'warn' : ''}">${rv.success ? 'результат' : rv.chance === 'hot' ? 'был шанс' : 'без результата'}</span>` : '';
      return `<tr class="click" data-n="${esc(c.number || '')}"><td>${esc(when(c.at))}<div class="muted">${c.incoming ? 'входящий' : 'исходящий'}</div></td>
        <td class="num">${esc(phone(c.number))}</td><td class="n">${c.seconds ? dur(c.seconds) : '—'}</td>
        <td>${mark}${rv?.rop_line ? `<div>${esc(rv.rop_line)}</div>` : ''}</td><td class="rec-cell">${listenBtn(c)}</td></tr>`;
    }).join('')}</tbody></table></div></div>`;
  $$('tr[data-n]', view).forEach(tr => tr.addEventListener('click', () => {
    if (tr.dataset.n) drawer({kind: 'call', number: tr.dataset.n, title: '', note: ''});
  }));
};

function sumRows(rows) {
  const t = {dials: 0, answered: 0, talks: 0, incoming: 0, missed: 0, seconds: 0, crm_dials: 0, outcomes: {}};
  for (const r of rows) {
    for (const k of ['dials', 'answered', 'talks', 'incoming', 'missed', 'seconds', 'crm_dials']) t[k] += r[k];
    for (const [k, v] of Object.entries(r.outcomes)) t.outcomes[k] = (t.outcomes[k] || 0) + v;
  }
  return t;
}

function statsFilters() {
  return `<div class="filters">${[[1, 'Сегодня'], [7, '7 дней'], [30, '30 дней']].map(([d, t]) =>
    `<button data-days="${d}" aria-pressed="${S.statsDays === d}">${t}</button>`).join('')}</div>`;
}
function bindStats(view, page) {
  $$('[data-days]', view).forEach(b => b.addEventListener('click', () => { S.statsDays = +b.dataset.days; go(page); }));
}

VIEWS.stats = async view => {
  const data = await api('/api/stats?days=' + S.statsDays);
  const rows = data.rows.filter(r => !S.me.privileged || r.ext === S.ext);
  const t = sumRows(rows);
  const pct = (a, b) => b ? Math.round(a / b * 100) + '%' : '—';
  const days = [...new Set(rows.map(r => r.day))].sort();
  const byDay = Object.fromEntries(days.map(d => [d, sumRows(rows.filter(r => r.day === d))]));
  const max = Math.max(1, ...days.map(d => byDay[d].dials));
  const marks = OUT_ORDER.filter(k => t.outcomes[k]);
  view.innerHTML = `${statsFilters()}<div class="kpis">
      <div class="kpi"><b>${t.dials}</b><span>наборов (Mango)</span></div>
      <div class="kpi"><b>${t.answered}</b><span>дозвонились · ${pct(t.answered, t.dials)}</span></div>
      <div class="kpi"><b>${t.talks}</b><span>разговоров от ${data.min_seconds} с</span></div>
      <div class="kpi"><b>${hm(t.seconds)}</b><span>на линии</span></div>
      <div class="kpi"><b>${t.missed}</b><span>пропущено входящих из ${t.incoming}</span></div></div>
    <div class="grid2"><div class="panel"><h2>Наборы по дням</h2>${days.length > 1 ? `<div class="bars">${days.map(d => {
        const x = byDay[d];
        return `<div class="bar" title="${ddmm(d)}: ${x.dials} наборов, ${x.answered} дозвонились"><div class="stackbar"><i data-h="${x.answered / max * 100}"></i><i class="b2" data-h="${(x.dials - x.answered) / max * 100}"></i></div><span>${ddmm(d)}</span></div>`;
      }).join('')}</div><p class="muted">Зелёным дозвонились, серым не взяли трубку.</p>` : '<div class="empty">График появится, когда будет больше одного дня.</div>'}</div>
    <div class="panel"><h2>Итоги, отмеченные в CRM</h2>${marks.length ? `<table class="tbl"><tbody>${marks.map(k =>
      `<tr><td>${esc(data.labels[k])}</td><td class="n">${t.outcomes[k]}</td></tr>`).join('')}</tbody></table>` : '<div class="empty">Пока нет отметок.</div>'}
      <p class="muted">Звонков из CRM: ${t.crm_dials}.</p></div></div>`;
  heights(view);
  bindStats(view, 'stats');
};

VIEWS.team = async view => {
  const data = await api('/api/stats?days=' + S.statsDays, {scoped: false});
  const pct = (a, b) => b ? Math.round(a / b * 100) + '%' : '—';
  const people = S.me.team.map(p => ({...p, t: sumRows(data.rows.filter(r => r.ext === p.ext))}));
  const all = sumRows(data.rows.filter(r => S.me.team.some(p => p.ext === r.ext)));
  const good = o => (o.meeting || 0) + (o.proposal || 0);
  view.innerHTML = `${statsFilters()}<div class="kpis">
      <div class="kpi"><b>${all.dials}</b><span>наборов у отдела</span></div>
      <div class="kpi"><b>${pct(all.answered, all.dials)}</b><span>дозвон</span></div>
      <div class="kpi"><b>${all.talks}</b><span>разговоров от ${data.min_seconds} с</span></div>
      <div class="kpi"><b>${good(all.outcomes)}</b><span>встреч и КП в CRM</span></div></div>
    <div class="panel scroll"><table class="tbl"><thead><tr><th>Менеджер</th><th class="n">Наборов</th><th class="n">Дозвон</th>
      <th class="n">От ${data.min_seconds} с</th><th class="n hide-m">На линии</th><th class="n hide-m">Из CRM</th><th class="n">Встречи и КП</th><th class="n hide-m">Перезвоны</th><th class="n hide-m">Отказы</th></tr></thead><tbody>
      ${people.map(p => `<tr class="click" data-ext="${esc(p.ext)}"><td>${esc(p.name)} <span class="muted">· ${esc(p.ext)}</span></td><td class="n">${p.t.dials}</td>
        <td class="n">${pct(p.t.answered, p.t.dials)}</td><td class="n">${p.t.talks}</td><td class="n hide-m">${hm(p.t.seconds)}</td>
        <td class="n hide-m">${p.t.crm_dials}</td><td class="n">${good(p.t.outcomes)}</td><td class="n hide-m">${p.t.outcomes.callback || 0}</td>
        <td class="n hide-m">${p.t.outcomes.not_interested || 0}</td></tr>`).join('')}
    </tbody></table><p class="muted">Нажмите на менеджера, чтобы открыть его очередь.</p></div>`;
  bindStats(view, 'team');
  $$('tr[data-ext]', view).forEach(tr => tr.addEventListener('click', () => {
    S.ext = tr.dataset.ext; S.queue = null; S.pos = 0;
    const pick = $('#pick-ext'); if (pick) pick.value = S.ext;
    go('queue');
  }));
};

// ---------- кабинет клиента колл-центра ----------
const rub = v => Math.round(v || 0).toLocaleString('ru-RU') + ' ₽';
const pctOf = (a, b) => b ? Math.round(a / b * 100) + '%' : '—';
const RESULT = {interested: ['Заинтересованный', 'good'], callback: ['Перезвон', 'warn'], not_relevant: ['Неактуально', 'bad'],
                no_answer: ['Не дозвонились', ''], not_called: ['Ещё не звонили', ''], other: ['Другое', '']};

async function cabinetData(name) {
  const key = name || '';
  if (!S.cabCache || S.cabCache.key !== key || Date.now() - S.cabCache.at > 120000) {
    const c = await api('/api/cabinet' + (name ? '?client=' + encodeURIComponent(name) : ''), {scoped: false});
    S.cabCache = {key, at: Date.now(), c};
  }
  return S.cabCache.c;
}

function cabHeader(c) {
  return `<p class="muted">Данные колл-центра Averon по базе «${esc(c.name)}»${c.since ? ` с ${ddmm(c.since)}` : ''}. Обновляются из таблицы обзвона.</p>`;
}

function cabSummary(c) {
  const k = c.counts;
  const t = Object.fromEntries((c.tariffs || []).map(x => [x.name, x.price]));
  const bases = c.bases.filter(b => b.date);
  return `${cabHeader(c)}<div class="kpis">
      <div class="kpi"><b>${k.total}</b><span>номеров передано</span></div>
      <div class="kpi"><b>${k.called}</b><span>обзвонено · ${pctOf(k.called, k.total)}</span></div>
      <div class="kpi"><b>${k.reached}</b><span>дозвонились · ${pctOf(k.reached, k.called)}</span></div>
      <div class="kpi"><b>${k.interested}</b><span>заинтересованных</span></div>
      <div class="kpi"><b>${rub(c.summary.sum)}</b><span>к оплате</span></div></div>
    <div class="grid2"><div class="panel"><h2>Итог по номерам</h2><table class="tbl"><tbody>
      ${['interested', 'callback', 'not_relevant', 'no_answer', 'not_called', 'other'].filter(x => k[x]).map(x =>
        `<tr><td><span class="chip ${RESULT[x][1]}">${RESULT[x][0]}</span></td><td class="n">${k[x]}</td><td class="n muted">${pctOf(k[x], k.total)}</td></tr>`).join('')}
      </tbody></table>
      ${bases.length ? `<h2 class="gap">По базам</h2><div class="scroll"><table class="tbl"><thead><tr><th>База от</th><th class="n">Номеров</th><th class="n">Обзвонено</th><th class="n">Дозвон</th><th class="n">Заинтерес.</th></tr></thead><tbody>
        ${bases.map(b => `<tr><td>${ddmm(b.date)}.${b.date.slice(0, 4)}</td><td class="n">${b.total}</td><td class="n">${b.called || 0}</td><td class="n">${pctOf(b.reached || 0, b.called || 0)}</td><td class="n">${b.interested || 0}</td></tr>`).join('')}
      </tbody></table></div>` : ''}</div>
    <div class="stack"><div class="panel"><h2>Расчёт</h2><table class="tbl"><tbody>
      <tr><td>Первичных звонков</td><td class="n">${c.summary.first}</td><td class="n muted">${t['Первичный звонок'] != null ? '× ' + t['Первичный звонок'] + ' ₽' : ''}</td></tr>
      <tr><td>Повторных звонков</td><td class="n">${c.summary.repeat}</td><td class="n muted">${t['Повторный звонок (после НДЗ)'] != null ? '× ' + t['Повторный звонок (после НДЗ)'] + ' ₽' : ''}</td></tr>
      <tr><td>Заинтересованных</td><td class="n">${c.summary.interested}</td><td class="n muted">${t['Звонок с результатом «Заинтересованный»'] != null ? '× ' + t['Звонок с результатом «Заинтересованный»'] + ' ₽' : ''}</td></tr>
      <tr><td><b>Итого</b></td><td></td><td class="n"><b>${rub(c.summary.sum)}</b></td></tr></tbody></table></div>
    <div class="panel"><h2>Последние заинтересованные <span class="muted">· ${c.interested.length}</span></h2>${c.interested.length ? `<div class="list">${c.interested.slice(0, 5).map(leadRow).join('')}</div>` : '<div class="empty">Пока нет.</div>'}</div></div></div>`;
}

function leadRow(l) {
  return `<div class="item"><div class="main-l"><div class="t num">${esc(phone(l.phone) || l.phone)}</div>
    <div class="s">${esc([l.source, l.comment].filter(Boolean).join(' · '))}</div></div>
    <div class="r">${l.last ? ddmm(l.last) : ''}${l.remind ? `<span class="chip warn">${esc(l.remind)}</span>` : ''}</div></div>`;
}

function cabLeads(c, kind) {
  const list = kind === 'int' ? c.interested : c.callbacks;
  if (!list.length) return `${cabHeader(c)}<div class="panel empty">${kind === 'int' ? 'Заинтересованных пока нет.' : 'Перезвонов сейчас нет.'}</div>`;
  return `${cabHeader(c)}<div class="panel scroll"><table class="tbl"><thead><tr><th>Телефон</th><th>Звонок</th>${kind === 'cb' ? '<th>Напоминание</th>' : ''}<th class="hide-m">Источник</th><th>Комментарий оператора</th></tr></thead><tbody>
    ${list.map(l => `<tr><td class="num">${esc(phone(l.phone) || l.phone)}</td><td>${l.last ? ddmm(l.last) : ''}<div class="muted">${esc(l.attempts.filter(Boolean).join(' → '))}</div></td>
      ${kind === 'cb' ? `<td>${esc(l.remind || '')}</td>` : ''}<td class="hide-m">${esc(l.source)}</td><td>${esc(l.comment)}</td></tr>`).join('')}
    </tbody></table></div>`;
}

function cabDays(c) {
  const days = c.days.slice(0, 30).reverse();
  const max = Math.max(1, ...days.map(d => d.calls));
  return `${cabHeader(c)}<div class="panel"><h2>Звонки по дням</h2>${days.length > 1 ? `<div class="bars">${days.map(d =>
      `<div class="bar" title="${ddmm(d.day)}: ${d.calls} звонков, дозвонились ${d.reached || 0}"><div class="stackbar"><i data-h="${(d.reached || 0) / max * 100}"></i><i class="b2" data-h="${(d.calls - (d.reached || 0)) / max * 100}"></i></div><span>${ddmm(d.day)}</span></div>`).join('')}</div>
      <p class="muted">Зелёным дозвонились, серым не взяли трубку.</p>` : ''}
    <div class="scroll"><table class="tbl"><thead><tr><th>День</th><th class="n">Звонков</th><th class="n hide-m">Первичных</th><th class="n hide-m">Повторных</th><th class="n">Дозвонились</th><th class="n">Заинтерес.</th></tr></thead><tbody>
    ${c.days.map(d => `<tr><td>${ddmm(d.day)}</td><td class="n">${d.calls}</td><td class="n hide-m">${d.first || 0}</td><td class="n hide-m">${d.repeat || 0}</td><td class="n">${d.reached || 0}</td><td class="n">${d.interested || 0}</td></tr>`).join('')}
    </tbody></table></div></div>`;
}

VIEWS.cab = async view => { view.innerHTML = cabSummary(await cabinetData()); };
VIEWS.cabint = async view => { view.innerHTML = cabLeads(await cabinetData(), 'int'); };
VIEWS.cabcb = async view => { view.innerHTML = cabLeads(await cabinetData(), 'cb'); };
VIEWS.cabdays = async view => { view.innerHTML = cabDays(await cabinetData()); heights(view); };

VIEWS.clients = async view => {
  if (S.cabName) {
    const c = await cabinetData(S.cabName);
    const tab = S.cabTab || 'cab';
    view.innerHTML = `<div class="row gapb"><button class="btn small" id="cab-back">← Все клиенты</button><b>${esc(c.name)}</b></div>
      <div class="filters">${[['cab', 'Сводка'], ['int', 'Заинтересованные'], ['cb', 'Перезвоны'], ['days', 'По дням']].map(([k, t]) =>
        `<button data-tab="${k}" aria-pressed="${tab === k}">${t}</button>`).join('')}</div>
      <div id="cabbody">${tab === 'cab' ? cabSummary(c) : tab === 'days' ? cabDays(c) : cabLeads(c, tab)}</div>`;
    heights(view);
    $('#cab-back').addEventListener('click', () => { S.cabName = null; go('clients'); });
    $$('[data-tab]', view).forEach(b => b.addEventListener('click', () => { S.cabTab = b.dataset.tab; go('clients'); }));
    return;
  }
  const data = await api('/api/cabinet', {scoped: false});
  if (!data.clients.length) { view.innerHTML = '<div class="panel empty">В таблице колл-центра пока нет клиентов.</div>'; return; }
  view.innerHTML = `<div class="panel scroll"><h2>Клиенты колл-центра</h2><table class="tbl"><thead><tr><th>Клиент</th><th class="n">Первичных</th><th class="n">Повторных</th><th class="n">Заинтерес.</th><th class="n">Сумма</th></tr></thead><tbody>
    ${data.clients.map(c => `<tr class="click" data-name="${esc(c.name)}"><td>${esc(c.name)}</td><td class="n">${c.first}</td><td class="n">${c.repeat}</td><td class="n">${c.interested}</td><td class="n">${rub(c.sum)}</td></tr>`).join('')}
    </tbody></table><p class="muted">Нажмите на клиента, чтобы открыть его кабинет так, как его видит клиент.</p></div>`;
  $$('tr[data-name]', view).forEach(tr => tr.addEventListener('click', () => { S.cabName = tr.dataset.name; S.cabTab = 'cab'; go('clients'); }));
};

VIEWS.reports = async view => {
  const data = await api('/api/reports', {scoped: false});
  const groups = S.me.role === 'director'
    ? [['all', 'Все'], ['rop', 'Отделу'], ['day_manager', 'Итоги менеджеров']]
    : [['all', 'Все']];
  const f = S.repFilter || 'all';
  const shown = data.items.filter(r => f === 'all' || (f === 'rop' ? r.kind !== 'day_manager' : r.kind === f));
  view.innerHTML = `${groups.length > 1 ? `<div class="filters">${groups.map(([k, t]) => `<button data-rf="${k}" aria-pressed="${f === k}">${t}</button>`).join('')}</div>` : ''}
    ${shown.length ? shown.map(r => `<article class="panel report">
      <div class="rep-head"><b>${esc(r.title)}</b>${r.who ? `<span>${esc(r.who)}</span>` : ''}<span class="muted">${fmtWhen(r.at)}</span>${r.note ? `<span class="chip">${esc(r.note)}</span>` : ''}</div>
      <div class="rep-body">${r.html}</div>
      ${r.calls.length ? `<div class="rep-calls">${r.calls.map(c => `<button class="btn small" data-review="${c.id}">${esc(c.label)}</button>`).join('')}</div>` : ''}
    </article>`).join('') : `<div class="panel empty">${data.items.length ? 'В этом разделе отчётов нет.' : 'Отчёты появятся здесь, когда бот пришлёт следующий: итоги дня вечером, сводки утром, по понедельникам и 1 числа.'}</div>`}`;
  $$('[data-rf]', view).forEach(b => b.addEventListener('click', () => { S.repFilter = b.dataset.rf; go('reports'); }));
};

function modal(html) {
  const veil = document.createElement('div'); veil.className = 'rvm-veil';
  const box = document.createElement('div'); box.className = 'rvm'; box.setAttribute('role', 'dialog'); box.setAttribute('aria-modal', 'true');
  box.innerHTML = html;
  document.body.append(veil, box);
  const close = () => { veil.remove(); box.remove(); document.removeEventListener('keydown', onKey); };
  const onKey = e => { if (e.key === 'Escape') close(); };
  document.addEventListener('keydown', onKey);
  veil.addEventListener('click', close);
  box.addEventListener('click', e => { if (e.target.closest('.rvm-x')) close(); });
  return box;
}

async function trainingDetail(id) {
  const box = modal('<header class="rvm-head"><div class="rvm-mut">Загружаю…</div><button class="rvm-x" aria-label="Закрыть">×</button></header>');
  let t;
  try { t = await api('/api/trainings/' + id, {scoped: false}); } catch (e) { $('.rvm-mut', box).textContent = e.message; return; }
  box.innerHTML = `<header class="rvm-head"><div><span class="rvm-chip">${esc(t.result)}</span><h2>${esc(t.who)} · ${esc(t.mode)}</h2>
      <div class="rvm-mut">${fmtWhen(t.at)}${t.topic ? ' · ' + esc(t.topic) : ''}${t.test ? ' · пробная' : ''}</div></div><button class="rvm-x" aria-label="Закрыть">×</button></header>
    <div class="rvm-body">${t.checks.length ? t.checks.map((c, i) => `<div class="rvm-item ${c.passed ? 'good' : 'miss'}">
        <div><b>${i + 1}. Клиент:</b> ${esc(c.objection)}</div><div><b>Ответ:</b> ${esc(c.answer)}</div><div class="rvm-q">${c.passed ? 'Зачтено' : 'Не зачтено'}${c.comment ? ': ' + esc(c.comment) : ''}</div></div>`).join('')
      : t.turns.length ? t.turns.map(x => `<div class="rvm-line"><b>${esc(x.who)}</b> ${esc(x.text)}</div>`).join('') : '<div class="rvm-mut">Реплик нет.</div>'}</div>`;
}

VIEWS.training = async view => {
  const d = await api('/api/trainings');
  const director = S.me.role === 'director';
  const assign = director ? `<div class="panel"><h2>Назначить тренировку${S.ext ? '' : ''}</h2>
      <p class="muted">Менеджеру придёт сообщение в боте с кнопкой старта, назначение появится и у него в CRM. Сам тренажёр работает в Telegram, там можно отвечать голосом.</p>
      <div class="outform">
        <label class="field">Менеджер<select id="t-ext">${S.me.team.map(m => `<option value="${esc(m.ext)}"${m.ext === S.ext ? ' selected' : ''}>${esc(m.name)} · доб. ${esc(m.ext)}</option>`).join('')}</select></label>
        <label class="field">Режим<select id="t-mode"><option value="dialog">Разговор целиком</option><option value="drill">Отработка возражений</option></select></label>
        <label class="field">Тема, если нужна<input id="t-topic" maxlength="200" placeholder="Например, «пришлите на почту»"></label>
        <button class="btn primary" id="t-go">Назначить</button></div><div id="t-out" class="muted"></div></div>` : '';
  const pending = d.pending.length ? `<div class="panel"><h2>Назначено, ещё не начато <span class="muted">· ${d.pending.length}</span></h2><div class="list">
      ${d.pending.map(p => `<div class="row gapb"><span>${director ? esc(p.who) + ' · ' : ''}${esc(p.mode)}${p.topic ? ' · ' + esc(p.topic) : ''}</span>
        ${p.link ? `<a class="btn small primary" href="${esc(p.link)}" target="_blank" rel="noopener">Начать в тренажёре</a>` : ''}</div>`).join('')}</div></div>` : '';
  const list = d.items.length ? `<div class="panel"><h2>${director ? 'Тренировки менеджера' : 'Мои тренировки'} <span class="muted">· ${d.items.length}</span></h2><div class="scroll"><table class="tbl">
      <thead><tr><th>Когда</th>${director ? '<th>Кто</th>' : ''}<th>Режим</th><th>Тема</th><th>Итог</th></tr></thead><tbody>
      ${d.items.map(t => `<tr class="click" data-tid="${t.id}"><td>${fmtWhen(t.at)}</td>${director ? `<td>${esc(t.who)}</td>` : ''}<td>${esc(t.mode)}</td><td>${esc(t.topic || '')}</td><td>${esc(t.result)}${t.test ? ' <span class="muted">· пробная</span>' : ''}</td></tr>`).join('')}
      </tbody></table></div></div>` : `<div class="panel empty">Тренировок пока не было.${director ? '' : ' Откройте подробный разбор звонка и нажмите «Потренировать этот момент».'}</div>`;
  view.innerHTML = `<div class="stack">${assign}${pending}${list}</div>`;
  $$('tr[data-tid]', view).forEach(tr => tr.addEventListener('click', () => trainingDetail(tr.dataset.tid)));
  if (director) $('#t-go', view).addEventListener('click', async () => {
    const out = $('#t-out', view);
    try {
      await api('/api/trainings', {method: 'POST', scoped: false, body: {person_ext: $('#t-ext', view).value, mode: $('#t-mode', view).value, topic: $('#t-topic', view).value}});
      out.textContent = 'Назначено.'; setTimeout(() => go('training'), 700);
    } catch (e) { out.textContent = e.message; }
  });
};

const qaHtml = q => `<article class="panel qa"><div class="q"><span class="muted">${fmtWhen(q.at)}</span>${esc(q.question)}</div>
  <div class="a">${q.html || '<span class="muted">Ответа нет.</span>'}</div></article>`;

VIEWS.ask = async view => {
  const d = await api('/api/ask', {scoped: false});
  const director = S.me.role === 'director';
  const examples = director
    ? ['Кто из менеджеров чаще всего теряет клиента на возражении «пришлите на почту»?', 'Что общего у звонков с результатом за неделю?', 'Какие три темы взять на планёрку завтра?']
    : ['Где я чаще всего теряю клиента?', 'Что у меня получилось лучше всего на этой неделе?', 'Какие перезвоны я обещал на эту неделю?'];
  view.innerHTML = `<div class="panel"><h2>Спросите про звонки отдела</h2>
      <p class="muted">Отвечает агент РОПа по разобранным звонкам${director ? '' : ' (видит только ваши звонки)'}, как в боте. Ответ занимает до минуты.</p>
      <textarea id="q-text" rows="3" maxlength="2000" placeholder="${esc(examples[0])}"></textarea>
      <div class="row gapb chips">${examples.map(x => `<button class="btn small" data-ex="${esc(x)}">${esc(x)}</button>`).join('')}</div>
      <button class="btn primary" id="q-go">Спросить</button><span class="muted" id="q-st"></span></div>
    <div id="q-list">${d.items.map(qaHtml).join('')}</div>`;
  const ta = $('#q-text', view);
  $$('[data-ex]', view).forEach(b => b.addEventListener('click', () => { ta.value = b.dataset.ex; ta.focus(); }));
  $('#q-go', view).addEventListener('click', async () => {
    const btn = $('#q-go', view), st = $('#q-st', view);
    btn.disabled = true; st.textContent = ' Смотрю данные…';
    try {
      const q = await api('/api/ask', {method: 'POST', scoped: false, body: {question: ta.value}});
      $('#q-list', view).insertAdjacentHTML('afterbegin', qaHtml({...q, at: new Date().toISOString()}));
      ta.value = ''; st.textContent = '';
    } catch (e) { st.textContent = ' ' + e.message; }
    btn.disabled = false;
  });
};

const fmtWhen = v => v ? new Date(v).toLocaleString('ru-RU', {day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'}) : '—';

VIEWS.access = async view => {
  const d = await api('/api/access', {scoped: false});
  const roleName = {director: 'Директор', manager: 'Менеджер', client: 'Клиент'};
  const who = i => i.role === 'manager' ? `${esc(i.name)} · доб. ${esc(i.ext)}` : esc(i.name || roleName[i.role]) + (i.tg ? ' · с Telegram' : '');
  view.innerHTML = `<div class="grid2"><div class="panel"><h2>Выдать личную ссылку</h2>
      <p class="muted">По ссылке человек входит в CRM без пароля, на любом устройстве. Ссылку видно только сейчас: скопируйте и отправьте лично.
        Ссылка руководителя, привязанная к его Telegram, открывает ему и «Промты».</p>
      <div class="outform">
        <label class="field">Кому<select id="a-role"><option value="manager">Менеджеру</option><option value="client">Клиенту колл-центра</option><option value="director">Руководителю</option></select></label>
        <label class="field" id="a-ext-f">Менеджер<select id="a-ext">${d.managers.map(m => `<option value="${esc(m.ext)}">${esc(m.name)} · доб. ${esc(m.ext)}</option>`).join('')}</select></label>
        <label class="field" id="a-cl-f" hidden>Клиент<select id="a-client">${d.clients.map(c => `<option>${esc(c)}</option>`).join('')}</select></label>
        <label class="field" id="a-tg-f" hidden>Чья ссылка<select id="a-tg">${d.directors.map(p => `<option value="${esc(p.tg)}">${esc(p.name)}</option>`).join('')}<option value="">Другой человек, без Telegram</option></select></label>
        <label class="field" id="a-name-f" hidden>Имя<input id="a-name" maxlength="80" placeholder="Например, Евгений"></label>
        <button class="btn primary" id="a-go">Создать ссылку</button></div>
      <div id="a-out"></div></div>
    <div class="panel"><h2>Действующие ссылки <span class="muted">· ${d.invites.length}</span></h2>
      ${d.invites.length ? `<div class="scroll"><table class="tbl"><thead><tr><th>Кто</th><th>Вход</th><th>Создана</th><th>Последний вход</th><th></th></tr></thead><tbody>
        ${d.invites.map(i => `<tr><td>${who(i)}</td><td>${roleName[i.role]}</td><td>${fmtWhen(i.created)}</td><td>${fmtWhen(i.used)}</td>
          <td><button class="btn small" data-revoke="${i.id}">Отозвать</button></td></tr>`).join('')}</tbody></table></div>`
        : '<div class="empty">Ссылок ещё нет.</div>'}
      <p class="muted">Отозванная ссылка сразу перестаёт работать на всех устройствах. Если менеджера убрать из отдела в боте, его ссылка тоже перестанет работать.</p></div></div>`;
  const role = $('#a-role', view);
  const sync = () => {
    $('#a-ext-f', view).hidden = role.value !== 'manager';
    $('#a-cl-f', view).hidden = role.value !== 'client';
    $('#a-tg-f', view).hidden = role.value !== 'director';
    $('#a-name-f', view).hidden = role.value !== 'director' || $('#a-tg', view).value !== '';
  };
  $('#a-tg', view).addEventListener('change', sync);
  role.addEventListener('change', sync);
  $('#a-go', view).addEventListener('click', async () => {
    const out = $('#a-out', view);
    try {
      const r = await api('/api/access', {method: 'POST', scoped: false, body: {role: role.value, person_ext: $('#a-ext', view).value,
        client: $('#a-client', view).value, name: $('#a-tg', view).value ? '' : $('#a-name', view).value, tg: $('#a-tg', view).value}});
      out.innerHTML = `<div class="linkbox"><input readonly value="${esc(r.link)}" aria-label="Ссылка"><button class="btn small" id="a-copy">Скопировать</button></div>
        <p class="muted">Ссылка появится в списке после обновления страницы.</p>`;
      $('#a-copy', out).addEventListener('click', async () => {
        try { await navigator.clipboard.writeText(r.link); $('#a-copy', out).textContent = 'Скопировано'; }
        catch (e) { $('input', out).select(); }
      });
    } catch (e) { out.innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
  });
  $$('[data-revoke]', view).forEach(b => b.addEventListener('click', async () => {
    if (!confirm('Отозвать ссылку? Человек сразу выйдет из CRM.')) return;
    try { await api(`/api/access/${b.dataset.revoke}/revoke`, {method: 'POST', scoped: false, body: {}}); go('access'); }
    catch (e) { alert(e.message); }
  }));
};

function linkBox(out, link, note) {
  out.innerHTML = `<div class="linkbox"><input readonly value="${esc(link)}" aria-label="Ссылка"><button class="btn small">Скопировать</button></div>
    ${note ? `<p class="muted">${esc(note)}</p>` : ''}`;
  $('button', out).addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(link); $('button', out).textContent = 'Скопировано'; }
    catch (e) { $('input', out).select(); }
  });
}

// ---------- сотрудники: список и карточка человека ----------
const pctN = (a, b) => b ? Math.round(a / b * 100) : null;
const pctTxt = (a, b) => b ? pctN(a, b) + '%' : '—';
// Стрелка к прошлой неделе: доля с результатом выросла или упала.
function trend(now, before) {
  const a = pctN(now.success, now.talks), b = pctN(before.success, before.talks);
  if (a === null || b === null || a === b) return '';
  return a > b ? `<span class="trend up" title="Прошлая неделя: ${b}%">▲ ${a - b}</span>` : `<span class="trend down" title="Прошлая неделя: ${b}%">▼ ${b - a}</span>`;
}
const botChip = p => p.linked ? `<span class="chip good">в боте</span>` : p.invite_until ? '<span class="chip warn">ждёт по ссылке</span>' : '<span class="chip">не подключён</span>';

VIEWS.staff = async view => {
  if (S.person) return personView(view, S.person);
  const d = await api('/api/staff', {scoped: false});
  const managers = d.items.filter(p => p.role === 'manager');
  const others = d.items.filter(p => p.role !== 'manager');
  const mRow = p => {
    const s = p.stats || {week: {talks: 0, success: 0}, prev: {talks: 0, success: 0}, dials: {dials: 0}, top: null};
    const head = `<tr class="click" data-person="${esc(p.ext)}"><td><b>${esc(p.name)}</b> <span class="muted">· ${esc(p.ext)}</span></td>
      <td class="n hide-m">${s.dials.dials}</td>`;
    if (p.kitchen) return `${head}<td class="hide-m"></td><td class="muted">свой скрипт (кухни)</td><td class="hide-m"></td><td>${botChip(p)}</td></tr>`;
    return `${head}<td class="n hide-m">${s.week.talks}</td>
      <td class="n">${s.week.success} <span class="muted">${pctTxt(s.week.success, s.week.talks)}</span> ${trend(s.week, s.prev)}</td>
      <td class="hide-m">${s.top ? `${esc(s.top.label)} <span class="muted">· ${s.top.n}</span>` : '<span class="muted">—</span>'}</td>
      <td>${botChip(p)}</td></tr>`;
  };
  view.innerHTML = `<div class="panel"><h2>Менеджеры <span class="muted">· ${managers.length}</span></h2>
      <div class="scroll"><table class="tbl"><thead><tr><th>Кто</th><th class="n hide-m">Наборов за 7 дн.</th><th class="n hide-m">Разговоров разобрано</th>
        <th class="n">С результатом</th><th class="hide-m">Частая ошибка за 30 дн.</th><th>Бот</th></tr></thead><tbody>
      ${managers.map(mRow).join('') || '<tr><td colspan="6" class="muted">Менеджеров нет.</td></tr>'}</tbody></table></div>
      <p class="muted">Нажмите на человека, чтобы открыть его карточку: цифры, ошибки, звонки, тренировки, перезвоны, доступы. Стрелка показывает, как изменилась доля разговоров с результатом к прошлой неделе.</p></div>
    <div class="panel"><h2>Руководители <span class="muted">· ${others.length}</span></h2><div class="scroll"><table class="tbl"><tbody>
      ${others.map(p => `<tr class="click" data-person="${esc(p.ext)}"><td><b>${esc(p.name)}</b></td><td>${esc(p.role_title)}</td>
        <td>${botChip(p)}${p.username ? ' @' + esc(p.username) : ''}</td><td class="hide-m">${p.copies ? 'получает копии разборов' : ''}</td></tr>`).join('')}</tbody></table></div></div>
    <div class="panel"><h2>Добавить человека</h2>
      <div class="outform">
        <label class="field">Кто<select id="s-role"><option value="manager">Менеджер</option><option value="head">Руководитель</option></select></label>
        <label class="field" id="s-ext-f">Добавочный в Mango<input id="s-ext" inputmode="numeric" maxlength="6" placeholder="15"></label>
        <label class="field">Имя<input id="s-name" maxlength="80" placeholder="Например, Пётр Иванов"></label>
        <label class="field">Ник в Telegram, если знаете<input id="s-user" maxlength="33" placeholder="@nick"></label>
        <button class="btn primary" id="s-go">Добавить и дать ссылку</button></div>
      <div id="s-out"></div>
      <p class="muted">Менеджеров из Mango бот добавляет сам. С ником хватит простого /start в боте, ссылка нужна на случай, если ника нет. Ссылка одноразовая и действует 7 дней.
        Руководитель получает отчёты по отделу и горячие потери; копии всех разборов включаются в его карточке.</p></div>`;
  $$('[data-person]', view).forEach(tr => tr.addEventListener('click', () => { S.person = tr.dataset.person; S.personTab = null; S.memIdx = 0; go('staff'); }));
  const role = $('#s-role', view);
  role.addEventListener('change', () => { $('#s-ext-f', view).hidden = role.value !== 'manager'; });
  $('#s-go', view).addEventListener('click', async () => {
    const out = $('#s-out', view);
    try {
      const r = await api('/api/staff', {method: 'POST', scoped: false, body: {role: role.value, person_ext: $('#s-ext', view).value,
        name: $('#s-name', view).value, username: $('#s-user', view).value}});
      linkBox(out, r.link, 'Отправьте ссылку человеку лично. Он появится в списке после обновления страницы.');
    } catch (e) { out.innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
  });
};

// Своя карточка менеджера: то же, что видит руководитель, кроме доступов.
VIEWS.me = async view => personView(view, null);

const PERSON_TABS = [['over', 'Обзор'], ['memory', 'Память'], ['calls', 'Звонки и разборы'], ['train', 'Тренировки'], ['tasks', 'Перезвоны'], ['access', 'Доступ и бот']];

async function personView(view, ext) {
  const p = await api('/api/person' + (ext ? '?person=' + encodeURIComponent(ext) : ''), {scoped: false});
  const tabs = PERSON_TABS.filter(([k]) => k === 'access' ? !!p.access : p.manager);
  const tab = tabs.some(([k]) => k === S.personTab) ? S.personTab : tabs[0][0];
  const back = ext ? '<button class="btn small" id="p-back">← Все сотрудники</button>' : '';
  view.innerHTML = `<div class="row gapb">${back}<b class="p-name">${esc(p.name)}</b><span class="muted">${esc(p.role_title)}${/^\d+$/.test(p.ext) ? ' · доб. ' + esc(p.ext) : ''}</span>
      ${p.access ? botChip(p.access) : ''}</div>
    ${tabs.length > 1 ? `<div class="filters">${tabs.map(([k, t]) => `<button data-ptab="${k}" aria-pressed="${tab === k}">${t}</button>`).join('')}</div>` : ''}
    <div id="p-body"></div>`;
  if (back) $('#p-back', view).addEventListener('click', () => { S.person = null; go('staff'); });
  PERSON_TAB[tab]($('#p-body', view), p, () => personView(view, ext));
  $$('[data-ptab]', view).forEach(b => b.addEventListener('click', () => { S.personTab = b.dataset.ptab; S.memIdx = 0; personView(view, ext); }));
}

const kitchenNote = p => p.kitchen ? '<div class="panel muted">Звонки этого сотрудника бот разбирает по отдельному скрипту (кухни), поэтому разговоров, результатов и ошибок здесь нет. Наборы, перезвоны и тренировки показываются как у всех.</div>' : '';

const exBtns = list => (list || []).map(c => `<button class="btn small" data-review="${c.id}">${esc(c.at ? when(c.at) : 'звонок ' + c.id)}</button>`).join('');
const RESULT_CHIP = {'получилось': 'good', 'частично': 'warn', 'не получилось': 'bad'};

function memoryCard(c, latest) {
  const weak = c.weak_spots || [], good = c.strengths || [];
  const counts = x => x.counts ? `<span class="muted">за 30 дн. ${x.counts.month} · за 7 дн. ${x.counts.week} · неделей раньше ${x.counts.prev}</span>` : '';
  return `${c.focus || c.trend ? `<div class="panel mem-focus">
      ${c.focus ? `<div class="mem-label">Фокус на ближайшие дни</div><div class="mem-big">${esc(c.focus)}</div>` : ''}
      ${c.previous_focus ? `<p class="muted">Прошлый фокус: «${esc(c.previous_focus)}»${c.focus_result ? ` <span class="chip ${RESULT_CHIP[c.focus_result] || ''}">${esc(c.focus_result)}</span>` : ''}</p>` : ''}
      ${c.trend ? `<p>${esc(c.trend)}</p>` : ''}</div>` : ''}
    <div class="grid2"><div class="panel"><h2>Слабые места <span class="muted">· ${weak.length}</span></h2>
      ${weak.length ? weak.map((x, i) => `<div class="mem-item">
        <div class="mem-title"><b>${i + 1}. ${esc(x.title)}</b> ${counts(x)}</div>
        ${x.pattern ? `<p>${esc(x.pattern)}</p>` : ''}
        ${x.advice ? `<p class="mem-say">Как лучше: ${esc(x.advice)}</p>` : ''}
        ${x.examples.length ? `<div class="p-ex">${exBtns(x.examples)}</div>` : ''}</div>`).join('') : '<div class="empty">Слабых мест бот не выделил.</div>'}</div>
    <div class="panel"><h2>Что получается</h2>${good.length ? good.map(x => `<div class="p-pos"><span>${esc(x.title)}</span> ${exBtns(x.examples)}</div>`).join('')
      : '<div class="empty">Пока не отмечено.</div>'}
      <p class="muted">Карточка от ${esc(ddmm(c.day))}${latest ? '' : ' (старая версия)'} по ${c.talks} ${plural(c.talks, 'разговору', 'разговорам', 'разговорам')} за 30 дней. Кнопки с датой открывают разбор и запись звонка.</p></div></div>`;
}

const PERSON_TAB = {
  memory(el, p) {
    if (p.kitchen) { el.innerHTML = kitchenNote(p); return; }
    const cards = p.memory || [];
    const pick = Math.min(S.memIdx || 0, Math.max(cards.length - 1, 0));
    if (!cards.length) {
      el.innerHTML = `<div class="panel empty">${p.memory_on ? `Бот пишет карточку памяти каждый день после ${p.memory_hour}:00, если у менеджера были новые разборы. Первая появится сегодня вечером.`
        : 'Память включится после выкладки: бот будет писать карточку каждый день после 19:00.'}<br>Пока можно смотреть частые ошибки по цифрам на вкладке «Обзор».</div>`;
      return;
    }
    el.innerHTML = memoryCard(cards[pick], pick === 0) + (cards.length > 1 ? `<div class="panel"><h2>История памяти <span class="muted">· ${cards.length}</span></h2><div class="scroll"><table class="tbl"><tbody>
      ${cards.map((c, i) => `<tr class="click${i === pick ? ' mem-cur' : ''}" data-mem="${i}"><td>${esc(ddmm(c.day))}</td><td>${esc(c.focus || c.trend || '')}</td>
        <td>${c.focus_result ? `<span class="chip ${RESULT_CHIP[c.focus_result] || ''}">${esc(c.focus_result)}</span>` : ''}</td></tr>`).join('')}</tbody></table></div></div>` : '');
    $$('[data-mem]', el).forEach(tr => tr.addEventListener('click', () => { S.memIdx = +tr.dataset.mem; PERSON_TAB.memory(el, p); window.scrollTo(0, 0); }));
  },
  over(el, p) {
    if (p.kitchen) {
      const d = p.dials;
      el.innerHTML = `${kitchenNote(p)}<div class="kpis">${[['week', '7 дней'], ['month', '30 дней']].map(([k, t]) =>
        `<div class="kpi"><b>${d[k].dials}</b><span>наборов за ${t} · дозвон ${pctTxt(d[k].answered, d[k].dials)}</span></div>`).join('')}</div>`;
      return;
    }
    const w = p.periods.week, pr = p.periods.prev, m = p.periods.month, dw = p.dials.week;
    const max = Math.max(1, ...p.days.map(d => d.talks));
    const outs = Object.entries(m.outcomes).sort((a, b) => b[1] - a[1]);
    const mem = (p.memory || [])[0];
    el.innerHTML = `${mem && mem.focus ? `<div class="panel mem-focus row"><span><span class="mem-label">Фокус от бота · ${esc(ddmm(mem.day))}</span> ${esc(mem.focus)}</span>
        <button class="btn small" data-ptab="memory">Слабые места →</button></div>` : ''}<div class="kpis">
        <div class="kpi"><b>${dw.dials}</b><span>наборов за 7 дней · дозвон ${pctTxt(dw.answered, dw.dials)} · на линии ${hm(dw.seconds)}</span></div>
        <div class="kpi"><b>${w.talks}</b><span>разговоров разобрано · прошлая неделя ${pr.talks}</span></div>
        <div class="kpi"><b>${w.success} <small>${pctTxt(w.success, w.talks)}</small></b><span>с результатом ${trend(w, pr) || ''} · прошлая неделя ${pr.success} (${pctTxt(pr.success, pr.talks)})</span></div>
        <div class="kpi"><b>${w.hot}</b><span>горячих потерь за 7 дней</span></div></div>
      <div class="grid2"><div class="stack">
        <div class="panel"><h2>Частые ошибки за 30 дней <span class="muted">· потеряно ${m.lost} из ${m.talks} разговоров</span></h2>
          ${p.mistakes.length ? `<div class="scroll"><table class="tbl"><thead><tr><th>Где терялся результат</th><th class="n">30 дн.</th><th class="n">7 дн.</th><th class="n hide-m">Пред. 7 дн.</th></tr></thead><tbody>
          ${p.mistakes.map(x => `<tr><td>${esc(x.label)}${x.examples.length ? `<div class="p-ex">${x.examples.map(c =>
              `<button class="btn small" data-review="${c.id}" title="${esc(c.what || '')}">${esc(when(c.at))}</button>`).join('')}</div>` : ''}</td>
            <td class="n">${x.month}</td><td class="n">${x.week}</td><td class="n hide-m">${x.prev}</td></tr>`).join('')}</tbody></table></div>
          <p class="muted">Кнопки с датой открывают пример: разбор и запись звонка.</p>` : '<div class="empty">Потерь с понятной причиной за 30 дней нет.</div>'}</div>
        <div class="panel"><h2>Разговоры по дням</h2>${m.talks ? `<div class="bars p-days">${p.days.map(d =>
            `<div class="bar" title="${ddmm(d.day)}: разговоров ${d.talks}, с результатом ${d.success}, наборов ${d.dials}"><div class="stackbar"><i data-h="${d.success / max * 100}"></i><i class="b2" data-h="${(d.talks - d.success) / max * 100}"></i></div><span>${ddmm(d.day).slice(0, 2)}</span></div>`).join('')}</div>
          <p class="muted">Зелёным разговоры с результатом, серым без результата, за 30 дней.</p>` : '<div class="empty">Разобранных разговоров за 30 дней нет.</div>'}</div></div>
      <div class="stack">
        <div class="panel"><h2>Результаты за 30 дней <span class="muted">· ${m.success} из ${m.talks}</span></h2>${outs.length ? `<table class="tbl"><tbody>${outs.map(([k, n]) =>
            `<tr><td>${esc(p.outcome_labels[k] || k)}</td><td class="n">${n}</td></tr>`).join('')}</tbody></table>` : '<div class="empty">Пока нет.</div>'}
          <button class="btn small" data-deals-of="${esc(p.ext)}">${S.me.role === 'director' ? 'Его сделки' : 'Мои сделки'} →</button></div>
        <div class="panel"><h2>Что получается</h2>${p.positives.length ? `<div class="list">${p.positives.map(x =>
            `<div class="p-pos"><span>${esc(x.why)}</span> <button class="btn small" data-review="${x.id}">${esc(when(x.at))}</button></div>`).join('')}</div>` : '<div class="empty">Пока нет отмеченных удачных мест.</div>'}</div>
      </div></div>`;
    heights(el);
  },
  calls(el, p) {
    const f = S.personCalls || 'all';
    const shown = p.calls.filter(c => f === 'all' || (f === 'good' ? c.success : f === 'lost' ? !c.success && c.chance !== 'none' : c.category === f));
    const cats = p.mistakes.map(x => [x.key, x.label]);
    el.innerHTML = `${kitchenNote(p)}<div class="panel"><h2>Разобранные разговоры за 30 дней <span class="muted">· ${p.calls.length}${p.calls.length >= 60 ? ', последние' : ''}</span></h2>
      <div class="row gapb"><div class="filters">${[['all', 'Все'], ['good', 'С результатом'], ['lost', 'Потеряны']].map(([k, t]) =>
        `<button data-cf="${k}" aria-pressed="${f === k}">${t}</button>`).join('')}</div>
        ${cats.length ? `<label class="field"><select id="p-cat" aria-label="Ошибка"><option value="">Любая ошибка</option>${cats.map(([k, t]) =>
          `<option value="${esc(k)}"${f === k ? ' selected' : ''}>${esc(t)}</option>`).join('')}</select></label>` : ''}</div>
      ${shown.length ? `<div class="scroll"><table class="tbl"><thead><tr><th>Когда</th><th>Номер</th><th class="n">Длит.</th><th>Итог</th><th></th></tr></thead><tbody>
      ${shown.map(c => `<tr><td>${esc(when(c.at))}</td><td class="num">${esc(phone(c.number))}</td><td class="n">${dur(c.seconds)}</td>
        <td><span class="chip ${c.success ? 'good' : c.chance === 'hot' ? 'bad' : c.chance === 'normal' ? 'warn' : ''}">${c.success ? 'результат' : c.chance === 'hot' ? 'горячий, потерян' : c.chance === 'normal' ? 'потерян' : 'без шанса'}</span>
          ${c.verdict ? esc(c.verdict) : ''}${c.rop_line ? `<div class="muted">${esc(c.rop_line)}</div>` : ''}</td>
        <td><button class="btn small" data-review="${c.id}">Разбор${c.rec ? ' и запись' : ''}</button></td></tr>`).join('')}</tbody></table></div>`
        : '<div class="empty">Таких разговоров нет.</div>'}</div>`;
    $$('[data-cf]', el).forEach(b => b.addEventListener('click', () => { S.personCalls = b.dataset.cf; PERSON_TAB.calls(el, p); }));
    $('#p-cat', el)?.addEventListener('change', e => { S.personCalls = e.target.value || 'all'; PERSON_TAB.calls(el, p); });
  },
  train(el, p, reload) {
    const t = p.trainings, top = p.mistakes[0];
    const canAssign = S.me.role === 'director' && !S.me.test;
    el.innerHTML = `<div class="stack">
      ${canAssign ? `<div class="panel"><h2>Назначить тренировку</h2>
        <p class="muted">Менеджеру придёт сообщение в боте с кнопкой старта. ${top ? `Самая частая ошибка за 30 дней: «${esc(top.label)}».` : ''}</p>
        <div class="row">${top ? `<button class="btn primary" id="p-train-top">Отработать «${esc(top.label)}»</button>` : ''}
          <button class="btn" id="p-train-dialog">Разговор целиком</button></div><div class="muted" id="p-train-out"></div></div>` : ''}
      ${t.pending.length ? `<div class="panel"><h2>Назначено, ещё не начато <span class="muted">· ${t.pending.length}</span></h2><div class="list">
        ${t.pending.map(x => `<div class="row gapb"><span>${esc(x.mode)}${x.topic ? ' · ' + esc(x.topic) : ''}</span>
          ${x.link ? `<a class="btn small primary" href="${esc(x.link)}" target="_blank" rel="noopener">Начать в тренажёре</a>` : ''}</div>`).join('')}</div></div>` : ''}
      <div class="panel"><h2>Пройденные тренировки <span class="muted">· ${t.items.length}</span></h2>${t.items.length ? `<div class="scroll"><table class="tbl">
        <thead><tr><th>Когда</th><th>Режим</th><th>Тема</th><th>Итог</th></tr></thead><tbody>
        ${t.items.map(x => `<tr class="click" data-tid="${x.id}"><td>${fmtWhen(x.at)}</td><td>${esc(x.mode)}</td><td>${esc(x.topic || '')}</td><td>${esc(x.result)}${x.test ? ' <span class="muted">· пробная</span>' : ''}</td></tr>`).join('')}
        </tbody></table></div>` : '<div class="empty">Тренировок пока не было.</div>'}</div></div>`;
    $$('tr[data-tid]', el).forEach(tr => tr.addEventListener('click', () => trainingDetail(tr.dataset.tid)));
    const assign = async (mode, topic) => {
      const out = $('#p-train-out', el);
      try {
        await api('/api/trainings', {method: 'POST', scoped: false, body: {person_ext: p.ext, mode, topic}});
        out.textContent = 'Назначено.'; setTimeout(reload, 700);
      } catch (e) { out.textContent = e.message; }
    };
    $('#p-train-top', el)?.addEventListener('click', () => assign('drill', top.label));
    $('#p-train-dialog', el)?.addEventListener('click', () => assign('dialog', ''));
  },
  tasks(el, p) {
    const late = p.tasks.filter(t => t.late).length;
    el.innerHTML = `<div class="panel"><h2>Открытые перезвоны и встречи <span class="muted">· ${p.tasks.length}${late ? `, просрочено ${late}` : ''}</span></h2>
      ${p.tasks.length ? `<div class="scroll"><table class="tbl"><thead><tr><th>Когда</th><th>Что</th><th>Номер</th><th>Клиент</th><th class="hide-m">Договорённость</th></tr></thead><tbody>
      ${p.tasks.map(t => `<tr><td><span class="chip ${t.late ? 'late' : t.due_date === p.today ? 'today' : ''}">${esc(ddmm(t.due_date))}${t.due_time ? ' ' + esc(t.due_time) : ''}${t.late ? ' · просрочен' : ''}</span></td>
        <td>${esc(t.label)}</td><td class="num">${esc(phone(t.number))}</td><td>${esc(t.title)}</td><td class="hide-m">${esc(t.note)}</td></tr>`).join('')}</tbody></table></div>`
        : '<div class="empty">Открытых перезвонов нет.</div>'}</div>`;
  },
  access(el, p, reload) {
    const a = p.access;
    el.innerHTML = `<div class="panel"><h2>Telegram-бот</h2>
        <p>${a.linked ? `Подключён${a.username ? ' как @' + esc(a.username) : ''}. Получает разборы и уведомления.` : a.invite_until ? `Ждёт подключения по ссылке до ${fmtWhen(a.invite_until)}.` : 'Не подключён.'}</p>
        ${a.reviewed === null ? '' : `<p class="muted">Звонки ${a.reviewed ? 'разбираются ботом' : 'не разбираются ботом'}.</p>`}
        <div class="row">${a.linked ? '' : '<button class="btn" id="p-inv">Ссылка в бота</button>'}
          ${a.can_unlink ? '<button class="btn" id="p-unlink">Отключить от бота</button>' : ''}</div><div id="p-inv-out"></div></div>
      ${a.can_copy ? `<div class="panel"><h2>Копии всех разборов</h2>
        <label class="row"><input type="checkbox" id="p-copies"${a.copies ? ' checked' : ''}> Присылать полные разборы всех менеджеров в Telegram</label>
        <p class="muted">Включается только руководителям: там звонки всех менеджеров.</p></div>` : ''}
      <div class="panel"><h2>Вход в CRM</h2><p>${a.crm_links ? `Личных ссылок: ${a.crm_links}. Последний вход: ${a.crm_used ? fmtWhen(a.crm_used) : 'ещё не входил'}.` : 'Личной ссылки нет.'}</p>
        <button class="btn" id="p-access">Открыть «Доступы»</button></div>`;
    $('#p-access', el).addEventListener('click', () => go('access'));
    $('#p-inv', el)?.addEventListener('click', async () => {
      const out = $('#p-inv-out', el);
      try { const r = await api('/api/staff/invite', {method: 'POST', scoped: false, body: {person_ext: p.ext}}); linkBox(out, r.link, 'Ссылка одноразовая, действует 7 дней.'); }
      catch (e) { out.textContent = e.message; }
    });
    $('#p-unlink', el)?.addEventListener('click', async () => {
      if (!confirm(`Отключить ${p.name} от бота? Сообщения бота ему больше не придут. Вход в CRM по его личной ссылке закрывается в «Доступах».`)) return;
      try { await api('/api/staff/unlink', {method: 'POST', scoped: false, body: {person_ext: p.ext}}); reload(); }
      catch (e) { alert(e.message); }
    });
    $('#p-copies', el)?.addEventListener('change', async e => {
      try { await api('/api/staff/copies', {method: 'POST', scoped: false, body: {person_ext: p.ext, on: e.target.checked}}); reload(); }
      catch (err) { alert(err.message); e.target.checked = !e.target.checked; }
    });
  },
};

// Режим промта: черновик живёт на странице, сохраняется только текст, по которому собран пример.
VIEWS.prompts = async view => {
  const o = await api('/api/prompts', {scoped: false});
  const P = S.pm && o.kinds.some(k => k.kind === S.pm.kind) ? S.pm : (S.pm = {kind: o.kinds[0].kind});
  const st = await api('/api/prompts/' + P.kind, {scoped: false});
  if (P.start === undefined || P.loadedFor !== P.kind) Object.assign(P, {start: st.body, body: st.body, history: [], note: '', token: null, tokenBody: null, preview: null, diff: [], loadedFor: P.kind});
  const changed = () => P.body !== P.start;
  const canSave = () => changed() && P.token && P.tokenBody === P.body;
  const diffHtml = lines => lines.length ? `<div class="pm-diff">${lines.map(l => `<div class="${l.op === '+' ? 'add' : 'del'}">${l.op === '+' ? '+' : '−'} ${esc(l.text)}</div>`).join('')}</div>` : '';
  const render = () => {
    view.innerHTML = `<div class="filters">${o.kinds.map(k => `<button data-kind="${k.kind}" aria-pressed="${k.kind === P.kind}">${esc(k.title)}</button>`).join('')}</div>
    <div class="grid2"><div class="panel">
      <h2>${esc(st.title)}</h2>
      <p class="muted">Сейчас у вас действует ${esc(st.source)}${st.version ? ' (сохранение #' + st.version + ')' : ''}. Правки действуют и в боте.${st.hint ? ' ' + esc(st.hint) : ''}</p>
      ${P.history.map(h => `<div class="pm-qa"><div class="q">${esc(h.ask)}</div><div class="muted">${esc(h.reply)}</div></div>`).join('')}
      ${diffHtml(P.diff)}
      <textarea id="pm-ask" rows="2" maxlength="2000" placeholder="Что поменять, обычными словами: например, «пиши короче и добавь, сколько звонков у каждого менеджера»"></textarea>
      <div class="row gapb"><button class="btn primary" id="pm-edit">Поправить</button><span class="muted" id="pm-st"></span></div>
      <details class="pm-text"${P.open ? ' open' : ''}><summary>Текст промта${changed() ? ' · изменён' : ''}</summary>
        <textarea id="pm-body" rows="16" maxlength="20000">${esc(P.body)}</textarea></details>
      <div class="row gapb">
        <button class="btn" id="pm-prev"${changed() ? '' : ' disabled'}>Пример по новому тексту</button>
        <button class="btn" id="pm-undo"${changed() ? '' : ' disabled'}>Отменить правки</button>
        <button class="btn primary" data-save="me"${canSave() ? '' : ' disabled'}>Сохранить для себя</button>
        <button class="btn primary" data-save="all"${canSave() ? '' : ' disabled'}>Сохранить для всех</button></div>
      ${changed() && !canSave() ? '<p class="muted">Сохранить можно после примера по этому тексту.</p>' : ''}
      ${P.preview ? `<div class="panel report">${P.preview.problem ? `<p><b>Пример не получился:</b> ${esc(P.preview.problem)}. Если сохранить так, при сбое бот пришлёт причину и стандартную версию. Лучше поправить текст ещё раз.</p>`
        : `<div class="rep-head"><b>${P.kind === 'review' ? 'Пример разбора последнего звонка по новому тексту' : 'Пример отчёта по новому тексту'}</b>${P.preview.call_id ? `<button class="btn small" data-review="${P.preview.call_id}">Как было</button>` : ''}</div><div class="rep-body">${P.preview.html}</div>`}</div>` : ''}
    </div>
    <div class="panel"><h2>Сохранения</h2>
      <p class="muted">✅ действует для вас, 🌐 для всех. Если 🌐 нет ни у одного, для всех действует исходная.</p>
      ${st.personal ? '<button class="btn small" id="pm-common">Убрать личную, получать общую</button>' : ''}
      <div class="pm-vers">${st.versions.map(v => `<div class="pm-ver"><div><b>${v.mine ? '✅ ' : ''}${v.common ? '🌐 ' : ''}#${v.id}</b> · ${fmtWhen(v.at)} · ${esc(v.scope)} · ${esc(v.who)}</div>
        ${v.note ? `<div class="muted">${esc(v.note)}</div>` : ''}
        <div class="row"><button class="btn small" data-vt="${v.id}">Текст</button><button class="btn small" data-use="${v.id}" data-scope="me">Вернуть для себя</button><button class="btn small" data-use="${v.id}" data-scope="all">Вернуть для всех</button></div></div>`).join('')}</div></div></div>`;
    bind();
  };
  const reload = () => { P.loadedFor = null; go('prompts'); };
  const bind = () => {
    $$('[data-kind]', view).forEach(b => b.addEventListener('click', () => {
      if (b.dataset.kind === P.kind || (changed() && !confirm('Несохранённые правки пропадут. Перейти?'))) return;
      S.pm = {kind: b.dataset.kind}; go('prompts');
    }));
    const body = $('#pm-body', view);
    body.addEventListener('input', () => { P.body = body.value; P.open = true; });
    body.addEventListener('change', render);
    $('details.pm-text', view).addEventListener('toggle', e => { P.open = e.target.open; });
    $('#pm-edit', view).addEventListener('click', async () => {
      const ask = $('#pm-ask', view).value, btn = $('#pm-edit', view);
      btn.disabled = true; $('#pm-st', view).textContent = ' Правлю текст…';
      try {
        const r = await api(`/api/prompts/${P.kind}/edit`, {method: 'POST', scoped: false, body: {body: P.body, history: P.history, ask}});
        P.history.push({ask, reply: r.reply});
        P.diff = r.diff;
        if (r.prompt) { P.body = r.prompt; P.note = r.changes || ask; P.preview = null; }
        render();
      } catch (e) { $('#pm-st', view).textContent = ' ' + e.message; btn.disabled = false; }
    });
    $('#pm-undo', view).addEventListener('click', () => { Object.assign(P, {body: P.start, diff: [], note: '', preview: null, token: null}); render(); });
    $('#pm-prev', view).addEventListener('click', async () => {
      const btn = $('#pm-prev', view); btn.disabled = true; btn.textContent = 'Собираю пример, до минуты…';
      const text = P.body;
      try {
        const r = await api(`/api/prompts/${P.kind}/preview`, {method: 'POST', scoped: false, body: {body: text}});
        Object.assign(P, {preview: r, token: r.token, tokenBody: text});
      } catch (e) { P.preview = {problem: e.message}; }
      render();
    });
    $$('[data-save]', view).forEach(b => b.addEventListener('click', async () => {
      if (b.dataset.save === 'all' && !confirm(P.kind === 'review' ? 'Сохранить для всех? Так будут разбираться все звонки, в том числе менеджерам.' : 'Сохранить для всех?')) return;
      try {
        const r = await api(`/api/prompts/${P.kind}/save`, {method: 'POST', scoped: false, body: {body: P.body, note: P.note, scope: b.dataset.save, token: P.token}});
        toast(`Сохранено ${b.dataset.save === 'all' ? 'для всех' : 'для вас'}: сохранение #${r.version}.`);
        reload();
      } catch (e) { alert(e.message); }
    }));
    $('#pm-common', view)?.addEventListener('click', async () => {
      try { await api(`/api/prompts/${P.kind}/personal-off`, {method: 'POST', scoped: false, body: {}}); reload(); } catch (e) { alert(e.message); }
    });
    $$('[data-vt]', view).forEach(b => b.addEventListener('click', async () => {
      try {
        const v = await api('/api/prompts/version/' + b.dataset.vt, {scoped: false});
        modal(`<header class="rvm-head"><div><h2>Сохранение #${v.id}</h2><div class="rvm-mut">${esc(v.note)}</div></div><button class="rvm-x" aria-label="Закрыть">×</button></header>
          <div class="rvm-body"><div class="rvm-pre">${esc(v.body)}</div></div>`);
      } catch (e) { alert(e.message); }
    }));
    $$('[data-use]', view).forEach(b => b.addEventListener('click', async () => {
      const all = b.dataset.scope === 'all';
      if (!confirm(`Вернуть сохранение #${b.dataset.use} ${all ? 'для всех' : 'для себя'}?`)) return;
      try { await api(`/api/prompts/version/${b.dataset.use}/use`, {method: 'POST', scoped: false, body: {scope: b.dataset.scope}}); toast('Готово.'); reload(); }
      catch (e) { alert(e.message); }
    }));
  };
  render();
};

// Голосовой робот: звонок роботом, голос и манера речи (как страница /voice робота), итоги звонков.
const ROBOT_SLIDERS = [
  ['speed', 'Скорость речи', 0.7, 1.2, 0.01, '1.0 обычно, 1.1 заметно быстрее, максимум 1.2'],
  ['stability', 'Стабильность', 0, 1, 0.05, 'выше: ровнее и одинаковее от фразы к фразе; ниже: живее, но интонация гуляет'],
  ['similarity', 'Схожесть с голосом', 0, 1, 0.05, ''],
  ['style', 'Выразительность', 0, 1, 0.05, '0 спокойно; больше: эмоциональнее и чуть медленнее синтез'],
];
const ROBOT_MANNER = [
  ['casual', 'Разговорность', 0, 1, 0.05, '0 как диктор; выше: «пожалста», «щас», иногда «ну»'],
  ['bg_noise', 'Фон на линии', 0, 1, 0.05, 'тихий шум помещения весь звонок, чтобы в паузах линия не «пропадала»; 0 выключает, слышно только в звонке'],
  ['tempo_vary', 'Разный темп', 0, 0.3, 0.01, 'вопрос медленнее, длинное утверждение быстрее; 0.1 — на 10 %, 0 выключает'],
  ['pause_ms', 'Пауза между фразами, мс', 0, 600, 10, 'тишина после каждого предложения'],
  ['inner_pause_ms', 'Паузы на знаках, мс', 40, 600, 10, 'самая длинная пауза внутри фразы, всё длиннее укорачивается'],
  ['tail_ms', 'Конец реплики клиента, мс', 600, 2000, 50, 'сколько тишины ждать перед ответом; меньше: быстрее, но может перебить'],
  ['filler_after_ms', '«Так…» через, мс', 100, 3000, 50, 'когда сказать «Так…», если модель думает дольше'],
];
const ROBOT_CHECKS = [
  ['speaker_boost', 'Усиление сходства с голосом'],
  ['calm', 'Спокойный тон: «!» читать как точку, «отлично» как «хорошо»'],
  ['whole_reply', 'Реплика на одном дыхании: озвучивать ответ целиком (ровнее интонация, ответ на 0,3–0,6 с позже)'],
  ['one_breath', 'Фраза на одном дыхании: без пауз на запятых, двоеточиях и тире внутри предложения (не звучит как стих)'],
  ['fewer_commas', 'Меньше пауз на запятых'],
  ['fillers', '«Так…», «Угу…», пока модель думает; когда клиент назвал данные — «Так, записываю…»'],
  ['backchannel', '«Угу», «Ага» по ходу долгого рассказа клиента'],
  ['goodbye_wait', 'Ответить «Всего доброго» на прощание клиента, а не класть трубку сразу'],
];
const ROBOT_MODELS = [['eleven_v4', 'eleven_v4 (самая живая)'], ['eleven_multilingual_v2', 'eleven_multilingual_v2'],
  ['eleven_flash_v2_5', 'eleven_flash_v2_5 (самая быстрая)'], ['eleven_turbo_v2_5', 'eleven_turbo_v2_5']];
let robotAudio = null;

function robotCall(c) {
  modal(`<header class="rvm-head"><div><span class="rvm-chip ${c.good ? 'good' : ''}">${esc(c.outcome)}${c.transfer ? ' · перевод' : ''}</span>
      <h2>Робот · ${esc(phone(c.phone))}</h2><div class="rvm-mut">${fmtWhen(c.at)}${c.seconds ? ' · ' + dur(c.seconds) : ''}</div></div>
      <button class="rvm-x" aria-label="Закрыть">×</button></header>
    <div class="rvm-body">${c.comment ? `<section><h3>Итог</h3><div>${esc(c.comment)}</div></section>` : ''}
      ${Object.keys(c.facts).length ? `<section><h3>Что узнал</h3><dl class="rvm-kv">${Object.entries(c.facts).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl></section>` : ''}
      <section><h3>Разговор</h3>${c.transcript.length ? c.transcript.map(l => `<div class="rvm-line"><b>${esc(l.who)}</b> ${esc(l.text)}</div>`).join('') : '<div class="rvm-mut">Разговора не было.</div>'}</section></div>`);
}

VIEWS.robot = async view => {
  const [d, log] = await Promise.all([api('/api/robot', {scoped: false}), api('/api/robot/calls', {scoped: false})]);
  const cfg = {...d.settings};
  const slider = ([id, label, min, max, step, hint]) => `<div class="rb-row"><label for="rb-${id}">${label}</label>
    <input type="range" id="rb-${id}" data-k="${id}" min="${min}" max="${max}" step="${step}" value="${cfg[id]}"><output id="rb-${id}-v"></output>
    ${hint ? `<div class="muted">${hint}</div>` : ''}</div>`;
  const models = ROBOT_MODELS.some(([m]) => m === cfg.model) ? ROBOT_MODELS : [...ROBOT_MODELS, [cfg.model, cfg.model]];
  const ro = d.readonly ? ' disabled' : '';
  view.innerHTML = `<div class="grid2"><div class="rb-col">
    <div class="panel"><h2>Позвонить роботом</h2>
      <p class="muted">${d.up ? '<span class="chip good">робот включён</span>' : '<span class="chip bad">робот выключен</span> Включается на сервере: <code>bash scripts/voice_test.sh sip</code>'}
        Робот звонит с текущими сохранёнными настройками голоса. Звоните только тем, кто согласился на звонок.</p>
      <div class="outform">
        <label class="field">Номер<input id="rb-phone" inputmode="tel" placeholder="+7 999 123-45-67"></label>
        <label class="field">Сценарий<select id="rb-op">${d.operators.map(o => `<option value="${esc(o.id)}">${esc(o.name)} · ${esc(o.company)}</option>`).join('')}</select></label>
        <button class="btn primary" id="rb-call"${ro}>Позвонить роботом</button>
        ${d.test_phones.length ? `<button class="btn" id="rb-me"${ro}>Позвонить мне</button>` : ''}</div>
      <div class="muted" id="rb-call-st"></div></div>
    <div class="panel"><h2>Голос</h2>
      <div class="rb-row"><label for="rb-voice">Голос из аккаунта ElevenLabs</label><select id="rb-voice"><option value="">— указать voice_id —</option>
        ${Object.entries(d.voices || {}).map(([name, id]) => `<option value="${esc(id)}"${id === cfg.voice_id ? ' selected' : ''}>${esc(name)}</option>`).join('')}</select></div>
      <div class="rb-row"><label for="rb-voice_id">voice_id</label><input id="rb-voice_id" value="${esc(cfg.voice_id)}" autocomplete="off"></div>
      <div class="rb-row"><label for="rb-model">Модель</label><select id="rb-model">${models.map(([m, t]) => `<option value="${esc(m)}"${m === cfg.model ? ' selected' : ''}>${esc(t)}</option>`).join('')}</select></div>
      ${ROBOT_SLIDERS.map(slider).join('')}</div>
    <div class="panel"><h2>Подобрать голос</h2>
      <p class="muted">Русские голоса из библиотеки ElevenLabs. «Слушать» играет образец автора голоса, «Взять» добавляет голос в аккаунт и подставляет его выше; потом «Прослушать» и «Сохранить для звонков».</p>
      <div class="outform"><label class="field">Поиск<input id="rb-vq" placeholder="например calm, office, Анна" autocomplete="off"></label>
        <label class="field">Пол<select id="rb-vg"><option value="female">женский</option><option value="male">мужской</option><option value="">любой</option></select></label>
        <label class="field">Тип<select id="rb-vu"><option value="conversational">разговорный</option><option value="">любой</option></select></label>
        <button class="btn" id="rb-vfind">Найти</button></div>
      <div id="rb-vres"></div></div>
    <div class="panel"><h2>Клон голоса</h2>
      <p class="muted">Голос живого менеджера по его записям: 1–3 минуты чистой речи одного человека, без голоса клиента и музыки. Только с согласия этого человека.</p>
      <div class="outform"><label class="field">Название<input id="rb-cname" value="Ольга" autocomplete="off"></label>
        <label class="field">Записи (mp3, wav, m4a)<input type="file" id="rb-cfiles" accept="audio/*" multiple></label>
        <button class="btn" id="rb-clone"${ro}>Сделать клон</button></div>
      <div class="muted" id="rb-cst"></div></div>
    <div class="panel"><h2>Манера и темп разговора</h2>
      ${ROBOT_MANNER.map(slider).join('')}
      ${ROBOT_CHECKS.map(([id, label]) => `<label class="rb-check"><input type="checkbox" data-c="${id}"${cfg[id] ? ' checked' : ''}> ${label}</label>`).join('')}</div>
    <div class="panel"><h2>Проверка</h2>
      <textarea id="rb-text" rows="3">Здравствуйте. Меня зовут Мария, я голосовой помощник, кухни на заказ. Вы недавно интересовались кухней, подскажите, вопрос ещё актуален?</textarea>
      <div class="row"><button class="btn" id="rb-listen">Прослушать</button>
        <button class="btn primary" id="rb-save"${d.can_save && !d.readonly ? '' : ' disabled'}>Сохранить для звонков</button>
        <button class="btn" id="rb-reset">Вернуть сохранённое</button></div>
      <div class="muted" id="rb-st">${d.can_save ? 'Сохранённые настройки действуют со следующего звонка, без перезапуска.' : 'Робот не установлен на этом сервере: сохранить некуда.'}</div></div>
    </div>
    <div class="panel"><h2>Звонки робота <span class="muted">· ${log.items.length}</span></h2>
      ${log.items.length ? `<div class="scroll"><table class="tbl"><thead><tr><th>Когда</th><th>Номер</th><th>Итог</th><th class="n">Длит.</th></tr></thead><tbody>
        ${log.items.map((c, i) => `<tr class="click" data-rc="${i}"><td>${fmtWhen(c.at)}</td><td>${esc(phone(c.phone))}</td>
          <td><span class="chip ${c.good ? 'good' : ''}">${esc(c.outcome)}</span>${c.comment ? `<div class="muted">${esc(c.comment)}</div>` : ''}</td><td class="n">${c.seconds ? dur(c.seconds) : ''}</td></tr>`).join('')}</tbody></table></div>`
        : '<div class="empty">Звонков по телефону ещё не было.</div>'}</div></div>`;
  const show = el => { const v = Number(el.value); $(`#${el.id}-v`, view).textContent = el.dataset.k.endsWith('_ms') ? v : v.toFixed(2); };
  $$('[data-k]', view).forEach(el => { show(el); el.addEventListener('input', () => show(el)); });
  const voiceSel = $('#rb-voice', view), voiceId = $('#rb-voice_id', view);
  voiceSel.addEventListener('change', () => { if (voiceSel.value) voiceId.value = voiceSel.value; });
  voiceId.addEventListener('input', () => { voiceSel.value = [...voiceSel.options].some(o => o.value === voiceId.value) ? voiceId.value : ''; });
  const collect = () => {
    const out = {voice_id: voiceId.value.trim(), model: $('#rb-model', view).value};
    $$('[data-k]', view).forEach(el => { out[el.dataset.k] = Number(el.value); });
    $$('[data-c]', view).forEach(el => { out[el.dataset.c] = el.checked; });
    return out;
  };
  const st = (text, err) => { const el = $('#rb-st', view); el.textContent = text; el.className = err ? 'muted bad-text' : 'muted'; };
  $('#rb-listen', view).addEventListener('click', async () => {
    const s = collect(), btn = $('#rb-listen', view);
    if (!s.voice_id) return st('Выберите голос или впишите voice_id.', true);
    robotAudio = robotAudio || new AudioContext(); await robotAudio.resume();
    btn.disabled = true; st('Синтезирую…');
    try {
      const r = await fetch('/api/robot/preview', {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json', 'X-CRM': '1'},
        body: JSON.stringify({text: $('#rb-text', view).value, settings: s})});
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'Синтез не получился.');
      const rate = Number(r.headers.get('X-Rate') || 48000), pcm = new Int16Array(await r.arrayBuffer());
      const buf = robotAudio.createBuffer(1, pcm.length, rate), ch = buf.getChannelData(0);
      for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 32768;
      const src = robotAudio.createBufferSource(); src.buffer = buf; src.connect(robotAudio.destination); src.start();
      st(`Фраза ${(pcm.length / rate).toFixed(1)} с. Это ещё не сохранено.`);
    } catch (e) { st(e.message, true); }
    btn.disabled = false;
  });
  $('#rb-save', view).addEventListener('click', async () => {
    try { await api('/api/robot/settings', {method: 'POST', scoped: false, body: collect()}); toast('Сохранено. Следующий звонок робота пойдёт с этими настройками.'); }
    catch (e) { st(e.message, true); }
  });
  $('#rb-reset', view).addEventListener('click', () => go('robot'));
  const useVoice = (id, name, voices) => {
    if (voices) voiceSel.innerHTML = '<option value="">— указать voice_id —</option>' + Object.entries(voices).map(([n, v]) => `<option value="${esc(v)}">${esc(n)}</option>`).join('');
    if (![...voiceSel.options].some(o => o.value === id)) voiceSel.insertAdjacentHTML('beforeend', `<option value="${esc(id)}">${esc(name)}</option>`);
    voiceSel.value = id; voiceId.value = id;
    st(`Голос «${name}» подставлен. Нажмите «Прослушать», затем «Сохранить для звонков».`);
  };
  let vPage = 0, vSample = null;
  const findVoices = async (more) => {
    const box = $('#rb-vres', view);
    vPage = more ? vPage + 1 : 0;
    if (!more) box.innerHTML = '<div class="muted">Ищу…</div>';
    try {
      const qs = new URLSearchParams({q: $('#rb-vq', view).value, gender: $('#rb-vg', view).value, use: $('#rb-vu', view).value, page: vPage});
      const r = await api('/api/robot/voices?' + qs, {scoped: false});
      const rows = r.items.map(v => `<div class="rb-voice"><div><b>${esc(v.name)}</b> <span class="muted">${esc([v.gender === 'female' ? 'жен.' : v.gender === 'male' ? 'муж.' : '', v.age, v.accent, v.descriptive, v.use_case].filter(Boolean).join(' · '))}${v.users ? ` · взяли ${v.users}` : ''}</span>
          ${v.description ? `<div class="muted">${esc(v.description)}</div>` : ''}</div>
        <div class="row">${v.preview_url ? `<button class="btn small" data-vs="${esc(v.preview_url)}">Слушать</button>` : ''}
          <button class="btn small primary" data-va="${esc(v.owner)}|${esc(v.voice_id)}" data-vn="${esc(v.name)}"${ro}>Взять</button></div></div>`).join('');
      if (!more) box.innerHTML = '';
      $('#rb-vmore', view)?.remove();
      box.insertAdjacentHTML('beforeend', (rows || (more ? '' : '<div class="empty">Ничего не нашлось.</div>')) + (r.more ? '<button class="btn small" id="rb-vmore">Ещё</button>' : ''));
      $('#rb-vmore', view)?.addEventListener('click', () => findVoices(true));
    } catch (e) { box.innerHTML = `<div class="muted bad-text">${esc(e.message)}</div>`; }
  };
  $('#rb-vfind', view).addEventListener('click', () => findVoices(false));
  $('#rb-vq', view).addEventListener('keydown', e => { if (e.key === 'Enter') findVoices(false); });
  $('#rb-vres', view).addEventListener('click', async e => {
    const play = e.target.closest('[data-vs]'), take = e.target.closest('[data-va]');
    if (play) {
      if (vSample) { vSample.pause(); URL.revokeObjectURL(vSample.src); }
      play.disabled = true;
      try {
        const r = await fetch('/api/robot/voices/sample?' + new URLSearchParams({url: play.dataset.vs}), {credentials: 'same-origin'});
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'Образец не загрузился.');
        vSample = new Audio(URL.createObjectURL(await r.blob())); await vSample.play();
      } catch (err) { st(err.message, true); }
      play.disabled = false;
    } else if (take) {
      const [owner, id] = take.dataset.va.split('|');
      take.disabled = true;
      try {
        const r = await api('/api/robot/voices/add', {method: 'POST', scoped: false, body: {owner, voice_id: id, name: take.dataset.vn}});
        useVoice(r.voice_id, take.dataset.vn, r.voices);
        take.textContent = 'Взят';
      } catch (err) { st(err.message, true); take.disabled = false; }
    }
  });
  $('#rb-clone', view).addEventListener('click', async () => {
    const files = $('#rb-cfiles', view).files, out = $('#rb-cst', view), name = $('#rb-cname', view).value.trim() || 'Клон';
    if (!files.length) return (out.textContent = 'Выберите файлы с записями голоса.');
    const form = new FormData();
    form.append('name', name);
    [...files].forEach(f => form.append('files', f, f.name));
    $('#rb-clone', view).disabled = true; out.textContent = 'Отправляю записи в ElevenLabs, это до минуты…';
    try {
      const r = await fetch('/api/robot/voices/clone', {method: 'POST', credentials: 'same-origin', headers: {'X-CRM': '1'}, body: form});
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.error || 'Клон не получился.');
      useVoice(data.voice_id, name, data.voices);
      out.textContent = `Клон «${name}» готов и подставлен в «Голос». Послушайте и сохраните.`;
    } catch (e) { out.textContent = e.message; }
    $('#rb-clone', view).disabled = false;
  });
  const dialNow = async number => {
    const out = $('#rb-call-st', view);
    out.textContent = 'Заказываю звонок…';
    try {
      const r = await api('/api/robot/call', {method: 'POST', scoped: false, body: {phone: number, operator: $('#rb-op', view).value}});
      out.textContent = `Робот звонит на ${phone(r.number)}. Итог появится в списке справа после разговора, обновите страницу.`;
    } catch (e) { out.textContent = e.message; }
  };
  $('#rb-call', view).addEventListener('click', () => {
    const n = $('#rb-phone', view).value;
    if (!n.trim()) return ($('#rb-call-st', view).textContent = 'Введите номер.');
    if (confirm(`Робот позвонит на ${n}. Звонить?`)) dialNow(n);
  });
  $('#rb-me', view)?.addEventListener('click', () => dialNow(d.test_phones[0]));
  $$('[data-rc]', view).forEach(tr => tr.addEventListener('click', () => robotCall(log.items[+tr.dataset.rc])));
};

// ---------- сделки: клиент от холодного звонка до оплаты ----------
const DEAL_SHOW = [['active', 'В работе'], ['stalled', 'Зависшие'], ['lost', 'Отказ'], ['paid', 'Оплачено'], ['all', 'Все']];
const DEAL_PERIODS = [['30', '30 дней'], ['90', '90 дней'], ['', 'Всё время']];
const TEMP = {hot: ['горячий', 'good'], warm: ['тёплый', 'warn'], cold: ['холодный', ''], lost: ['отказ', 'bad']};
const dealName = d => d.company || phone(d.number);
function dealStatus(d) {
  if (d.status === 'paid') return `<span class="chip good">оплачено${d.amount ? ' · ' + rub(d.amount) : ''}</span>`;
  if (d.status === 'lost') return `<span class="chip bad">отказ${d.lost.auto ? '' : ' · отмечен'}</span>`;
  if (d.stalled_days != null) return `<span class="chip late">стоит ${d.stalled_days} дн.</span>`;
  return '<span class="chip acc">в работе</span>';
}
function dealSteps(d) {
  return `<div class="deal-steps">${(S.dealStages || []).slice(1).map((st, i) => {
    const n = i + 2;
    const cls = d.stage_no > n || (d.stage_no === n && d.status === 'paid') ? 'done' : d.stage_no === n ? (d.status === 'lost' ? 'lost' : 'cur') : '';
    return `<span class="${cls}">${esc(st.name)}</span>`;
  }).join('')}</div>`;
}

VIEWS.deals = async view => {
  const director = S.me.role === 'director';
  const f = S.dealF || (S.dealF = {show: 'active', days: '90', person: ''});
  const q = new URLSearchParams({days: f.days, person: director ? f.person : ''});
  const d = await api('/api/deals?' + q, {scoped: false});
  S.dealStages = d.stages; S.dealReasons = d.reasons; S.canPay = d.can_pay;
  const list = d.deals.filter(x => f.show === 'all' || (f.show === 'stalled' ? x.stalled_days != null : x.status === f.show));
  const fn = d.funnel;
  const stageRows = fn.stages.map((st, i) => {
    const prev = i ? fn.stages[i - 1].reached : 0;
    const conv = i > 1 && prev ? Math.round(st.reached * 100 / prev) + '%' : '';
    return `<tr><td><b>${i + 1}. ${esc(st.name)}</b></td><td class="n">${st.reached}</td><td class="n muted hide-m">${conv}</td>
      <td>${st.lost ? `<span class="chip bad">${st.lost}</span> ${st.reasons.map(([r, n]) => `<span class="muted">${esc(r)} · ${n}</span>`).join('; ')}` : '<span class="muted">—</span>'}</td></tr>`;
  }).join('');
  view.innerHTML = `
    <div class="filters">${DEAL_SHOW.map(([k, t]) => `<button data-ds="${k}" aria-pressed="${f.show === k}">${t}${k === 'stalled' && fn.stalled ? ' · ' + fn.stalled : ''}</button>`).join('')}</div>
    <div class="filters">${DEAL_PERIODS.map(([k, t]) => `<button data-dd="${k}" aria-pressed="${f.days === k}">${t}</button>`).join('')}
      ${director ? `<label class="field"><select id="deal-person" aria-label="Менеджер"><option value="">Все менеджеры</option>${d.managers.map(m => `<option value="${esc(m.ext)}"${m.ext === f.person ? ' selected' : ''}>${esc(m.name)}</option>`).join('')}</select></label>` : ''}</div>
    <div class="kpis">
      <div class="kpi"><b>${fn.active}</b><span>в работе${fn.stalled ? `, из них ${fn.stalled} без движения` : ''}</span></div>
      <div class="kpi"><b>${fn.cold_results}</b><span>с результатом на холодном звонке из ${fn.stages[1].reached}</span></div>
      <div class="kpi"><b>${fn.paid}</b><span>оплачено${fn.paid_sum ? ' на ' + rub(fn.paid_sum) : ''}</span></div>
    </div>
    <div class="panel"><h2>Где отваливаются клиенты</h2><div class="scroll"><table class="tbl"><thead><tr><th>Этап</th><th class="n">Дошли</th><th class="n hide-m">Переход</th><th>Отказ на этапе и почему</th></tr></thead><tbody>${stageRows}</tbody></table></div>
      <p class="muted">Клиент — номер телефона. Звонки и презентации CRM разбирает сама; Telegram или почту, договор и отказ отмечает менеджер в сделке, оплату — директор. Отказ на холодном звонке CRM ставит сама, если последний разговор без результата и перезвона нет; причина берётся из разбора.</p></div>
    ${d.unlinked && d.unlinked.length ? `<div class="panel"><h2>Презентации без клиента <span class="muted">· ${d.unlinked.length}</span></h2>
      <p class="muted">Эти презентации шли не через Mango (мессенджер, Zoom), и бот не знает номер клиента. Укажите номер, и презентация попадёт в сделку клиента.</p>
      <div class="scroll"><table class="tbl"><thead><tr><th>Когда</th>${director ? '<th class="hide-m">Менеджер</th>' : ''}<th>О чём</th><th></th></tr></thead><tbody>
      ${d.unlinked.map(u => `<tr><td>${esc(when(u.at))}</td>${director ? `<td class="hide-m">${esc(u.manager || '')}</td>` : ''}
        <td>${u.company ? `<b>${esc(u.company)}</b> · ` : ''}${esc(u.summary || '')}${u.contact ? `<div class="muted">В реестре: ${esc(u.contact)}</div>` : ''}
          <div><a class="btn small" href="https://drive.google.com/file/d/${encodeURIComponent(u.file_id)}/view" target="_blank" rel="noopener">Запись</a></div></td>
        <td>${S.me.readonly ? '' : `<button class="btn small" data-plink="${u.id}">Привязать к клиенту</button>`}</td></tr>`).join('')}</tbody></table></div></div>` : ''}
    <div class="panel"><h2>${esc(DEAL_SHOW.find(x => x[0] === f.show)[1])} <span class="muted">· ${list.length}</span></h2>
      ${list.length ? `<div class="scroll"><table class="tbl"><thead><tr><th>Клиент</th>${director ? '<th class="hide-m">Менеджер</th>' : ''}<th>Этап</th><th class="hide-m">Последнее</th><th>Что дальше</th></tr></thead><tbody>
      ${list.slice(0, 300).map(x => `<tr class="click" data-deal="${esc(x.phone)}"><td><b>${esc(dealName(x))}</b>${x.company ? `<div class="muted">${esc(phone(x.number))}</div>` : ''}</td>
        ${director ? `<td class="hide-m">${esc(x.manager)}</td>` : ''}<td>${esc(x.stage_name)}<div>${dealStatus(x)}</div></td><td class="hide-m">${esc(when(x.last_at))}</td>
        <td>${x.status === 'lost' ? `<span class="muted">${esc(x.lost.reason)}</span>` : esc(x.advice || '')}</td></tr>`).join('')}</tbody></table></div>`
      : '<p class="muted">Таких сделок нет.</p>'}</div>`;
  $$('[data-ds]', view).forEach(b => b.addEventListener('click', () => { f.show = b.dataset.ds; go('deals'); }));
  $$('[data-dd]', view).forEach(b => b.addEventListener('click', () => { f.days = b.dataset.dd; go('deals'); }));
  $('#deal-person', view)?.addEventListener('change', e => { f.person = e.target.value; go('deals'); });
  $$('[data-deal]', view).forEach(tr => tr.addEventListener('click', () => dealCard(tr.dataset.deal)));
  $$('[data-plink]', view).forEach(b => b.addEventListener('click', () => linkPresentation(d.unlinked.find(u => String(u.id) === b.dataset.plink))));
};

function linkPresentation(u) {
  const box = modal(`<header class="rvm-head"><div><h2>Привязать к клиенту</h2>
      <div class="rvm-mut">Презентация ${esc(when(u.at))}${u.company ? ' · ' + esc(u.company) : ''}</div></div><button class="rvm-x" aria-label="Закрыть">×</button></header>
    <div class="rvm-body"><div class="outform">
      <label class="field">Номер клиента<input id="pl-phone" inputmode="tel" placeholder="+7 999 123-45-67" autocomplete="off"></label>
      <button class="btn primary" id="pl-go">Привязать</button></div>
      <p class="muted">Номер, по которому менеджер звонил клиенту или с которого клиент писал в мессенджер. Если с этим номером был холодный звонок, презентация встанет в ту же сделку.</p></div>`);
  $('#pl-phone', box).focus();
  $('#pl-go', box).addEventListener('click', async () => {
    try {
      const r = await api(`/api/presentations/${u.id}/link`, {method: 'POST', scoped: false, body: {phone: $('#pl-phone', box).value}});
      toast(r.call_found ? 'Привязано к сделке клиента' : 'Привязано. Звонков с этим номером в Mango нет, сделка создана по презентации');
      $('.rvm-x', box).click();
      go('deals');
    } catch (e) { toast(e.message, true); }
  });
}

// Кнопка «Его сделки» в карточке сотрудника.
document.addEventListener('click', e => {
  const b = e.target.closest && e.target.closest('[data-deals-of]');
  if (b) { S.dealF = {show: 'active', days: '90', person: b.dataset.dealsOf}; openPage('deals'); }
});

function dealTimeline(items) {
  return items.map(t => {
    const head = `<div class="rvm-ih"><b>${esc(t.title)}</b><span class="rvm-mut">${esc(when(t.at))}${t.by ? ' · ' + esc(t.by) : ''}</span>
      ${t.type === 'presentation' && TEMP[t.temperature] ? `<span class="rvm-chip ${TEMP[t.temperature][1]}">клиент ${TEMP[t.temperature][0]}</span>` : ''}
      ${t.amount ? `<span class="rvm-chip good">${rub(t.amount)}</span>` : ''}</div>`;
    const cls = t.type === 'event' ? '' : t.ok || (t.type === 'presentation' && !t.lost) ? 'good' : t.lost ? 'miss' : '';
    return `<div class="rvm-item ${cls}">${head}
      ${t.text ? `<div>${esc(t.text)}</div>` : ''}
      ${t.lost ? `<div class="rvm-q">Где остыл: ${esc(t.lost)}</div>` : ''}
      ${t.say ? `<div class="rvm-say">Как можно было: ${esc(t.say)}</div>` : ''}
      ${t.type === 'call' ? `<div><button class="btn small" data-review="${t.id}">Разбор и запись</button></div>` : ''}
      ${t.file_id ? `<div><a class="btn small" href="https://drive.google.com/file/d/${encodeURIComponent(t.file_id)}/view" target="_blank" rel="noopener">Запись презентации</a></div>` : ''}</div>`;
  }).join('');
}

async function dealCard(ph) {
  const box = modal('<header class="rvm-head"><div class="rvm-mut">Загружаю…</div><button class="rvm-x" aria-label="Закрыть">×</button></header>');
  let d;
  try { d = await api('/api/deals/' + encodeURIComponent(ph), {scoped: false}); } catch (e) { $('.rvm-mut', box).textContent = e.message; return; }
  const ro = S.me.readonly;
  const acts = d.status === 'active' ? [
    ['presentation', 'Презентация проведена'], ['materials', 'Отправил материалы'], ['contract_sent', 'Договор отправлен'],
    ['contract_edits', 'Правки по договору'], ['contract_signed', 'Договор подписан'],
    ...(d.can_pay ? [['paid', 'Оплата получена']] : []), ['lost', 'Отказ']] : [['reopen', 'Снова в работе']];
  box.innerHTML = `<header class="rvm-head"><div><div>${dealStatus(d)}</div><h2>${esc(dealName(d))}</h2>
      <div class="rvm-mut">${d.company ? `<a href="tel:+${esc(d.number)}">${esc(phone(d.number))}</a> · ` : ''}${esc(d.manager)} · этап: ${esc(d.stage_name)}</div></div>
      <button class="rvm-x" aria-label="Закрыть">×</button></header>
    <div class="rvm-body">
      ${dealSteps(d)}
      ${d.status === 'lost' ? `<div class="rvm-item miss"><b>Отказ: ${esc(d.lost.reason)}</b>${d.lost.note ? `<div>${esc(d.lost.note)}</div>` : ''}
        <div class="rvm-q">${d.lost.auto ? 'CRM поставила сама по последнему разговору. Если это не так, нажмите «Снова в работе».' : 'Отмечено ' + esc(when(d.lost.at))}</div></div>` : ''}
      ${d.advice ? `<div><h3>Что дальше</h3><div class="rvm-say">${esc(d.advice)}</div></div>` : ''}
      ${d.missing.length && d.status === 'active' ? `<div><h3>Для договора не хватает</h3><div>${d.missing.map(m => `<span class="chip warn">${esc(m)}</span>`).join(' ')}</div></div>` : ''}
      ${d.contracts.length ? `<div><h3>Договоры</h3>${d.contracts.map(c => `<div>${esc(contractTitle(c))}${c.by ? ` <span class="rvm-mut">· ${esc(c.by)}</span>` : ''}</div>`).join('')}</div>` : ''}
      ${ro ? '' : `<div><h3>Отметить</h3><div class="row chips">${acts.map(([k, t]) => `<button class="btn small${k === 'lost' ? ' danger' : ''}" data-ev="${k}">${t}</button>`).join('')}
        <button class="btn small" data-ev="note">Заметка</button><button class="btn small" data-contract>Составить договор</button></div><div id="deal-form"></div></div>`}
      <div><h3>История</h3>${dealTimeline([...d.timeline].reverse()) || '<div class="rvm-mut">Пусто.</div>'}</div>
    </div>`;
  $$('[data-ev]', box).forEach(b => b.addEventListener('click', () => dealForm(box, d, b.dataset.ev)));
  $('[data-contract]', box)?.addEventListener('click', () => {
    S.contractDeal = {phone: d.phone, number: d.number, title: dealName(d) + (d.company ? ' · ' + phone(d.number) : ''), missing: d.missing};
    $('.rvm-x', box).click();
    openPage('contracts');
  });
}

// ---------- договоры: генератор .docx (static/contracts/) и журнал ----------
const CONTRACT_JS = ['pizzip.min.js', 'docxtemplater.min.js', 'logic.js', 'generator.js'];
let contractJs = null;
function contractScripts() {
  // Библиотеки весят ~200 КБ, поэтому грузим их только при открытии раздела.
  contractJs = contractJs || CONTRACT_JS.reduce((p, name) => p.then(() => new Promise((ok, fail) => {
    const el = document.createElement('script');
    el.src = '/static/contracts/' + name;
    el.onload = ok;
    el.onerror = () => { contractJs = null; fail(new Error('Генератор договоров не загрузился. Обновите страницу.')); };
    document.head.appendChild(el);
  })), Promise.resolve());
  return contractJs;
}
const contractTitle = c => `${c.type_title}${c.num ? ' № ' + c.num : c.base_num ? ' к № ' + c.base_num : ''}${c.date ? ' от ' + ddmm(c.date) + '.' + c.date.slice(0, 4) : ''}${c.amount ? ' · ' + rub(c.amount) : ''}`;

VIEWS.contracts = async view => {
  await contractScripts();
  const deal = S.contractDeal || null;
  S.contractDeal = null;
  view.innerHTML = `<div id="ct-gen"></div><div class="panel" id="ct-log"><p class="muted">Загружаю журнал…</p></div>`;
  const journal = async () => {
    const d = await api('/api/contracts', {scoped: false});
    const box = $('#ct-log', view);
    if (!box) return;
    box.innerHTML = `<h2>Журнал договоров <span class="muted">· ${d.items.length}</span></h2>
      ${d.items.length ? `<div class="scroll"><table class="tbl"><thead><tr><th class="n">№</th><th>Договор</th><th>Клиент</th><th class="hide-m">ИНН</th><th class="n">Сумма</th><th class="hide-m">Оформил</th></tr></thead><tbody>
      ${d.items.map(c => `<tr${c.phone ? ` class="click" data-deal="${esc(c.phone)}"` : ''}><td class="n">${c.num ?? '—'}</td>
        <td>${esc(c.type_title)}${c.base_num ? ` к № ${esc(c.base_num)}` : ''}<div class="muted">${c.date ? esc(ddmm(c.date) + '.' + c.date.slice(0, 4)) : ''}${c.tariff ? ' · ' + esc(c.tariff) : ''}</div></td>
        <td>${esc(c.company || '')}${c.phone ? '<div class="muted">сделка →</div>' : ''}</td><td class="hide-m num">${esc(c.inn || '')}</td>
        <td class="n">${c.amount ? rub(c.amount) : ''}</td><td class="hide-m">${esc(c.by || '')}<div class="muted">${esc(when(c.created_at))}</div></td></tr>`).join('')}
      </tbody></table></div>` : '<p class="muted">Договоров пока нет. Оформленный здесь договор попадёт в журнал с номером.</p>'}`;
    $$('[data-deal]', box).forEach(tr => tr.addEventListener('click', () => dealCard(tr.dataset.deal)));
  };
  await window.ContractGenerator.mount($('#ct-gen', view), {
    api: (path, o = {}) => api(path, {...o, scoped: false}), deal, readonly: S.me.readonly,
    onDone: () => journal().catch(e => toast(e.message, true)),
  });
  await journal();
};

function dealForm(box, d, kind) {
  const form = $('#deal-form', box);
  const label = {contract_edits: 'Какие правки попросил клиент', note: 'Заметка', lost: 'Что сказал клиент'}[kind];
  form.innerHTML = `<div class="outform">
    ${kind === 'lost' ? `<label class="field">Причина<select id="dv-reason"><option value="">Выберите</option>${Object.entries(d.reasons).map(([k, t]) => `<option value="${k}">${esc(t)}</option>`).join('')}</select></label>` : ''}
    ${kind === 'paid' ? '<label class="field">Сумма, ₽<input id="dv-amount" inputmode="decimal" placeholder="165 000"></label>' : ''}
    <label class="field">${esc(label || 'Комментарий, если нужно')}<textarea id="dv-note" rows="2" maxlength="2000"></textarea></label>
    <button class="btn primary" id="dv-go">Сохранить</button></div>`;
  $('#dv-go', form).addEventListener('click', async () => {
    try {
      await api(`/api/deals/${encodeURIComponent(d.phone)}/event`, {method: 'POST', scoped: false, body: {
        kind, note: $('#dv-note', form).value, reason: $('#dv-reason', form)?.value, amount: $('#dv-amount', form)?.value}});
      toast('Сохранено');
      $('.rvm-x', box).click();
      if (S.page === 'deals') go('deals');
      dealCard(d.phone);
    } catch (e) { toast(e.message, true); }
  });
}

// ---------- start ----------
(async () => {
  try {
    S.me = await api('/api/me', {scoped: false});
  } catch (e) {
    if (!$('.login')) showLogin(e.message);
    else if (!S.me) showLogin(e.message);
    return;
  }
  const saved = load();
  if (S.me.privileged) {
    S.ext = S.me.team.some(p => p.ext === saved.ext) ? saved.ext : S.me.team[0]?.ext || null;
  }
  shell();
  // Ссылка из меню директора на странице аналитики: /?work#раздел. После раздела через «/» может идти
  // его параметр: так закладка «Собрать номера» присылает номера в «Источники» (#sources/import:...).
  const fromHash = () => {
    let asked = '';
    try { asked = decodeURIComponent(location.hash.slice(1)); } catch (e) { /* испорченная ссылка */ }
    if (location.hash) history.replaceState(null, '', location.pathname + location.search);
    const cut = asked.indexOf('/');
    S.arg = cut < 0 ? null : asked.slice(cut + 1);
    return cut < 0 ? asked : asked.slice(0, cut);
  };
  go(fromHash() || saved.page || HOME[S.me.role]);
  window.addEventListener('hashchange', () => { const page = fromHash(); if (page) go(page); });
  // Очередь и счётчики обновляются раз в минуту, если менеджер не заполняет итог.
  setInterval(async () => {
    if (document.hidden || S.busy || S.dial || $('.drawer') || $('#outform:not(.hidden)')) return;
    if (S.page === 'queue') go('queue');
  }, 60000);
})();
