// Кабинет голосового робота: операторы, кампании, контакты, стоп-лист, звонки, аналитика.
// Работает поверх app.js (его помощники esc, $, $$, api, modal, toast, phone, dur, fmtWhen, heights, go).
'use strict';

const RB = {tab: 'campaigns', owner: '', st: null, voiceView: VIEWS.robot};
const RB_DAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
const RB_TABS = [
  ['operators', 'Операторы'], ['campaigns', 'Кампании'], ['contacts', 'Контакты'], ['stop', 'Стоп-лист'],
  ['calls', 'Звонки'], ['analytics', 'Аналитика'], ['integrations', 'Интеграции'],
  ['settings', 'Цены и клиенты', 'director'], ['voice', 'Голос и тест', 'director'],
];
try { Object.assign(RB, JSON.parse(localStorage.getItem('crm-robot') || '{}'), {st: null, voiceView: RB.voiceView}); } catch (e) { /* без хранилища */ }

const rbq = (path, extra = {}) => {
  const u = new URL('/api/robot/cab' + path, location.origin);
  if (RB.owner) u.searchParams.set('owner', RB.owner);
  Object.entries(extra).forEach(([k, v]) => { if (v !== '' && v != null) u.searchParams.set(k, v); });
  return u.pathname + u.search;
};
const rbGet = (path, extra) => api(rbq(path, extra), {scoped: false});
const rbPost = (path, body = {}) => api('/api/robot/cab' + path, {method: 'POST', scoped: false, body: {...body, owner: RB.owner}});
const rbRO = () => RB.st.readonly ? ' disabled' : '';
const rbSave = () => { try { localStorage.setItem('crm-robot', JSON.stringify({tab: RB.tab, owner: RB.owner})); } catch (e) { /* без хранилища */ } };
const money = v => v == null ? '—' : `${Number(v).toLocaleString('ru-RU', {maximumFractionDigits: 2})} ₽`;
const opName = id => (RB.st.operators || []).find(o => o.id === id)?.name || id;

VIEWS.robot = async view => {
  RB.st = await api(rbq(''), {scoped: false}).catch(async e => {
    if (RB.owner) { RB.owner = ''; rbSave(); return api(rbq(''), {scoped: false}); }
    throw e;
  });
  if (!RB.st.enabled) {
    view.innerHTML = `<div class="panel"><h2>Голосовой робот</h2><p>${esc(RB.st.paid_note)}</p>
      <p class="muted">Робот сам обзванивает ваши номера по расписанию, выясняет интерес и записывает итог каждого звонка. Вы видите звонки, записи разговоров и аналитику здесь.</p></div>`;
    return;
  }
  const tabs = RB_TABS.filter(t => !t[2] || t[2] === RB.st.role);
  if (!tabs.some(t => t[0] === RB.tab)) RB.tab = 'campaigns';
  const owners = RB.st.owners.length > 1 ? `<label class="field rb-owner">Чей кабинет<select id="rb-owner">${RB.st.owners.map(o =>
    `<option value="${esc(o.id)}"${o.id === RB.st.owner ? ' selected' : ''}>${esc(o.name)}</option>`).join('')}</select></label>` : '';
  view.innerHTML = `<div class="rb-head"><div class="filters rb-tabs">${tabs.map(([k, t]) =>
      `<button data-rbt="${k}" aria-pressed="${k === RB.tab}">${t}</button>`).join('')}</div>${owners}</div><div id="rb-body"><div class="empty">Загружаю…</div></div>`;
  $$('[data-rbt]', view).forEach(b => b.addEventListener('click', () => { RB.tab = b.dataset.rbt; rbSave(); go('robot'); }));
  $('#rb-owner', view)?.addEventListener('change', e => { RB.owner = e.target.value; rbSave(); go('robot'); });
  const body = $('#rb-body', view);
  try { await RB_VIEW[RB.tab](body); } catch (e) { body.innerHTML = `<div class="panel empty">${esc(e.message)}</div>`; }
};

const RB_VIEW = {};

// ---------- операторы ----------
RB_VIEW.operators = async el => {
  const ops = RB.st.operators;
  el.innerHTML = `<div class="panel"><h2>Операторы <span class="muted">· ${ops.length}</span></h2>
    <p class="muted">Оператор — это сценарий робота: как представляется, план разговора, правила и аргументы, что записать. Встроенного оператора можно скопировать и править копию.</p>
    <div class="row gapb"><button class="btn primary" id="op-new"${rbRO()}>Новый оператор</button></div>
    ${ops.length ? `<table class="tbl"><thead><tr><th>Имя</th><th>Компания</th><th class="hide-m">Цель</th><th class="n">План / правила / аргументы</th><th></th></tr></thead><tbody>
      ${ops.map(o => `<tr class="click" data-op="${esc(o.id)}"><td><b>${esc(o.name)}</b>${o.builtin ? ' <span class="chip">встроенный</span>' : ''}</td>
        <td>${esc(o.company)}</td><td class="hide-m muted">${esc(o.goal)}</td><td class="n">${o.plan} / ${o.rules} / ${o.facts}</td>
        <td><button class="btn small" data-copy="${esc(o.id)}"${rbRO()}>Копия</button></td></tr>`).join('')}</tbody></table>`
      : '<div class="empty">Операторов пока нет.</div>'}</div>`;
  $('#op-new', el).addEventListener('click', () => operatorForm(null));
  $$('[data-op]', el).forEach(tr => tr.addEventListener('click', e => { if (!e.target.closest('[data-copy]')) operatorForm(tr.dataset.op); }));
  $$('[data-copy]', el).forEach(b => b.addEventListener('click', () => operatorForm(null, b.dataset.copy)));
};

