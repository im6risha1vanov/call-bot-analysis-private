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
  key: '<circle cx="8" cy="15" r="4"/><path d="m11 12 9-9M17 6l3 3M15 8l2 2"/>',
  star: '<path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z"/>',
};
const svg = k => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON[k]}</svg>`;

const STAFF = ['manager', 'director'];
const PAGES = [
  {id: 'team', name: 'Команда', icon: 'team', roles: ['director']},
  {id: 'queue', name: 'Очередь', icon: 'queue', roles: STAFF},
  {id: 'tasks', name: 'Перезвоны', icon: 'tasks', roles: STAFF},
  {id: 'leads', name: 'База', icon: 'leads', roles: STAFF},
  {id: 'calls', name: 'Звонки', icon: 'calls', roles: STAFF},
  {id: 'stats', name: 'Итоги', icon: 'stats', roles: STAFF},
  {id: 'reports', name: 'Отчёты', icon: 'report', roles: STAFF},
  {id: 'training', name: 'Тренировки', icon: 'train', roles: STAFF},
  {id: 'clients', name: 'Клиенты', icon: 'client', roles: ['director']},
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
  const people = S.me.privileged ? `<label class="field"><select id="pick-ext" aria-label="Менеджер">${
    S.me.team.map(p => `<option value="${esc(p.ext)}"${p.ext === S.ext ? ' selected' : ''}>${esc(p.name)} · ${esc(p.ext)}</option>`).join('')
  }</select></label>` : '';
  root.innerHTML = `<div class="app">
    <aside class="side">
      <div class="logo"><i></i>Averon <b>CRM</b></div>
      <nav class="nav">${pages.map(p => `<button data-page="${p.id}">${svg(p.icon)}<span>${p.name}</span><span class="count" id="count-${p.id}"></span></button>`).join('')}</nav>
      <div class="side-foot"><span>${esc(S.me.name)}${S.me.ext ? ' · доб. ' + esc(S.me.ext) : ''}</span>
        ${S.me.role === 'director' ? '<a class="btn small" href="/director">Аналитика директора</a>' : ''}
        ${S.me.test ? '<a class="btn small" href="/test-exit">Сменить вход</a>' : '<button id="logout">Выйти</button>'}</div>
    </aside>
    <div class="main">
      ${S.me.test ? `<div class="banner"><b>Тестовый вход · ${esc(ROLE_NAME[S.me.role] || '')}</b><span>${S.me.role === 'manager' ? 'Только просмотр: звонки и отметки работают во входе самого менеджера.' : S.me.role === 'director' ? 'Очереди менеджеров здесь только для просмотра.' : 'Так кабинет увидит клиент.'}</span>${S.me.role === 'director' ? '<a href="/director">Аналитика директора</a>' : ''}<a href="/test-exit">Сменить вход</a></div>` : ''}
      <header class="top"><h1 id="title"></h1><span class="sp"></span>${people}</header>
      <main class="content" id="view"></main>
    </div></div>`;
  $$('.nav button').forEach(b => b.addEventListener('click', () => go(b.dataset.page)));
  if ($('#logout')) $('#logout').addEventListener('click', async () => { await api('/api/logout', {method: 'POST', body: {}}).catch(() => {}); showLogin(); });
  const pick = $('#pick-ext');
  if (pick) pick.addEventListener('change', () => { S.ext = pick.value; S.queue = null; S.pos = 0; S.dial = null; save(); go(S.page); });
}

function save() { try { localStorage.setItem('crm', JSON.stringify({page: S.page, ext: S.ext})); } catch (e) { /* без хранилища */ } }
function load() { try { return JSON.parse(localStorage.getItem('crm') || '{}'); } catch (e) { return {}; } }

async function go(page) {
  if (!PAGES.some(p => p.id === page && allowed(p))) page = HOME[S.me.role] || 'queue';
  S.page = page; save();
  $$('.nav button').forEach(b => b.setAttribute('aria-current', b.dataset.page === page ? 'page' : 'false'));
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

const fmtWhen = v => v ? new Date(v).toLocaleString('ru-RU', {day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'}) : '—';

VIEWS.access = async view => {
  const d = await api('/api/access', {scoped: false});
  const roleName = {director: 'Директор', manager: 'Менеджер', client: 'Клиент'};
  const who = i => i.role === 'manager' ? `${esc(i.name)} · доб. ${esc(i.ext)}` : esc(i.name || roleName[i.role]);
  view.innerHTML = `<div class="grid2"><div class="panel"><h2>Выдать личную ссылку</h2>
      <p class="muted">По ссылке человек входит в CRM без пароля, на любом устройстве. Ссылку видно только сейчас: скопируйте и отправьте лично.</p>
      <div class="outform">
        <label class="field">Кому<select id="a-role"><option value="manager">Менеджеру</option><option value="client">Клиенту колл-центра</option><option value="director">Руководителю</option></select></label>
        <label class="field" id="a-ext-f">Менеджер<select id="a-ext">${d.managers.map(m => `<option value="${esc(m.ext)}">${esc(m.name)} · доб. ${esc(m.ext)}</option>`).join('')}</select></label>
        <label class="field" id="a-cl-f" hidden>Клиент<select id="a-client">${d.clients.map(c => `<option>${esc(c)}</option>`).join('')}</select></label>
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
    $('#a-name-f', view).hidden = role.value !== 'director';
  };
  role.addEventListener('change', sync);
  $('#a-go', view).addEventListener('click', async () => {
    const out = $('#a-out', view);
    try {
      const r = await api('/api/access', {method: 'POST', scoped: false, body: {role: role.value, person_ext: $('#a-ext', view).value,
        client: $('#a-client', view).value, name: $('#a-name', view).value}});
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
  go(saved.page || HOME[S.me.role]);
  // Очередь и счётчики обновляются раз в минуту, если менеджер не заполняет итог.
  setInterval(async () => {
    if (document.hidden || S.busy || S.dial || $('.drawer') || $('#outform:not(.hidden)')) return;
    if (S.page === 'queue') go('queue');
  }, 60000);
})();