async function operatorForm(id, copyFrom) {
  const src = id || copyFrom ? await rbGet('/operators/' + encodeURIComponent(id || copyFrom)) : {
    name: '', gender: 'female', company: '', lead_source: '', goal: '', greeting: 'Здравствуйте, ', farewell: 'Спасибо, хорошего дня.',
    transfer_to: '', plan: [''], rules: [], facts: [], fields: [], max_items: 10};
  const locked = id && src.builtin;
  const ro = locked || RB.st.readonly ? ' disabled' : '';
  const list = (key, title, hint) => `<section class="op-list" data-list="${key}"><h3>${title} <span class="muted">до ${src.max_items || 10}</span></h3>
    <p class="muted">${hint}</p><div class="op-items">${(src[key].length ? src[key] : (key === 'plan' ? [''] : [])).map(v => opItem(v, ro)).join('')}</div>
    ${ro ? '' : '<button class="btn small" data-add>+ Добавить</button>'}</section>`;
  const box = modal(`<header class="rvm-head"><div><h2>${id ? (locked ? 'Встроенный оператор' : 'Оператор') : copyFrom ? 'Копия оператора' : 'Новый оператор'}</h2>
      <div class="rvm-mut">${locked ? 'Только просмотр. Чтобы поменять, сделайте копию.' : 'Робот говорит только то, что здесь написано.'}</div></div>
      <button class="rvm-x" aria-label="Закрыть">×</button></header>
    <div class="rvm-body op-form">
      <div class="op-grid">
        <label class="field">Имя робота<input data-f="name" value="${esc(src.name)}"${ro}></label>
        <label class="field">Род<select data-f="gender"${ro}><option value="female"${src.gender !== 'male' ? ' selected' : ''}>женский</option>
          <option value="male"${src.gender === 'male' ? ' selected' : ''}>мужской</option></select></label>
        <label class="field">Компания (как представляется)<input data-f="company" value="${esc(src.company)}"${ro}></label>
        <label class="field">Перевод тёплого клиента на добавочный Mango<input data-f="transfer_to" value="${esc(src.transfer_to)}" inputmode="numeric"${ro}></label>
      </div>
      <label class="field">Откуда у нас номер клиента<input data-f="lead_source" value="${esc(src.lead_source)}" placeholder="оставлял заявку на расчёт кухни"${ro}></label>
      <label class="field">Цель звонка<textarea data-f="goal" rows="2"${ro}>${esc(src.goal)}</textarea></label>
      <label class="field">Приветствие<textarea data-f="greeting" rows="2"${ro}>${esc(src.greeting)}</textarea></label>
      <label class="field">Прощание<input data-f="farewell" value="${esc(src.farewell)}"${ro}></label>
      ${list('plan', 'План разговора', 'Шаги по порядку: что выяснить и когда завершить.')}
      ${list('rules', 'Правила', 'Как вести себя в особых случаях: занят, просит не звонить, спрашивает цену.')}
      ${list('facts', 'Аргументы и факты', 'Что робот может говорить о компании и предложении.')}
      <section class="op-list" data-list="fields"><h3>Что записать по итогу</h3>
        <p class="muted">Каждая строка — колонка в итогах звонка, в виде «Название: что записать».</p>
        <div class="op-items">${src.fields.map(f => opItem(f.text, ro, f.key)).join('')}</div>${ro ? '' : '<button class="btn small" data-add>+ Добавить</button>'}</section>
      <div class="row">${locked ? `<button class="btn primary" id="op-copy"${RB.st.readonly ? ' disabled' : ''}>Сделать копию</button>` : `<button class="btn primary" id="op-save"${ro}>Сохранить</button>`}
        ${id && !locked ? `<button class="btn danger" id="op-del"${ro}>Удалить</button>` : ''}<span class="muted" id="op-st"></span></div>
    </div>`);
  box.classList.add('rvm-wide');
  $$('[data-add]', box).forEach(b => b.addEventListener('click', () => {
    const items = $('.op-items', b.closest('.op-list'));
    if (items.children.length >= (src.max_items || 10) && b.closest('[data-list]').dataset.list !== 'fields') return toast('Можно не больше 10 пунктов.', true);
    items.insertAdjacentHTML('beforeend', opItem('', ''));
    items.lastElementChild.querySelector('textarea').focus();
  }));
  box.addEventListener('click', e => { const x = e.target.closest('[data-rm]'); if (x) x.closest('.op-item').remove(); });
  const collect = () => {
    const out = {};
    $$('[data-f]', box).forEach(i => { out[i.dataset.f] = i.value; });
    ['plan', 'rules', 'facts'].forEach(k => { out[k] = $$(`[data-list="${k}"] textarea`, box).map(t => t.value.trim()).filter(Boolean); });
    out.fields = $$('[data-list="fields"] textarea', box).map(t => ({key: t.dataset.key || '', text: t.value.trim()})).filter(f => f.text);
    return out;
  };
  $('#op-copy', box)?.addEventListener('click', () => { box.previousElementSibling?.remove(); box.remove(); operatorForm(null, id); });
  $('#op-save', box)?.addEventListener('click', async () => {
    try {
      await rbPost('/operators' + (id ? '/' + encodeURIComponent(id) : ''), {...collect(), copy_from: copyFrom || ''});
      box.previousElementSibling?.remove(); box.remove(); toast('Оператор сохранён. Робот возьмёт его со следующего звонка.'); go('robot');
    } catch (e) { $('#op-st', box).textContent = e.message; }
  });
  $('#op-del', box)?.addEventListener('click', async () => {
    if (!confirm('Удалить оператора?')) return;
    try { await rbPost('/operators/' + encodeURIComponent(id) + '/delete'); box.previousElementSibling?.remove(); box.remove(); go('robot'); }
    catch (e) { $('#op-st', box).textContent = e.message; }
  });
}

function opItem(value, ro, key = '') {
  return `<div class="op-item"><textarea rows="2" data-key="${esc(key)}"${ro}>${esc(value)}</textarea>${ro ? '' : '<button class="btn small" data-rm aria-label="Убрать">×</button>'}</div>`;
}

// ---------- кампании ----------
RB_VIEW.campaigns = async el => {
  const {items} = await rbGet('/campaigns');
  el.innerHTML = `<div class="panel"><h2>Кампании <span class="muted">· ${items.length}</span></h2>
    <p class="muted">Кампания — это список номеров, оператор и расписание. Робот звонит сам в рабочие дни и часы кампании, по одному звонку за раз, и повторяет недозвоны. Звоните только тем, кто согласился на звонок.</p>
    <div class="row gapb"><button class="btn primary" id="cp-new"${rbRO()}>Новая кампания</button></div>
    ${items.length ? `<table class="tbl"><thead><tr><th>Кампания</th><th>Статус</th><th class="hide-m">Расписание</th><th class="n">Обзвонено</th><th class="n">Лиды</th><th></th></tr></thead><tbody>
      ${items.map(c => `<tr><td><b>${esc(c.name)}</b><div class="muted">${esc(opName(c.operator))}</div></td>
        <td><span class="chip ${c.status === 'running' ? 'good' : c.status === 'paused' ? 'warn' : ''}">${esc(c.status_name)}</span></td>
        <td class="hide-m">${c.days.map(d => RB_DAYS[d - 1]).join(' ')} · ${c.start_time}–${c.end_time}<div class="muted">${esc(tzName(c.timezone))} · попыток ${c.attempts}</div></td>
        <td class="n">${c.done} из ${c.total}</td><td class="n">${c.interested}</td>
        <td><div class="row">${c.status === 'running' ? `<button class="btn small" data-ca="pause" data-id="${c.id}"${rbRO()}>Пауза</button>`
          : c.status !== 'done' ? `<button class="btn small primary" data-ca="start" data-id="${c.id}"${rbRO()}>Запустить</button>` : ''}
          <button class="btn small" data-ce="${c.id}">Открыть</button></div></td></tr>`).join('')}</tbody></table>`
      : '<div class="empty">Кампаний пока нет. Сначала загрузите номера во вкладке «Контакты».</div>'}</div>`;
  $('#cp-new', el).addEventListener('click', () => campaignForm(null));
  $$('[data-ce]', el).forEach(b => b.addEventListener('click', () => campaignForm(items.find(c => c.id === +b.dataset.ce))));
  $$('[data-ca]', el).forEach(b => b.addEventListener('click', async () => {
    if (b.dataset.ca === 'start' && !confirm('Запустить кампанию? Робот начнёт звонить по расписанию. Убедитесь, что у этих людей есть согласие на звонок.')) return;
    try { await rbPost(`/campaigns/${b.dataset.id}/${b.dataset.ca}`); go('robot'); } catch (e) { toast(e.message, true); }
  }));
};

const tzName = tz => (RB.st.timezones.find(([k]) => k === tz) || [tz, tz])[1];

async function campaignForm(c) {
  const contacts = await rbGet('/contacts');
  const v = c || {name: '', operator: RB.st.operators[0]?.id || '', days: [1, 2, 3, 4, 5], start_time: '10:00', end_time: '19:00',
    timezone: 'Europe/Moscow', attempts: 2, retry_minutes: 120};
  const ro = RB.st.readonly ? ' disabled' : '';
  const box = modal(`<header class="rvm-head"><div><h2>${c ? esc(c.name) : 'Новая кампания'}</h2>
      ${c ? `<div class="rvm-mut">${esc(c.status_name)} · обзвонено ${c.done} из ${c.total} · лидов ${c.interested}</div>` : ''}</div>
      <button class="rvm-x" aria-label="Закрыть">×</button></header>
    <div class="rvm-body op-form">
      <label class="field">Название<input id="cf-name" value="${esc(v.name)}"${ro}></label>
      <label class="field">Оператор<select id="cf-op"${ro}>${RB.st.operators.map(o => `<option value="${esc(o.id)}"${o.id === v.operator ? ' selected' : ''}>${esc(o.name)} · ${esc(o.company)}</option>`).join('')}</select></label>
      <div class="field">Дни недели<div class="filters">${RB_DAYS.map((d, i) => `<button data-day="${i + 1}" aria-pressed="${v.days.includes(i + 1)}"${ro}>${d}</button>`).join('')}</div></div>
      <div class="op-grid">
        <label class="field">Начало<input id="cf-start" type="time" value="${esc(v.start_time)}"${ro}></label>
        <label class="field">Окончание<input id="cf-end" type="time" value="${esc(v.end_time)}"${ro}></label>
        <label class="field">Часовой пояс<select id="cf-tz"${ro}>${RB.st.timezones.map(([k, t]) => `<option value="${esc(k)}"${k === v.timezone ? ' selected' : ''}>${esc(t)}</option>`).join('')}</select></label>
        <label class="field">Попыток дозвона<select id="cf-att"${ro}>${[1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map(n => `<option${n === v.attempts ? ' selected' : ''}>${n}</option>`).join('')}</select></label>
        <label class="field">Повтор недозвона через, мин<input id="cf-retry" type="number" min="10" value="${v.retry_minutes}"${ro}></label>
      </div>
      <div class="field">Номера<label class="rb-check"><input type="checkbox" id="cf-all"${c ? '' : ' checked'}${ro}> ${c ? 'Добавить' : 'Взять'} все контакты кабинета (${contacts.total})</label>
        <span class="muted">Номера из стоп-листа робот пропустит. Повторно один номер в кампанию не попадает.</span></div>
      <div class="row"><button class="btn primary" id="cf-save"${ro}>Сохранить</button>
        ${c ? `<button class="btn" id="cf-targets">Номера кампании</button><button class="btn" id="cf-finish"${ro}>Завершить</button><button class="btn danger" id="cf-del"${ro}>Удалить</button>` : ''}
        <span class="muted" id="cf-st"></span></div>
      <div id="cf-list"></div>
    </div>`);
  box.classList.add('rvm-wide');
  $$('[data-day]', box).forEach(b => b.addEventListener('click', () => b.setAttribute('aria-pressed', b.getAttribute('aria-pressed') !== 'true')));
  const close = () => { box.previousElementSibling?.remove(); box.remove(); };
  const st = t => { $('#cf-st', box).textContent = t; };
  $('#cf-save', box).addEventListener('click', async () => {
    const body = {name: $('#cf-name', box).value, operator: $('#cf-op', box).value,
      days: $$('[data-day][aria-pressed="true"]', box).map(b => +b.dataset.day), start_time: $('#cf-start', box).value, end_time: $('#cf-end', box).value,
      timezone: $('#cf-tz', box).value, attempts: +$('#cf-att', box).value, retry_minutes: +$('#cf-retry', box).value};
    if ($('#cf-all', box).checked) body.contacts = 'all';
    try { await rbPost('/campaigns' + (c ? '/' + c.id : ''), body); close(); toast('Кампания сохранена.'); go('robot'); } catch (e) { st(e.message); }
  });
  $('#cf-targets', box)?.addEventListener('click', async () => {
    const {items} = await rbGet(`/campaigns/${c.id}/targets`);
    $('#cf-list', box).innerHTML = items.length ? `<div class="scroll"><table class="tbl"><thead><tr><th>Номер</th><th>Имя</th><th class="n">Попыток</th><th>Итог</th></tr></thead><tbody>
      ${items.map(t => `<tr><td>${esc(phone(t.phone))}</td><td>${esc(t.name)}</td><td class="n">${t.attempts}</td>
        <td>${t.done ? esc(t.outcome || 'готово') : t.next_at ? 'повтор ' + fmtWhen(t.next_at) : 'ждёт звонка'}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">Номеров нет.</div>';
  });
  for (const [sel, action, ask] of [['#cf-finish', 'finish', 'Завершить кампанию? Робот больше не будет по ней звонить.'], ['#cf-del', 'delete', 'Удалить кампанию вместе со списком номеров? Звонки останутся во вкладке «Звонки».']]) {
    $(sel, box)?.addEventListener('click', async () => {
      if (!confirm(ask)) return;
      try { await rbPost(`/campaigns/${c.id}/${action}`); close(); go('robot'); } catch (e) { st(e.message); }
    });
  }
}

// ---------- контакты ----------
RB_VIEW.contacts = async el => {
  const [data, tabs] = await Promise.all([rbGet('/contacts', {q: RB.q || ''}), rbGet('/contacts/tabs').catch(() => ({tabs: []}))]);
  el.innerHTML = `<div class="grid2"><div class="panel"><h2>Контакты <span class="muted">· ${data.total}</span></h2>
      <div class="row gapb"><input id="ct-q" placeholder="Поиск по номеру, имени, заметке" value="${esc(RB.q || '')}"><button class="btn" id="ct-find">Найти</button>
        ${data.items.length ? `<button class="btn danger small" id="ct-del"${rbRO()}>Удалить отмеченные</button>` : ''}</div>
      ${data.items.length ? `<div class="scroll"><table class="tbl"><thead><tr><th></th><th>Номер</th><th>Имя</th><th class="hide-m">Заметка</th><th class="hide-m">Откуда</th></tr></thead><tbody>
        ${data.items.map(c => `<tr><td><input type="checkbox" data-ct="${c.id}"></td><td>${esc(phone(c.phone))}${c.stopped ? ' <span class="chip bad">стоп</span>' : ''}</td>
          <td>${esc(c.name)}</td><td class="hide-m muted">${esc(c.note)}</td><td class="hide-m muted">${esc(c.source)}</td></tr>`).join('')}</tbody></table></div>
        ${data.items.length < data.total ? `<p class="muted">Показаны последние ${data.items.length}. Остальные найдутся поиском.</p>` : ''}`
        : '<div class="empty">Контактов нет. Загрузите файл или возьмите номера с листа таблицы.</div>'}</div>
    <div class="stack"><div class="panel"><h2>Загрузить файл</h2>
      <p class="muted">CSV или Excel (.xlsx). Столбец с номером найдётся по заголовку «Телефон», имя по «Имя» или «ФИО», заметка по «Комментарий». Без заголовков берём столбец, где номера.</p>
      <input type="file" id="ct-file" accept=".csv,.txt,.xlsx"${rbRO()}><div class="muted" id="ct-st"></div></div>
      <div class="panel"><h2>Из таблицы «Прозвон лидов»</h2>
      ${tabs.tabs.length ? `<div class="row"><select id="ct-tab">${tabs.tabs.map(t => `<option>${esc(t)}</option>`).join('')}</select>
        <button class="btn" id="ct-sheet"${rbRO()}>Взять номера</button></div><div class="muted" id="ct-st2"></div>` : '<div class="empty">Листы таблицы сейчас недоступны.</div>'}</div></div></div>`;
  const find = () => { RB.q = $('#ct-q', el).value; go('robot'); };
  $('#ct-find', el).addEventListener('click', find);
  $('#ct-q', el).addEventListener('keydown', e => { if (e.key === 'Enter') find(); });
  $('#ct-del', el)?.addEventListener('click', async () => {
    const ids = $$('[data-ct]:checked', el).map(i => +i.dataset.ct);
    if (!ids.length) return toast('Отметьте контакты.', true);
    if (!confirm(`Удалить ${ids.length} контакт(ов)? Из уже созданных кампаний номера не пропадут.`)) return;
    try { await rbPost('/contacts/delete', {ids}); go('robot'); } catch (e) { toast(e.message, true); }
  });
  $('#ct-file', el).addEventListener('change', async e => {
    const f = e.target.files[0]; if (!f) return;
    const st = $('#ct-st', el);
    if (f.size > 4_000_000) return (st.textContent = 'Файл больше 4 МБ. Разбейте его на части.');
    st.textContent = 'Загружаю…';
    try {
      let added = 0, skipped = 0, total = 0;
      if (/\.xlsx$/i.test(f.name)) {
        if (f.size > 280_000) throw new Error('Excel-файл больше 280 КБ. Сохраните его как CSV или разбейте на части.');
        const r = await rbPost('/contacts/upload', {name: f.name, data: await fileB64(f)});
        ({added, skipped, total} = r);
      } else {
        const text = await fileText(f);
        for (const part of csvParts(text)) {
          const r = await rbPost('/contacts/upload', {name: f.name, text: part});
          added += r.added; skipped += r.skipped; total = r.total;
          st.textContent = `Загружено ${added}…`;
        }
      }
      toast(`Добавлено номеров: ${added}${skipped ? `, без номера пропущено строк: ${skipped}` : ''}. Всего в кабинете ${total}.`);
      go('robot');
    } catch (err) { st.textContent = err.message; }
  });
  $('#ct-sheet', el)?.addEventListener('click', async () => {
    const st = $('#ct-st2', el); st.textContent = 'Читаю лист…';
    try { const r = await rbPost('/contacts/sheet', {tab: $('#ct-tab', el).value}); toast(`Добавлено номеров: ${r.added}. Всего в кабинете ${r.total}.`); go('robot'); }
    catch (e) { st.textContent = e.message; }
  });
};

function fileB64(f) {
  return new Promise((ok, bad) => { const r = new FileReader(); r.onload = () => ok(String(r.result).split(',')[1] || ''); r.onerror = () => bad(new Error('Файл не прочитался.')); r.readAsDataURL(f); });
}
async function fileText(f) {
  const buf = await f.arrayBuffer();
  try { return new TextDecoder('utf-8', {fatal: true}).decode(buf); } catch (e) { return new TextDecoder('windows-1251').decode(buf); }
}
function csvParts(text, size = 250_000) { // крупный CSV частями, заголовок в каждой
  const lines = text.replace(/^﻿/, '').split(/\r?\n/);
  const head = lines[0], parts = []; let cur = [];
  let len = 0;
  for (const line of lines.slice(1)) {
    if (len + line.length > size && cur.length) { parts.push([head, ...cur].join('\n')); cur = []; len = 0; }
    cur.push(line); len += line.length + 1;
  }
  parts.push([head, ...cur].join('\n'));
  return parts;
}

// ---------- стоп-лист ----------
RB_VIEW.stop = async el => {
  const {items} = await rbGet('/stop');
  el.innerHTML = `<div class="grid2"><div class="panel"><h2>Стоп-лист <span class="muted">· ${items.length}</span></h2>
      <p class="muted">По этим номерам робот не звонит ни в одной кампании. Кто просит робота больше не звонить, попадает сюда сам.</p>
      ${items.length ? `<div class="scroll"><table class="tbl"><thead><tr><th>Номер</th><th>Причина</th><th class="hide-m">Когда</th><th></th></tr></thead><tbody>
        ${items.map(s => `<tr><td>${esc(phone(s.phone))}</td><td>${esc(s.reason)}</td><td class="hide-m">${fmtWhen(s.at)}</td>
          <td><button class="btn small" data-unstop="${esc(s.phone)}"${rbRO()}>Убрать</button></td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">Стоп-лист пуст.</div>'}</div>
    <div class="panel"><h2>Добавить номера</h2><label class="field">По одному в строке<textarea id="sl-phones" rows="6"${rbRO()}></textarea></label>
      <label class="field">Причина<input id="sl-reason" placeholder="просил не звонить"${rbRO()}></label>
      <div class="row"><button class="btn primary" id="sl-add"${rbRO()}>Добавить</button></div></div></div>`;
  $('#sl-add', el).addEventListener('click', async () => {
    try { const r = await rbPost('/stop', {phones: $('#sl-phones', el).value, reason: $('#sl-reason', el).value}); toast(`В стоп-лист добавлено: ${r.added}.`); go('robot'); }
    catch (e) { toast(e.message, true); }
  });
  $$('[data-unstop]', el).forEach(b => b.addEventListener('click', async () => {
    try { await rbPost('/stop/remove', {phone: b.dataset.unstop}); go('robot'); } catch (e) { toast(e.message, true); }
  }));
};

// ---------- звонки ----------
function rbFilters(campaigns, withOutcome = true) {
  const f = RB.f || {};
  return `<div class="row gapb rb-filters">
    <select id="rf-camp"><option value="">Все кампании</option>${campaigns.map(c => `<option value="${c.id}"${String(c.id) === String(f.campaign || '') ? ' selected' : ''}>${esc(c.name)}</option>`).join('')}</select>
    ${withOutcome ? `<select id="rf-out"><option value="">Все статусы</option>${Object.entries(RB.st.outcomes).map(([k, t]) => `<option value="${k}"${k === f.outcome ? ' selected' : ''}>${esc(t)}</option>`).join('')}</select>` : ''}
    <label class="field">с<input type="date" id="rf-from" value="${esc(f.from || '')}"></label><label class="field">по<input type="date" id="rf-to" value="${esc(f.to || '')}"></label>
    <button class="btn" id="rf-go">Показать</button></div>`;
}
function bindFilters(el) {
  $('#rf-go', el).addEventListener('click', () => {
    RB.f = {campaign: $('#rf-camp', el).value, outcome: $('#rf-out', el)?.value || '', from: $('#rf-from', el).value, to: $('#rf-to', el).value};
    go('robot');
  });
}

RB_VIEW.calls = async el => {
  const f = RB.f || {};
  const [{items}, camps] = await Promise.all([rbGet('/calls', f), rbGet('/campaigns')]);
  el.innerHTML = `<div class="panel"><h2>Звонки <span class="muted">· ${items.length}${items.length >= 500 ? ' последних' : ''}</span></h2>
    ${rbFilters(camps.items)}
    <div class="row gapb"><a class="btn small" href="${esc(rbq('/calls.csv', f))}" download>Скачать CSV</a>
      <span class="muted">Записи разговоров хранятся ${RB.st.recording_days} дней.</span></div>
    ${items.length ? `<div class="scroll"><table class="tbl"><thead><tr><th>Дата</th><th>Телефон</th><th>Статус</th><th class="n">Длит.</th><th class="hide-m">Кампания</th><th class="n hide-m">Стоимость</th><th>Запись</th></tr></thead><tbody>
      ${items.map((c, i) => `<tr class="click" data-call="${i}"><td>${fmtWhen(c.at)}</td><td>${esc(phone(c.phone))}</td>
        <td><span class="chip ${c.good ? 'good' : c.outcome_key === 'do_not_call' ? 'bad' : ''}">${esc(c.outcome)}</span>${c.comment ? `<div class="muted">${esc(c.comment)}</div>` : ''}</td>
        <td class="n">${c.seconds ? dur(c.seconds) : ''}</td><td class="hide-m">${esc(c.campaign || 'вручную')}</td><td class="n hide-m">${c.cost ? money(c.cost) : ''}</td>
        <td>${c.recording ? `<button class="btn small" data-play="${i}">▶</button>` : ''}</td></tr>`).join('')}</tbody></table></div>`
      : '<div class="empty">Звонков по этим условиям нет.</div>'}</div>`;
  bindFilters(el);
  $$('[data-call]', el).forEach(tr => tr.addEventListener('click', e => { if (!e.target.closest('[data-play]')) rbCallCard(items[+tr.dataset.call]); }));
  $$('[data-play]', el).forEach(b => b.addEventListener('click', () => rbCallCard(items[+b.dataset.play], true)));
};

function rbCallCard(c, autoplay = false) {
  const box = modal(`<header class="rvm-head"><div><span class="rvm-chip ${c.good ? 'good' : ''}">${esc(c.outcome)}</span>
      <h2>Робот · ${esc(phone(c.phone))}</h2><div class="rvm-mut">${fmtWhen(c.at)}${c.seconds ? ' · ' + dur(c.seconds) : ''}${c.campaign ? ' · ' + esc(c.campaign) : ''}${c.cost ? ' · ' + money(c.cost) : ''}</div></div>
      <button class="rvm-x" aria-label="Закрыть">×</button></header>
    <div class="rvm-body">${c.recording ? `<section><h3>Запись</h3><audio class="rec" controls preload="none" src="${esc(rbq('/recording/' + encodeURIComponent(c.id)))}"></audio></section>` : ''}
      ${c.comment ? `<section><h3>Итог</h3><div>${esc(c.comment)}</div></section>` : ''}
      ${Object.keys(c.facts).length ? `<section><h3>Что узнал</h3><dl class="rvm-kv">${Object.entries(c.facts).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl></section>` : ''}
      <section><h3>Разговор</h3>${c.transcript.length ? c.transcript.map(l => `<div class="rvm-line"><b>${esc(l.who)}</b> ${esc(l.text)}</div>`).join('') : '<div class="rvm-mut">Разговора не было.</div>'}</section></div>`);
  if (autoplay) $('audio', box)?.play().catch(() => {});
}

// ---------- аналитика ----------
RB_VIEW.analytics = async el => {
  const f = RB.f || {};
  const [a, camps] = await Promise.all([rbGet('/analytics', f), rbGet('/campaigns')]);
  const max = Math.max(1, ...a.days.map(d => d.calls));
  const endMax = Math.max(1, ...a.ends.map(e => e.count));
  const director = RB.st.role === 'director';
  el.innerHTML = `${rbFilters(camps.items, false)}<div class="kpis">
      <div class="kpi"><b>${a.total}</b><span>звонков</span></div>
      <div class="kpi"><b>${a.dial_rate}%</b><span>дозвон · ${a.reached} разговоров</span></div>
      <div class="kpi"><b>${a.conversion}%</b><span>конверсия в лид</span></div>
      <div class="kpi"><b>${a.leads}</b><span>лидов (заинтересованы)</span></div>
      <div class="kpi"><b>${a.priced ? money(a.lead_cost) : '—'}</b><span>цена лида${director && RB.owner ? ' (себестоимость)' : ''}</span></div>
      <div class="kpi"><b>${a.priced ? money(a.cost) : '—'}</b><span>${a.minutes} мин разговоров${director && RB.owner && a.priced ? ` · клиенту ${money(a.client_cost)}` : ''}</span></div></div>
    ${!a.priced && director ? '<p class="muted">Стоимость появится, когда вы впишете цены во вкладке «Цены и клиенты».</p>' : ''}
    <div class="grid2"><div class="panel"><h2>Звонки по дням</h2>${a.days.length ? `<div class="bars">${a.days.slice(-31).map(d =>
        `<div class="bar" title="${ddmm(d.day)}: ${d.calls} звонков, ${d.reached} дозвон, ${d.leads} лидов"><div class="stackbar"><i data-h="${d.reached / max * 100}"></i><i class="b2" data-h="${(d.calls - d.reached) / max * 100}"></i></div><span>${ddmm(d.day)}</span></div>`).join('')}</div>
        <p class="muted">Зелёным разговоры, серым недозвоны.</p>` : '<div class="empty">Звонков пока нет.</div>'}</div>
    <div class="panel"><h2>Чем заканчивались звонки</h2>${a.total ? a.ends.map(e => `<div class="rb-end"><span>${esc(e.name)}</span><b>${e.count}</b>
        <div class="rb-endbar"><i data-w="${e.count / endMax * 100}"></i></div></div>`).join('') : '<div class="empty">Звонков пока нет.</div>'}</div></div>`;
  heights(el);
  $$('[data-w]', el).forEach(x => { x.style.width = x.dataset.w + '%'; });
  bindFilters(el);
};

// ---------- интеграции ----------
RB_VIEW.integrations = async el => {
  const sheet = RB.st.sheet;
  el.innerHTML = `<div class="panel"><h2>Интеграции</h2>
    <p class="muted">Здесь только то, что робот реально использует сейчас.</p>
    <table class="tbl"><tbody>
      <tr><td><b>CRM Averon</b><div class="muted">Звонки, записи и итоги робота попадают сюда, во вкладки «Звонки» и «Аналитика».</div></td><td><span class="chip good">работает</span></td></tr>
      ${sheet ? `<tr><td><b>Google Таблица</b><div class="muted">Итог каждого звонка робота строкой на лист «${esc(sheet.tab)}».</div></td>
        <td><span class="chip ${sheet.on ? 'good' : ''}">${sheet.on ? 'работает' : 'не подключена'}</span></td></tr>` : ''}
      <tr><td><b>Mango Office</b><div class="muted">Робот звонит через сотрудника «Робот» в Mango.</div></td><td><span class="chip good">работает</span></td></tr>
    </tbody></table></div>`;
};

// ---------- цены и клиенты (директор) ----------
RB_VIEW.settings = async el => {
  const o = RB.st.options;
  let known = [];
  try { known = (await rbGet('/known-clients')).clients; } catch (e) { /* таблица недоступна */ }
  const names = [...new Set([...known, ...o.clients])];
  const p = o.prices;
  el.innerHTML = `<div class="grid2"><div class="panel"><h2>Себестоимость</h2>
      <p class="muted">Из этого считается стоимость звонков и цена лида. Клиент видит себестоимость, умноженную на коэффициент.</p>
      <div class="op-grid">
        <label class="field">Минута Mango, ₽<input data-p="mango_min" value="${p.mango_min}" inputmode="decimal"${rbRO()}></label>
        <label class="field">Синтез ElevenLabs, ₽ за 1000 символов<input data-p="eleven_1k_chars" value="${p.eleven_1k_chars}" inputmode="decimal"${rbRO()}></label>
        <label class="field">Модель разговора, ₽ за 1000 токенов<input data-p="llm_1k_tokens" value="${p.llm_1k_tokens}" inputmode="decimal"${rbRO()}></label>
        <label class="field">Коэффициент для клиента<input data-p="multiplier" value="${p.multiplier}" inputmode="decimal"${rbRO()}></label>
      </div><div class="row"><button class="btn primary" id="pr-save"${rbRO()}>Сохранить цены</button></div>
      <p class="muted">Минуты считаются по длительности разговора, синтез по символам, которые робот озвучил (повторяющиеся фразы берутся из запаса и не оплачиваются), модель по токенам.</p></div>
    <div class="panel"><h2>Кабинет робота у клиентов</h2>
      <p class="muted">Платная опция. Включённый клиент видит в своём кабинете вкладку «Робот»: свои операторы, кампании, контакты, звонки и аналитику со стоимостью ×${p.multiplier}.</p>
      ${names.length ? names.map(n => `<label class="rb-check"><input type="checkbox" data-cl="${esc(n)}"${o.clients.includes(n) ? ' checked' : ''}${rbRO()}> ${esc(n)}</label>`).join('')
        : '<div class="empty">Клиентов в таблице «Прозвон лидов» не нашлось.</div>'}
      <div class="row"><button class="btn primary" id="cl-save"${rbRO()}>Сохранить</button></div></div></div>`;
  $('#pr-save', el).addEventListener('click', async () => {
    const prices = {}; $$('[data-p]', el).forEach(i => { prices[i.dataset.p] = i.value; });
    try { await rbPost('/options', {prices}); toast('Цены сохранены.'); go('robot'); } catch (e) { toast(e.message, true); }
  });
  $('#cl-save', el).addEventListener('click', async () => {
    try { await rbPost('/options', {clients: $$('[data-cl]:checked', el).map(i => i.dataset.cl)}); toast('Сохранено.'); go('robot'); } catch (e) { toast(e.message, true); }
  });
};

// ---------- голос и тестовый звонок: прежняя страница робота ----------
RB_VIEW.voice = async el => { await RB.voiceView(el); };
