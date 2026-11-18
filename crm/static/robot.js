// Раздел «Робот»: кампании, номера (контакты и стоп-лист), звонки с итогами, операторы; у директора ещё голос и настройки.
// Работает поверх app.js (его помощники esc, $, $$, api, modal, toast, phone, dur, fmtWhen, reload).
'use strict';

const RB = {tab: 'campaigns', owner: '', st: null, f: {}};
const RB_DAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
const RB_COSTS = [['mango_min', 'Минута Mango', 'за минуту'], ['eleven_1k_chars', 'Синтез ElevenLabs', 'за 1000 символов'], ['llm_1k_tokens', 'Модель YandexGPT', 'за 1000 токенов']];
// Шесть вкладок; клиент видит первые четыре.
const RB_TABS = [
  ['campaigns', 'Кампании и номера'], ['calls', 'Звонки'], ['operators', 'Операторы'],
  ['replies', 'Библиотека ответов', 'director'],
  ['voice', 'Голос', 'director'], ['settings', 'Настройки', 'director'],
];
const RB_OLD_TAB = {numbers: 'campaigns', contacts: 'campaigns', stop: 'campaigns', analytics: 'calls', integrations: 'settings'};
try { const saved = (window.AveronLearningWorkspace ? store.get('crm-robot', {}) : JSON.parse(localStorage.getItem('crm-robot') || '{}')); RB.tab = RB_OLD_TAB[saved.tab] || saved.tab || RB.tab; RB.owner = saved.owner || ''; } catch (e) { /* без хранилища */ }

const rbq = (path, extra = {}) => '/api/robot/cab' + path + qs({owner: RB.owner, ...extra});
const rbGet = (path, extra) => api(rbq(path, extra));
const rbPost = (path, body = {}) => api('/api/robot/cab' + path, {method: 'POST', body: {...body, owner: RB.owner}});
const rbRO = () => RB.st.readonly ? ' disabled' : '';
const rbSave = () => store.set('crm-robot', {tab: RB.tab, owner: RB.owner});
const money = v => v == null ? '—' : `${Number(v).toLocaleString('ru-RU', {maximumFractionDigits: 2})} ₽`;
const opName = id => (RB.st.operators || []).find(o => o.id === id)?.name || 'Оператор не указан';

VIEWS.robot = async (view, arg) => {
  const oldNumbers = ['numbers', 'contacts', 'stop'].includes(arg);
  const tab = RB_OLD_TAB[arg] || arg;
  if (tab && RB_TABS.some(t => t[0] === tab)) RB.tab = tab;
  RB.st = await loading(view, api(rbq('')).catch(async e => {
    if (RB.owner) { RB.owner = ''; rbSave(); return api(rbq('')); }
    throw e;
  }), 'page');
  if (!RB.st.enabled) {
    view.innerHTML = `<div class="page"><section class="card blk"><div class="card-h"><h2>Голосовой робот</h2></div><p class="lead">${esc(RB.st.paid_note)}</p>
      <p class="hint">Робот сам обзванивает ваши номера по расписанию, выясняет интерес и записывает итог каждого звонка. Вы видите звонки, записи разговоров и аналитику здесь.</p></section></div>`;
    return;
  }
  const tabs = RB_TABS.filter(t => !t[2] || (t[2] === RB.st.role && RB.st.home !== false)); // голос и настройки — нашего робота
  if (!tabs.some(t => t[0] === RB.tab)) RB.tab = 'campaigns';
  const owners = RB.st.owners.length > 1 && RB.tab !== 'voice' && RB.tab !== 'settings'
    ? `<select class="inp" id="rb-owner" aria-label="Чей кабинет">${RB.st.owners.map(o => `<option value="${esc(o.id)}"${o.id === RB.st.owner ? ' selected' : ''}>${esc(o.name)}</option>`).join('')}</select>` : '';
  setHead({filters: owners});
  $('#rb-owner')?.addEventListener('change', e => { RB.owner = e.target.value; rbSave(); reload(); });
  view.innerHTML = `<div class="page">${tabsHtml(tabs.map(([k, t]) => [k, t]), RB.tab)}<div class="page" id="rb-body"></div></div>`;
  $$('[data-tab]', view).forEach(b => b.addEventListener('click', () => { RB.tab = b.dataset.tab; rbSave(); go('robot', RB.tab, {replace: true}); }));
  const body = $('#rb-body', view);
  try {
    if (RB.tab === 'campaigns') {
      body.innerHTML = '<div data-campaigns></div><div id="rb-numbers"></div>';
      const panels = [[RB_VIEW.campaigns, $('[data-campaigns]', body)], [RB_VIEW.numbers, $('#rb-numbers', body)]];
      await Promise.all(panels.map(([render, el]) => render(el).catch(e => { el.innerHTML = errCard(e.message); })));
      if (oldNumbers && view.isConnected) $('#rb-numbers', body).scrollIntoView();
    } else {
      await RB_VIEW[RB.tab](body);
    }
  } catch (e) { body.innerHTML = emptyCard(esc(e.message)); }
};

const RB_VIEW = {};

// ---------- операторы ----------
RB_VIEW.operators = async el => {
  const ops = RB.st.operators;
  el.innerHTML = `<section class="card blk"><div class="card-h"><h2>Операторы</h2><span class="hint">${ops.length}</span><span class="sp"></span>
      <button class="btn primary small" type="button" id="op-new"${rbRO()}>Новый оператор</button></div>
    <p class="lead" data-css="margin-bottom:12px">Оператор — это сценарий робота: как представляется, план разговора, правила и что записать. Встроенного оператора можно скопировать и править копию.</p>
    ${ops.length ? `<div class="tbl-wrap"><table class="tbl anim"><thead><tr><th>Имя</th><th>Компания</th><th class="hide-m">Цель</th><th class="n hide-m">План / правила / аргументы</th><th></th></tr></thead><tbody>
      ${ops.map((o, i) => `<tr class="click" data-op="${esc(o.id)}" data-css="--r:${i}"><td class="nw"><b>${esc(o.name)}</b>${o.builtin ? ' <span class="chip">встроенный</span>' : ''}</td>
        <td>${esc(o.company)}</td><td class="hide-m s wrap">${esc(o.goal)}</td><td class="n hide-m">${o.plan} / ${o.rules} / ${o.facts}</td>
        <td><button class="btn small" type="button" data-copy="${esc(o.id)}"${rbRO()}>Копия</button></td></tr>`).join('')}</tbody></table></div>`
      : '<div class="empty">Операторов пока нет.</div>'}</section>`;
  $('#op-new', el).addEventListener('click', () => operatorForm(null));
  $$('[data-op]', el).forEach(tr => tr.addEventListener('click', e => { if (!e.target.closest('[data-copy]')) operatorForm(tr.dataset.op); }));
  $$('[data-copy]', el).forEach(b => b.addEventListener('click', () => operatorForm(null, b.dataset.copy)));
};

async function operatorForm(id, copyFrom) {
  const src = id || copyFrom ? await rbGet('/operators/' + encodeURIComponent(id || copyFrom)) : {
    name: '', gender: 'female', company: '', lead_source: '', goal: '', greeting: 'Здравствуйте, ', farewell: 'Спасибо, хорошего дня.', example: '',
    transfer_to: '', plan: [''], rules: [], facts: [], fields: [], max_items: 10};
  const builtin = id && src.builtin;
  const ro = RB.st.readonly || (builtin && RB.st.home === false) ? ' disabled' : ''; // встроенный общий: другая компания правит копию
  const list = (key, title, hint) => `<section class="op-list" data-list="${key}"><h3>${title} <span class="muted">до ${src.max_items || 10}</span></h3>
    <p class="muted">${hint}</p><div class="op-items">${(src[key].length ? src[key] : (key === 'plan' ? [''] : [])).map(v => opItem(v, ro)).join('')}</div>
    ${ro ? '' : '<button class="btn small" data-add>+ Добавить</button>'}</section>`;
  const box = modal(`<header class="rvm-head"><div><h2>${id ? (builtin ? 'Встроенный оператор' : 'Оператор') : copyFrom ? 'Копия оператора' : 'Новый оператор'}</h2>
      <div class="rvm-mut">${builtin ? (RB.st.home === false ? 'Образец: чтобы поменять, сделайте копию.' : src.edited ? 'Изменён в CRM. «Вернуть исходный» отменит правки.' : 'Правки сразу идут во все его кампании.') : 'Робот говорит только то, что здесь написано.'}</div></div>
      <button class="rvm-x" aria-label="Закрыть">×</button></header>
    <div class="rvm-body op-form">
      ${OP_GUIDE}
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
      <label class="field">Образец живого разговора<textarea data-f="example" rows="8" placeholder="Клиент: Да, актуален.&#10;Мария: Покупку в ближайшие пару месяцев планируете?"${ro}>${esc(src.example || '')}</textarea></label>
      <p class="muted">Как звучит хороший звонок: короткие реплики «Клиент: …» и «${esc(src.name || 'Робот')}: …». Робот берёт из него тон и длину фраз, а не текст слово в слово.</p>
      ${list('plan', 'План разговора', 'Шаги по порядку: что выяснить и когда завершить.')}
      ${list('rules', 'Правила', 'Как вести себя в особых случаях: занят, просит не звонить, спрашивает цену.')}
      ${list('facts', 'Аргументы и факты', 'Что робот может говорить о компании и предложении.')}
      <section class="op-list" data-list="fields"><h3>Что записать по итогу</h3>
        <p class="muted">Каждая строка — колонка в итогах звонка, в виде «Название: что записать».</p>
        <div class="op-items">${src.fields.map(f => opItem(f.text, ro, f.key)).join('')}</div>${ro ? '' : '<button class="btn small" data-add>+ Добавить</button>'}</section>
      <div class="row"><button class="btn primary" id="op-save"${ro}>Сохранить</button>
        ${builtin ? `<button class="btn" id="op-copy"${ro}>Сделать копию</button>` : ''}
        ${id && (!builtin || src.edited) ? `<button class="btn danger" id="op-del"${ro}>${builtin ? 'Вернуть исходный' : 'Удалить'}</button>` : ''}<span class="muted" id="op-st"></span></div>
    </div>`);
  box.classList.add('rvm-wide');
  $$('[data-add]', box).forEach(b => b.addEventListener('click', () => {
    const items = $('.op-items', b.closest('.op-list'));
    if (items.children.length >= (src.max_items || 10) && b.closest('[data-list]').dataset.list !== 'fields') return toast('Можно не больше 10 пунктов.', true);
    items.insertAdjacentHTML('beforeend', opItem('', ''));
    items.lastElementChild.querySelector('textarea').focus();
  }));
  box.addEventListener('click', e => { const x = e.target.closest('[data-rm]'); if (x) x.closest('.op-item').remove(); });
  if (!ro) $$('.op-items', box).forEach(opSortable);
  const collect = () => {
    const out = {};
    $$('[data-f]', box).forEach(i => { out[i.dataset.f] = i.value; });
    ['plan', 'rules', 'facts'].forEach(k => { out[k] = $$(`[data-list="${k}"] textarea`, box).map(t => t.value.trim()).filter(Boolean); });
    out.fields = $$('[data-list="fields"] textarea', box).map(t => ({key: t.dataset.key || '', text: t.value.trim()})).filter(f => f.text);
    return out;
  };
  $('#op-copy', box)?.addEventListener('click', () => { box.close(); operatorForm(null, id); });
  $('#op-save', box)?.addEventListener('click', async () => {
    try {
      await rbPost('/operators' + (id ? '/' + encodeURIComponent(id) : ''), {...collect(), copy_from: copyFrom || ''});
      box.close(); toast('Оператор сохранён. Робот возьмёт его со следующего звонка.'); reload();
    } catch (e) { $('#op-st', box).textContent = e.message; }
  });
  $('#op-del', box)?.addEventListener('click', async () => {
    if (!await askConfirm(builtin ? 'Вернуть исходного оператора? Правки из CRM пропадут, робот снова будет говорить по встроенной анкете.' : 'Удалить оператора?')) return;
    try { await rbPost('/operators/' + encodeURIComponent(id) + '/delete'); box.close(); if (builtin) toast('Исходная анкета возвращена.'); reload(); }
    catch (e) { $('#op-st', box).textContent = e.message; }
  });
}

function opItem(value, ro, key = '') {
  return `<div class="op-item">${ro ? '' : '<span class="op-grip" title="Перетащить" aria-label="Перетащить">⋮⋮</span>'}<textarea rows="2" data-key="${esc(key)}"${ro}>${esc(value)}</textarea>${ro ? '' : '<button class="btn small" data-rm aria-label="Убрать">×</button>'}</div>`;
}

// Перетаскивание пунктов за ⋮⋮ (мышь и палец): пункт едет за указателем, остальные плавно расступаются.
const opReduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;

function opFlip(items, move) {
  const before = new Map(items.map(el => [el, el.getBoundingClientRect().top]));
  move();
  if (opReduced()) return;
  items.forEach(el => {
    const dy = before.get(el) - el.getBoundingClientRect().top;
    if (!dy || el.classList.contains('dragging')) return;
    el.animate([{transform: `translateY(${dy}px)`}, {transform: 'none'}], {duration: 220, easing: 'cubic-bezier(.2,.8,.2,1)'});
  });
}

function opSortable(list) {
  list.addEventListener('pointerdown', e => {
    const grip = e.target.closest('.op-grip');
    if (!grip || e.button > 0) return;
    e.preventDefault();
    const item = grip.closest('.op-item');
    const startY = e.clientY, startSlot = item.offsetTop;
    const follow = y => { item.style.transform = `translateY(${y - startY - (item.offsetTop - startSlot)}px)`; };
    item.classList.add('dragging');
    list.classList.add('sorting');
    const onMove = ev => {
      const top = list.getBoundingClientRect().top;
      const next = [...list.children].find(el => el !== item && ev.clientY < top + el.offsetTop + el.offsetHeight / 2) || null;
      if (item.nextElementSibling !== next) opFlip([...list.children], () => list.insertBefore(item, next));
      follow(ev.clientY);
    };
    const onUp = () => {
      document.removeEventListener('pointermove', onMove);
      document.removeEventListener('pointerup', onUp);
      document.removeEventListener('pointercancel', onUp);
      const from = item.style.transform;
      item.style.transform = '';
      list.classList.remove('sorting');
      if (opReduced() || !from) return item.classList.remove('dragging');
      item.animate([{transform: from}, {transform: 'none'}], {duration: 200, easing: 'cubic-bezier(.2,.8,.2,1)'})
        .finished.finally(() => item.classList.remove('dragging'));
    };
    // слушаем документ, а не ⋮⋮: при перестановке пункт переезжает в DOM и захват указателя теряется
    document.addEventListener('pointermove', onMove);
    document.addEventListener('pointerup', onUp);
    document.addEventListener('pointercancel', onUp);
  });
  // с клавиатуры: Alt + стрелка в поле пункта двигает его вверх или вниз
  list.addEventListener('keydown', e => {
    if (!e.altKey || !['ArrowUp', 'ArrowDown'].includes(e.key) || !e.target.matches('textarea')) return;
    const item = e.target.closest('.op-item');
    const up = e.key === 'ArrowUp';
    if (!(up ? item.previousElementSibling : item.nextElementSibling)) return;
    e.preventDefault();
    opFlip([...list.children], () => list.insertBefore(item, up ? item.previousElementSibling : item.nextElementSibling.nextElementSibling));
    e.target.focus();
  });
}

const CAMPAIGN_GUIDE = `<details class="rb-more op-guide"><summary>Как запустить кампанию</summary><div class="op-guide-body">
  <ol>
    <li><b>Номера.</b> Сначала загрузите их в разделе «Кампании и номера»: файлом CSV или Excel либо из листов «Прозвон лидов». Звоните только тем, кто согласился на звонок.</li>
    <li><b>Оператор.</b> Выберите анкету, по которой робот будет говорить. Своего оператора можно завести на вкладке «Операторы». Перед запуском послушайте его: тестовый звонок делается на вкладке «Голос», а если её у вас нет, попросите менеджера Averon.</li>
    <li><b>Дни и часы.</b> Робот звонит только в эти часы по выбранному часовому поясу. Ставьте пояс клиентов, а не свой.</li>
    <li><b>Попытки и повтор.</b> Если не дозвонился или ответил автоответчик, робот перезвонит через указанное число минут, пока не кончатся попытки. Обычно хватает 2–3 попыток с перерывом в 2 часа.</li>
    <li><b>Запуск.</b> Сохраните, затем нажмите «Запустить» в списке кампаний. Робот звонит по одному номеру за раз, кампанию можно поставить на паузу.</li>
    <li><b>Итоги.</b> Разговоры, записи и данные по анкете появляются во вкладке «Звонки». Кто попросил не звонить, сам попадает в стоп-лист.</li>
  </ol>
</div></details>`;

const OP_GUIDE = `<details class="rb-more op-guide"><summary>Как правильно заполнить оператора</summary><div class="op-guide-body">
  <p>Робот говорит только то, что написано в анкете, и пересказывает это своими словами. Пишите коротко и так, как сказали бы вслух.</p>
  <ol>
    <li><b>Имя, компания, откуда номер.</b> Как робот представится («Мария, кухни на заказ») и одной фразой, где клиент оставлял заявку: робот скажет это, если спросят «откуда у вас мой номер».</li>
    <li><b>Цель.</b> Чем должен закончиться удачный звонок, например «договориться о звонке специалиста».</li>
    <li><b>Приветствие.</b> Представиться, напомнить о заявке и закончить простым вопросом, на который легко ответить «да».</li>
    <li><b>План.</b> Один шаг — один вопрос, по порядку. В шаге сразу пишите, когда завершать: «нет — вежливо завершить, итог not_relevant». Порядок меняется перетаскиванием за ⋮⋮. Если в копии встроенного оператора поменять план или приветствие, его готовые ответы без модели отключатся и звонок станет дороже.</li>
    <li><b>Правила.</b> Особые случаи: занят или за рулём, просит не звонить, автоответчик, спрашивает цену, «вы робот?».</li>
    <li><b>Аргументы и факты.</b> Только правда о компании и предложении. Чего здесь нет, робот не скажет: цены и скидки лучше оставить специалисту.</li>
    <li><b>Образец разговора.</b> 8–12 коротких реплик из хорошего живого звонка. Робот берёт из него тон и длину фраз и иногда повторяет фразы дословно, поэтому в образце не должно быть реплик, неуместных в другом звонке.</li>
    <li><b>Что записать по итогу.</b> Одна строка — одна колонка в итогах, варианты ответа в кавычках: «материал фасадов: «плёнка», «эмаль» или «не знает»».</li>
  </ol>
  <p class="muted">После сохранения позвоните себе на вкладке «Голос» и послушайте, ударения настраиваются там же, в «Произношении». Если вкладки «Голос» у вас нет, попросите менеджера Averon сделать тестовый звонок.</p>
</div></details>`;

// ---------- кампании ----------
RB_VIEW.campaigns = async el => {
  const {items} = await rbGet('/campaigns');
  el.innerHTML = `<section class="card blk"><div class="card-h"><h2>Кампании</h2><span class="hint">${items.length}</span><span class="sp"></span>
      <button class="btn primary small" type="button" id="cp-new"${rbRO()}>Новая кампания</button></div>
    <p class="lead" data-css="margin-bottom:12px">Кампания — это список номеров, оператор и расписание. Робот звонит сам в рабочие часы кампании и повторяет недозвоны. Звоните только тем, кто согласился на звонок.</p>
    ${items.length ? `<div class="tbl-wrap"><table class="tbl anim"><thead><tr><th>Кампания</th><th>Статус</th><th class="hide-m">Расписание</th><th class="n">Обзвонено</th><th class="n">Лиды</th><th></th></tr></thead><tbody>
      ${items.map((c, i) => `<tr class="click" data-ce="${c.id}" data-css="--r:${i}"><td><b>${esc(c.name)}</b><div class="s">${esc(opName(c.operator))}</div></td>
        <td><span class="chip ${c.status === 'running' ? 'good' : c.status === 'paused' ? 'warn' : ''}">${esc(c.status_name)}</span></td>
        <td class="hide-m nw">${c.days.map(d => RB_DAYS[d - 1]).join(' ')} · ${c.start_time}–${c.end_time}<div class="s">${esc(tzName(c.timezone))} · попыток ${c.attempts}</div></td>
        <td class="n nw">${c.done} из ${c.total}</td><td class="n">${c.interested}</td>
        <td>${c.status === 'running' ? `<button class="btn small" type="button" data-ca="pause" data-id="${c.id}"${rbRO()}>Пауза</button>`
          : c.status !== 'done' ? `<button class="btn small primary" type="button" data-ca="start" data-id="${c.id}"${rbRO()}>Запустить</button>` : ''}</td></tr>`).join('')}</tbody></table></div>`
      : '<div class="empty">Кампаний пока нет. Добавьте контакты в блоке «Номера для обзвона» ниже.</div>'}</section>`;
  $('#cp-new', el).addEventListener('click', () => campaignForm(null));
  $$('[data-ce]', el).forEach(tr => tr.addEventListener('click', e => { if (!e.target.closest('[data-ca]')) campaignForm(items.find(c => c.id === +tr.dataset.ce)); }));
  $$('[data-ca]', el).forEach(b => b.addEventListener('click', async () => {
    if (b.dataset.ca === 'start' && !await askConfirm('Запустить кампанию? Робот начнёт звонить по расписанию. Убедитесь, что у этих людей есть согласие на звонок.')) return;
    try { await rbPost(`/campaigns/${b.dataset.id}/${b.dataset.ca}`); reload(); } catch (e) { toast(e.message, true); }
  }));
};

const tzName = tz => (RB.st.timezones.find(([k]) => k === tz) || [tz, CRMText.zone(tz)])[1];

async function campaignForm(c) {
  const contacts = await rbGet('/contacts');
  const v = c || {name: '', operator: RB.st.operators[0]?.id || '', days: [1, 2, 3, 4, 5], start_time: '10:00', end_time: '19:00',
    timezone: 'Europe/Moscow', attempts: 2, retry_minutes: 120};
  const ro = RB.st.readonly ? ' disabled' : '';
  const box = modal(`<header class="rvm-head"><div><h2>${c ? esc(c.name) : 'Новая кампания'}</h2>
      ${c ? `<div class="rvm-mut">${esc(c.status_name)} · обзвонено ${c.done} из ${c.total} · лидов ${c.interested}</div>` : ''}</div>
      <button class="rvm-x" aria-label="Закрыть">×</button></header>
    <div class="rvm-body op-form">
      ${c || RB.st.readonly ? '' : CAMPAIGN_GUIDE}
      <label class="field">Название<input id="cf-name" value="${esc(v.name)}"${ro}></label>
      <label class="field">Оператор<select id="cf-op"${ro}>${RB.st.operators.length ? '' : '<option value="">Сначала заведите оператора на вкладке «Операторы»</option>'}${RB.st.operators.map(o => `<option value="${esc(o.id)}"${o.id === v.operator ? ' selected' : ''}>${esc(o.name)} · ${esc(o.company)}</option>`).join('')}</select></label>
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
  const close = () => box.close();
  const st = t => { $('#cf-st', box).textContent = t; };
  $('#cf-save', box).addEventListener('click', async () => {
    const body = {name: $('#cf-name', box).value, operator: $('#cf-op', box).value,
      days: $$('[data-day][aria-pressed="true"]', box).map(b => +b.dataset.day), start_time: $('#cf-start', box).value, end_time: $('#cf-end', box).value,
      timezone: $('#cf-tz', box).value, attempts: +$('#cf-att', box).value, retry_minutes: +$('#cf-retry', box).value};
    if ($('#cf-all', box).checked) body.contacts = 'all';
    try { await rbPost('/campaigns' + (c ? '/' + c.id : ''), body); close(); toast('Кампания сохранена.'); reload(); } catch (e) { st(e.message); }
  });
  $('#cf-targets', box)?.addEventListener('click', async () => {
    const {items} = await rbGet(`/campaigns/${c.id}/targets`);
    $('#cf-list', box).innerHTML = items.length ? `<div class="scroll"><table class="tbl"><thead><tr><th>Номер</th><th>Имя</th><th class="n">Попыток</th><th>Итог</th></tr></thead><tbody>
      ${items.map(t => `<tr><td>${esc(phone(t.phone))}</td><td>${esc(t.name)}</td><td class="n">${t.attempts}</td>
        <td>${t.done ? esc(CRMText.label(t.outcome,RB.st.outcomes,'Завершён')) : t.dispatching ? 'заявка отправляется' : t.next_at ? 'повтор ' + fmtWhen(t.next_at) : 'ждёт звонка'}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">Номеров нет.</div>';
  });
  for (const [sel, action, ask] of [['#cf-finish', 'finish', 'Завершить кампанию? Робот больше не будет по ней звонить.'], ['#cf-del', 'delete', 'Удалить кампанию вместе со списком номеров? Звонки останутся во вкладке «Звонки».']]) {
    $(sel, box)?.addEventListener('click', async () => {
      if (!await askConfirm(ask)) return;
      try { await rbPost(`/campaigns/${c.id}/${action}`); close(); reload(); } catch (e) { st(e.message); }
    });
  }
}

// ---------- контакты ----------
RB_VIEW.contacts = async el => {
  const selectionOwner = RB.owner, selectionQuery = RB.q || '';
  const [data, tabs] = await Promise.all([rbGet('/contacts', {q: selectionQuery}), rbGet('/contacts/tabs').catch(() => ({tabs: []}))]);
  if (!el.isConnected || RB.owner !== selectionOwner) return;
  el.innerHTML = `<div class="grid2"><section class="card blk"><div class="card-h"><h2>Номера для обзвона</h2><span class="hint">${fmt(data.total)}</span></div>
      <div class="row gapb"><input class="inp" id="ct-q" type="search" placeholder="Номер, имя или заметка" value="${esc(RB.q || '')}"><button class="btn" type="button" id="ct-find">Найти</button>
        <button class="btn" type="button" id="ct-all"${data.total ? '' : ' disabled'}>Выделить все</button><span class="hint" id="ct-count" aria-live="polite">Выбрано: 0</span>
        ${data.items.length ? `<button class="btn danger small" type="button" id="ct-del"${rbRO()}>Удалить отмеченные</button>` : ''}</div>
      ${data.items.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th></th><th>Номер</th><th>Имя</th><th class="hide-m">Заметка</th><th class="hide-m">Откуда</th></tr></thead><tbody>
        ${data.items.map(c => `<tr><td><input type="checkbox" data-ct="${c.id_exact || c.id}" aria-label="Отметить"></td><td class="num nw">${esc(phone(c.phone))}${c.stopped ? ' <span class="chip bad">стоп</span>' : ''}</td>
          <td>${esc(c.name)}</td><td class="hide-m s">${esc(c.note)}</td><td class="hide-m s">${esc(c.source)}</td></tr>`).join('')}</tbody></table></div>
        ${data.items.length < data.total ? `<p class="hint">Показаны последние ${data.items.length}. Остальные найдутся поиском.</p>` : ''}`
        : '<div class="empty">Номеров пока нет. Загрузите файл или возьмите номера с листа таблицы.</div>'}</section>
    <div class="stack"><section class="card blk" data-css="--i:1"><div class="card-h"><h2>Загрузить файл</h2></div>
      <p class="lead" data-css="margin-bottom:10px">CSV или Excel (.xlsx). Номер ищем по заголовку «Телефон», имя по «Имя» или «ФИО», заметку по «Комментарий».</p>
      <input type="file" id="ct-file" accept=".csv,.txt,.xlsx"${rbRO()}><div class="hint" id="ct-st"></div></section>
      <section class="card blk" data-css="--i:2"><div class="card-h"><h2>Из таблицы «Прозвон лидов»</h2></div>
      ${tabs.tabs.length ? `<div class="row"><select class="inp" id="ct-tab" aria-label="Лист">${tabs.tabs.map(t => `<option>${esc(t)}</option>`).join('')}</select>
        <button class="btn" type="button" id="ct-sheet"${rbRO()}>Взять номера</button></div><div class="hint" id="ct-st2"></div>` : '<div class="empty">Листы таблицы сейчас недоступны.</div>'}</section></div></div>`;
  const find = () => { RB.q = $('#ct-q', el).value; reload(); };
  $('#ct-find', el).addEventListener('click', find);
  $('#ct-q', el).addEventListener('keydown', e => { if (e.key === 'Enter') find(); });
  const selected = new Set();
  let selectedAll = false;
  const updateSelection = () => {
    $$('[data-ct]', el).forEach(i => { i.checked = selected.has(i.dataset.ct); });
    $('#ct-count', el).textContent = `Выбрано: ${fmt(selected.size)}`;
    $('#ct-all', el).textContent = selectedAll ? 'Снять выделение' : 'Выделить все';
  };
  $$('[data-ct]', el).forEach(i => i.addEventListener('change', () => {
    if (i.checked) selected.add(i.dataset.ct); else { selected.delete(i.dataset.ct); selectedAll = false; }
    updateSelection();
  }));
  $('#ct-all', el).addEventListener('click', async e => {
    if (selectedAll) { selected.clear(); selectedAll = false; updateSelection(); return; }
    const button = e.currentTarget;
    button.disabled = true;
    try {
      const result = await api('/api/robot/cab/contacts/ids' + qs({owner: selectionOwner, q: selectionQuery}));
      if (!el.isConnected || RB.owner !== selectionOwner) return;
      selected.clear();
      result.ids.forEach(id => selected.add(String(id)));
      selectedAll = selected.size > 0;
      updateSelection();
    } catch (err) { if (el.isConnected && RB.owner === selectionOwner) toast(err.message, true); }
    finally { if (el.isConnected) button.disabled = false; }
  });
  $('#ct-del', el)?.addEventListener('click', async () => {
    const ids = [...selected];
    if (!ids.length) return toast('Отметьте контакты.', true);
    if (!await askConfirm(`Удалить ${plainItems(ids.length, 'контакт', 'контакта', 'контактов')}? Из уже созданных кампаний номера не пропадут.`)) return;
    if (!el.isConnected || RB.owner !== selectionOwner) return;
    try {
      const ids36 = ids.map(id => BigInt(id).toString(36)).join(',');
      await api('/api/robot/cab/contacts/delete', {method: 'POST', body: {ids36, owner: selectionOwner}});
      if (el.isConnected && RB.owner === selectionOwner) reload();
    } catch (e) { if (el.isConnected && RB.owner === selectionOwner) toast(e.message, true); }
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
      reload();
    } catch (err) { st.textContent = err.message; }
  });
  $('#ct-sheet', el)?.addEventListener('click', async () => {
    const st = $('#ct-st2', el); st.textContent = 'Читаю лист…';
    try { const r = await rbPost('/contacts/sheet', {tab: $('#ct-tab', el).value}); toast(`Добавлено номеров: ${r.added}. Всего в кабинете ${r.total}.`); reload(); }
    catch (e) { st.textContent = e.message; }
  });
};

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

// ---------- стоп-лист: в «Номерах», под «Подробнее» ----------
async function stopBlock(el) {
  const {items} = await rbGet('/stop');
  el.innerHTML = moreBlock('Стоп-лист', `${items.length} ${plural(items.length, 'номер', 'номера', 'номеров')} · по ним робот не звонит`, `
    <p class="hint" data-css="margin:0">По этим номерам робот не звонит ни в одной кампании. Кто просит робота больше не звонить, попадает сюда сам.</p>
    <div class="grid2"><div>${items.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Номер</th><th>Причина</th><th class="hide-m">Когда</th><th></th></tr></thead><tbody>
      ${items.map(x => `<tr><td class="num nw">${esc(phone(x.phone))}</td><td>${esc(x.reason)}</td><td class="hide-m nw">${fmtWhen(x.at)}</td>
        <td><button class="btn small" type="button" data-unstop="${esc(x.phone)}"${rbRO()}>Убрать</button></td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">Стоп-лист пуст.</div>'}</div>
    <div><label class="field">Добавить номера, по одному в строке<textarea id="sl-phones" rows="5"${rbRO()}></textarea></label>
      <label class="field" data-css="margin-top:10px">Причина<input id="sl-reason" placeholder="просил не звонить"${rbRO()}></label>
      <div class="row" data-css="margin-top:10px"><button class="btn primary" type="button" id="sl-add"${rbRO()}>Добавить в стоп-лист</button></div></div></div>`, 2, RB.stopOpen);
  $('details', el).addEventListener('toggle', e => { RB.stopOpen = e.target.open; });
  $('#sl-add', el).addEventListener('click', async () => {
    try { const r = await rbPost('/stop', {phones: $('#sl-phones', el).value, reason: $('#sl-reason', el).value}); toast(`В стоп-лист добавлено: ${r.added}`); reload(); }
    catch (e) { toast(e.message, true); }
  });
  $$('[data-unstop]', el).forEach(b => b.addEventListener('click', async () => {
    try { await rbPost('/stop/remove', {phone: b.dataset.unstop}); reload(); } catch (e) { toast(e.message, true); }
  }));
}
RB_VIEW.numbers = async el => {
  el.innerHTML = '<div data-ct></div><div data-stop></div>';
  await Promise.all([RB_VIEW.contacts($('[data-ct]', el)), stopBlock($('[data-stop]', el))]);
};

// ---------- звонки ----------
function rbFilters(campaigns, withOutcome = true) {
  const f = RB.f || {};
  return `<div class="row gapb rb-filters">
    <select class="inp" id="rf-camp" aria-label="Кампания"><option value="">Все кампании</option>${campaigns.map(c => `<option value="${c.id}"${String(c.id) === String(f.campaign || '') ? ' selected' : ''}>${esc(c.name)}</option>`).join('')}</select>
    ${withOutcome ? `<select class="inp" id="rf-out" aria-label="Итог"><option value="">Все статусы</option>${Object.entries(RB.st.outcomes).map(([k, t]) => `<option value="${k}"${k === f.outcome ? ' selected' : ''}>${esc(t)}</option>`).join('')}</select>` : ''}
    <label class="field">с<input class="inp" type="date" id="rf-from" value="${esc(f.from || '')}"></label><label class="field">по<input class="inp" type="date" id="rf-to" value="${esc(f.to || '')}"></label>
    <button class="btn" type="button" id="rf-go">Показать</button></div>`;
}
function bindFilters(el) {
  $('#rf-go', el).addEventListener('click', () => {
    RB.f = {campaign: $('#rf-camp', el).value, outcome: $('#rf-out', el)?.value ?? RB.f?.outcome ?? '', from: $('#rf-from', el).value, to: $('#rf-to', el).value};
    reload();
  });
}

RB_VIEW.calls = async el => {
  const f = RB.f || {};
  const [{items}, camps, an] = await Promise.all([rbGet('/calls', f), rbGet('/campaigns'), rbGet('/analytics', {campaign: f.campaign, from: f.from, to: f.to})]);
  const dir = RB.st.role === 'director';
  const ends = an.ends.filter(e => e.count);
  el.innerHTML = `<div class="kpis">${[
      {key: 'rb-total', label: 'Звонков', value: an.total, sub: `${fmt(an.minutes)} мин разговоров`},
      {key: 'rb-rate', label: 'Дозвон', value: an.dial_rate, kind: 'pct', sub: `${plainItems(an.reached, 'разговор', 'разговора', 'разговоров')}`},
      {key: 'rb-leads', label: 'Лидов', value: an.leads, sub: `конверсия ${pctTxt(an.conversion)}`, help: 'Клиент заинтересован: робот записал интерес.'},
      {key: 'rb-cost', label: dir && RB.owner ? 'Цена лида (себестоимость)' : 'Цена лида', value: an.priced ? an.lead_cost : 0, kind: 'rub',
        sub: an.priced ? `всего ${money(an.cost)}${dir && RB.owner ? ` · клиент платит ${an.client_cost == null ? '— цена не задана' : money(an.client_cost)}` : ''}` : (dir ? 'цены ещё не посчитались' : '')}].map((k, i) => kpiHtml(k, i)).join('')}</div>
    <section class="card blk" data-css="--i:4">
      ${ends.length ? `<div class="filters" data-css="margin-bottom:12px"><button type="button" data-out="" aria-pressed="${!f.outcome}">Все<span class="c">${an.total}</span></button>${ends.map(e => {
        const key = e.key || Object.keys(RB.st.outcomes).find(k => RB.st.outcomes[k] === e.name) || '';
        return `<button type="button" data-out="${esc(key)}" aria-pressed="${!!key && key === f.outcome}"${key ? '' : ' disabled'}>${esc(e.name)}<span class="c">${e.count}</span></button>`;
      }).join('')}</div>` : ''}
      ${rbFilters(camps.items, false)}
      <div class="row" data-css="margin-bottom:12px"><a class="btn small" href="${esc(rbq('/calls.csv', f))}" download>Скачать CSV</a>
        <span class="hint">Записи разговоров хранятся ${RB.st.recording_days} дней.</span></div>
    ${items.length ? `<div class="tbl-wrap"><table class="tbl anim"><thead><tr><th>Когда</th><th>Телефон</th><th>Итог</th><th class="n hide-m">Длит.</th><th class="hide-m">Кампания</th><th class="n hide-m">Стоимость</th><th></th></tr></thead><tbody>
      ${items.map((c, i) => `<tr class="click" data-call="${i}" data-css="--r:${Math.min(i, 30)}"><td class="nw">${fmtWhen(c.at)}</td><td class="num nw">${esc(phone(c.phone))}</td>
        <td><span class="chip ${c.good ? 'good' : c.outcome_key === 'do_not_call' ? 'bad' : ''}">${esc(c.outcome)}</span>${c.comment ? `<div class="s">${esc(c.comment)}</div>` : ''}</td>
        <td class="n hide-m">${c.seconds ? dur(c.seconds) : ''}</td><td class="hide-m">${esc(c.campaign || 'вручную')}</td><td class="n hide-m">${c.cost ? money(c.cost) : ''}</td>
        <td>${c.recording ? `<button class="btn small" type="button" data-play="${i}">▶</button>` : ''}</td></tr>`).join('')}</tbody></table></div>
      ${items.length >= 500 ? '<p class="hint">Показаны последние 500.</p>' : ''}`
      : '<div class="empty">Звонков по этим условиям нет.</div>'}</section>`;
  countUp(el);
  bindFilters(el);
  $$('[data-out]', el).forEach(b => b.addEventListener('click', () => { RB.f = {...f, outcome: b.dataset.out}; reload(); }));
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

// ---------- цены, клиенты и интеграции (директор) ----------
const rbNum = v => { const n = parseFloat(String(v ?? '').replace(',', '.').replace(/\s/g, '')); return Number.isFinite(n) ? n : null; };

RB_VIEW.settings = async el => {
  const o = RB.st.options;
  let known = [];
  try { known = (await rbGet('/known-clients')).clients; } catch (e) { /* таблица недоступна */ }
  const p = o.prices;
  const econ = o.econ;
  const names = [...new Set([...known, ...o.clients, ...Object.keys(p.client_min || {})])];
  const month = econ.clients.reduce((m, c) => (m[c.name] = c, m), {});
  const since = `с 1 ${MONTHS[new Date(econ.month + 'T12:00:00Z').getUTCMonth()]}`;
  const sheet = RB.st.sheet;
  const auto = o.auto || {};
  const costRow = ([key, title, unit]) => {
    const a = auto[key] || {};
    return `<div class="item"><div class="main-l"><div class="t">${title}</div>
        <div class="s wrap-s">${a.note ? esc(a.note) + (a.at ? ' · обновлено ' + fmtWhen(a.at) : '') : 'ещё не считалось'}</div>
        ${a.error ? `<div class="s bad-text wrap-s">Не обновилось${a.tried ? ' ' + fmtWhen(a.tried) : ''}: ${esc(CRMText.error(a.error,'Не удалось обновить данные. Попробуйте позже.'))}</div>` : ''}</div>
      <div class="r"><b class="num">${a.value == null ? '—' : money(a.value)}</b><div class="s">${unit}</div></div></div>`;
  };
  const pt = econ.parts;
  const FRESH_MIN = 10;
  const minuteNote = econ.sample_minutes
    ? `Распознавание ${money(pt.asr)} + синтез ${money(pt.synth)} + модель ${money(pt.llm)}, без Mango. ${econ.estimate ? `Расход на минуту: ${fmt(econ.chars_min)} символов и ${fmt(econ.tokens_min)} токенов. Токены — оценка: с 22.10 простые ответы идут без модели, по автотесту это втрое меньше; точная цифра появится после ${FRESH_MIN} минут новых звонков.` : `Расход на минуту по звонкам робота с 22.10 (заготовки без модели), не старше 30 дней: ${fmt(econ.chars_min)} символов и ${fmt(econ.tokens_min)} токенов.`}`
    : `Пока только распознавание: у робота ещё нет разговоров за 30 дней, чтобы посчитать расход синтеза и модели на минуту. Mango не входит.`;
  const ty = econ.typical;
  const firstPrice = Object.values(p.client_min || {})[0];
  const calcField = (key, label, value, unit) => `<label class="field"><span>${label}</span><span class="rb-unit"><input class="inp" data-calc="${key}" inputmode="decimal" value="${value ?? ''}"><i>${unit}</i></span></label>`;
  el.innerHTML = `<div class="grid2 even"><section class="card blk"><div class="card-h"><h2>Себестоимость</h2><span class="hint">считается сама раз в 6 часов</span></div>
      <div class="list">${RB_COSTS.map(costRow).join('')}
        <div class="item rb-total"><div class="main-l"><div class="t">Минута разговора робота</div><div class="s wrap-s">${minuteNote}</div></div>
          <div class="r"><b class="num">${money(econ.cost_min)}</b><div class="s">наша себестоимость</div></div></div></div>
      <p class="hint">Минута Mango показана для справки, в себестоимость не входит. Руками эти цены не меняются: Mango считается по списаниям с баланса на минуты исходящих разговоров, ElevenLabs по подписке и курсу ЦБ, YandexGPT по прайсу Яндекс Облака.</p></section>
    <section class="card blk" data-css="--i:1"><div class="card-h"><h2>Калькулятор</h2><span class="hint">прикинуть цену для клиента</span></div>
      <div class="rb-calc">${calcField('calls', 'Звонков в месяц', ty.calls || 1000, 'шт')}${calcField('reach', 'Дозвон', ty.reach ?? 30, '%')}
        ${calcField('len', 'Средний разговор', ty.minutes_per_talk ?? 1.5, 'мин')}${calcField('price', 'Цена минуты для клиента', firstPrice ?? '', '₽')}</div>
      <div class="list" id="calc-out"></div></section></div>
    <section class="card blk" data-css="--i:2"><div class="card-h"><h2>Клиенты и цены</h2><span class="hint">цифры ${since}</span></div>
      <p class="lead" data-css="margin-bottom:8px">Платная опция. Включённый клиент видит в кабинете раздел «Робот» и стоимость своих звонков: минуты разговоров, посекундно, по его цене. Пока цена не задана, стоимость ему не показывается.</p>
      ${names.length ? `<div class="tbl-wrap"><table class="tbl rb-prices"><thead><tr><th>Клиент</th><th>Цена минуты</th><th class="n hide-m">Минут</th><th class="n">Клиент платит</th><th class="n hide-m">Себестоимость</th><th class="n">Прибыль</th></tr></thead><tbody>
        ${names.map(n => { const m = month[n]; const price = (p.client_min || {})[n];
          return `<tr><td><label class="rb-sw"><input type="checkbox" data-cl="${esc(n)}"${o.clients.includes(n) ? ' checked' : ''}${rbRO()}><span class="rb-sw-ui"></span><span><b>${esc(n)}</b></span></label></td>
            <td><span class="rb-unit"><input class="inp rb-price" data-price="${esc(n)}" inputmode="decimal" value="${price ?? ''}" placeholder="—"${rbRO()}><i>₽</i></span></td>
            <td class="n hide-m">${m ? fmt(m.minutes) : '—'}</td><td class="n">${m && m.charge != null ? money(m.charge) : '—'}</td>
            <td class="n hide-m">${m ? money(m.cost) : '—'}</td><td class="n ${m && m.profit < 0 ? 'bad-text' : ''}">${m && m.profit != null ? money(m.profit) : '—'}</td></tr>`; }).join('')}</tbody></table></div>`
        : '<div class="empty">Клиентов в таблице «Прозвон лидов» не нашлось.</div>'}
      <div class="row" data-css="margin-top:10px"><button class="btn primary" type="button" id="cl-save"${rbRO()}>Сохранить</button>
        <span class="hint">Себестоимость минуты сейчас ${money(econ.cost_min)}: цена ниже неё работает в минус.</span></div></section>
    <section class="card blk" data-css="--i:3"><div class="card-h"><h2>Куда идут итоги звонков</h2></div><div class="list">
      <div class="item"><div class="main-l"><div class="t">CRM Averon</div><div class="s" data-css="white-space:normal">Звонки, записи и итоги робота попадают сюда, на вкладку «Звонки».</div></div><span class="chip good">работает</span></div>
      ${sheet ? `<div class="item"><div class="main-l"><div class="t">Google Таблица</div><div class="s" data-css="white-space:normal">Итог каждого звонка строкой на лист «${esc(sheet.tab)}».</div></div><span class="chip ${sheet.on ? 'good' : ''}">${sheet.on ? 'работает' : 'не подключена'}</span></div>` : ''}
      <div class="item"><div class="main-l"><div class="t">Mango Office</div><div class="s" data-css="white-space:normal">Робот звонит через сотрудника «Робот» в Mango.</div></div><span class="chip good">работает</span></div></div></section>`;
  const calc = () => {
    const v = k => rbNum($(`[data-calc="${k}"]`, el).value);
    const minutes = (v('calls') || 0) * (v('reach') || 0) / 100 * (v('len') || 0);
    const cost = minutes * econ.cost_min;
    const price = v('price');
    const pay = price == null ? null : minutes * price;
    const profit = pay == null ? null : pay - cost;
    const line = (t, val, sub = '', cls = '') => `<div class="item"><div class="main-l"><div class="t">${t}</div>${sub ? `<div class="s wrap-s">${sub}</div>` : ''}</div><div class="r"><b class="num ${cls}">${val}</b></div></div>`;
    $('#calc-out', el).innerHTML = line('Минут разговоров', fmt(minutes), 'звонки × дозвон × средний разговор')
      + line('Наша себестоимость', money(cost), `по ${money(econ.cost_min)} за минуту`)
      + (pay == null ? line('Клиент платит', '—', 'впишите цену минуты') : line('Клиент платит', money(pay))
        + line('Прибыль', money(profit), pay ? `маржа ${pctTxt(profit / pay * 100)}` : '', profit < 0 ? 'bad-text' : ''));
  };
  $$('[data-calc]', el).forEach(i => i.addEventListener('input', calc));
  calc();
  $('#cl-save', el)?.addEventListener('click', async e => {
    const btn = e.currentTarget; // после await e.currentTarget уже null
    busy(btn, true);
    const prices = {};
    $$('[data-price]', el).forEach(i => { prices[i.dataset.price] = i.value.trim(); });
    try {
      await rbPost('/options', {clients: $$('[data-cl]:checked', el).map(i => i.dataset.cl), prices: {client_min: prices}});
      toast('Сохранено'); reload();
    } catch (err) { busy(btn, false); toast(err.message, true); }
  });
};

// ---------- голос и тестовый звонок (static/robot-voice.js) ----------
RB_VIEW.voice = async el => { await robotVoiceView(el); };


// Библиотека утверждённых ответов, отдельно для каждого проекта.
RB_VIEW.replies = async el => {
  const [{items: projects}, {items: campaigns}] = await Promise.all([rbGet('/reply-projects'), rbGet('/campaigns')]);
  if (!projects.some(p => String(p.id) === String(RB.replyProject))) RB.replyProject = projects[0]?.id || '';
  const selected = projects.find(p => String(p.id) === String(RB.replyProject));
  const items = selected ? (await rbGet('/replies', {project_id: selected.id})).items : [];
  el.innerHTML = `<section class="card blk"><div class="card-h"><h2>Библиотека ответов</h2><span class="sp"></span><button class="btn primary small" id="reply-project-new"${rbRO()}>Новый проект</button></div>
    <p class="lead">Утверждённые записи применяются только в кампаниях, привязанных к проекту. Новые варианты проверяет директор.</p>
    ${projects.length ? `<label class="field">Проект<select class="inp" id="reply-project">${projects.map(p => `<option value="${esc(p.id)}"${String(p.id) === String(RB.replyProject) ? ' selected' : ''}>${esc(p.name)} · ${esc(opName(p.operator))}</option>`).join('')}</select></label>
      ${(selected.campaigns || []).filter(c => c.reply_health?.error).map(c => `<p class="hint">Кампания ${esc(c.name || c.id)}: набор был отключён при последнем звонке — ${esc(c.reply_health.error)}.</p>`).join('')}
      <div><button class="btn small" id="reply-profile"${rbRO()}>Профиль проекта</button> <button class="btn small" id="reply-operator"${rbRO()}>Назначить оператора</button> <button class="btn small" id="reply-map"${rbRO()}>Привязать кампанию</button> <button class="btn small" id="reply-script"${rbRO()}>Привязать сценарий</button> <button class="btn small" id="reply-sources">Источники и покрытие</button> <button class="btn small" id="reply-metrics">Применение</button> <button class="btn small" id="reply-contact"${rbRO()}>Согласованный перезвон</button> <button class="btn small" id="reply-propose"${rbRO()}>Предложить черновики</button> <button class="btn small" id="reply-new"${rbRO()}>Добавить ответ</button></div>
      ${items.length ? items.map(i => `<article class="card blk"><div class="card-h"><b>${esc(i.question)}</b><span class="sp"></span><span class="chip">${esc(i.status)} · версия ${esc(i.version)}</span></div>
        <p>${esc(i.answer)}</p><p class="hint">Источник: ${i.source_kind === "guide" ? "шпаргалка" : i.source_kind === "learned" ? "разговоры" : "вручную"} · связанных звонков: ${esc(i.evidence_count ?? 0)}</p>${i.fact_needed ? `<p class="hint">Требуется проверить факт: ${esc(i.fact_note || "уточните данные перед утверждением")}</p>` : ""}<p class="hint">Активная версия: ${esc(i.active_version || 'нет')} ${i.rejection_reason ? ' · Причина: ' + esc(i.rejection_reason) : ''}</p>
        <button class="btn small" data-reply-edit="${i.id}"${rbRO()}>Изменить</button> <button class="btn small" data-reply-generate="${i.id}"${rbRO()}>Создать аудио</button>
        <button class="btn small" data-reply-review="${i.id}">Проверить</button> <button class="btn small" data-reply-versions="${i.id}">Версии</button> <button class="btn small" data-reply-evidence="${i.id}">Источники</button></article>`).join('') : '<p class="empty">В проекте пока нет ответов.</p>'}` : '<p class="empty">Создайте проект и привяжите к нему кампанию.</p>'}</section>`;
  $('#reply-project-new', el).onclick = () => replyProjectForm();
  $('#reply-project', el)?.addEventListener('change', e => { RB.replyProject = e.target.value; RB_VIEW.replies(el).catch(e => toast(e.message, true)); });
  $('#reply-profile', el)?.addEventListener('click', () => replyProfileForm(selected));
  $('#reply-operator', el)?.addEventListener('click', () => replyOperatorForm(selected));
  $('#reply-map', el)?.addEventListener('click', () => replyMapForm(selected, campaigns));
  $('#reply-script', el)?.addEventListener('click', async () => { const script = prompt('Название сценария для обучения'); if (!script) return; try { await rbPost(`/reply-projects/${selected.id}/script`, {script}); toast('Сценарий привязан'); } catch(e) { toast(e.message, true); } });
  $('#reply-propose', el)?.addEventListener('click', async () => { try { await rbPost(`/reply-projects/${selected.id}/propose`); toast('Черновики подготовлены'); reload(); } catch(e) { toast(e.message, true); } });
  $('#reply-metrics', el)?.addEventListener('click', () => replyMetrics(selected));
  $('#reply-contact', el)?.addEventListener('click', () => replyContactForm(selected));
  $('#reply-sources', el)?.addEventListener('click', () => replySources(selected));
  $('#reply-new', el)?.addEventListener('click', () => replyEditForm(selected.id));
  const find = id => items.find(i => String(i.id) === String(id));
  $$('[data-reply-edit]', el).forEach(b => b.onclick = () => replyEditForm(selected.id, find(b.dataset.replyEdit)));
  $$('[data-reply-generate]', el).forEach(b => b.onclick = async () => { const i = find(b.dataset.replyGenerate); b.disabled = true; try { await rbPost(`/replies/${i.id}/generate`, {version:i.version}); toast('Аудио создано'); reload(); } catch(e) { toast(e.message, true); b.disabled = false; } });
  $$('[data-reply-review]', el).forEach(b => b.onclick = () => replyReview(find(b.dataset.replyReview)));
  $$('[data-reply-versions]', el).forEach(b => b.onclick = () => replyVersions(find(b.dataset.replyVersions)));
  $$('[data-reply-evidence]', el).forEach(b => b.onclick = () => replyEvidence(selected.id, find(b.dataset.replyEvidence)));
};

function replyProjectForm() {
  const box = modal(`${modalHead('Новый проект')}<div class="rvm-body"><label class="field">Название<input class="inp" id="rp-name" maxlength="200"></label><label class="field">Профиль проекта<select class="inp" id="rp-profile"><option value="generic">Другой проект</option><option value="packaging">Картонная упаковка</option></select></label><label class="field">Оператор<select class="inp" id="rp-op"><option value="">Назначить позже</option>${(RB.st.operators || []).map(o => `<option value="${esc(o.id)}">${esc(o.name)}</option>`).join('')}</select></label><button class="btn primary" id="rp-save">Создать</button></div>`);
  $('#rp-save', box).onclick = async () => { try { const res = await rbPost('/reply-projects', {name: $('#rp-name', box).value, operator: $('#rp-op', box).value, profile: $('#rp-profile', box).value}); RB.replyProject = res.id; box.close(); reload(); } catch(e) { toast(e.message, true); } };
}
function replyProfileForm(project) {
  const box = modal(`${modalHead('Профиль проекта', 'Можно изменить только до появления источников и ответов.')}<div class="rvm-body"><label class="field">Профиль<select class="inp" id="rp-profile"><option value="generic"${project.profile !== 'packaging' ? ' selected' : ''}>Другой проект</option><option value="packaging"${project.profile === 'packaging' ? ' selected' : ''}>Картонная упаковка</option></select></label><button class="btn primary" id="rp-save">Сохранить</button></div>`);
  $('#rp-save', box).onclick = async () => { try { await rbPost(`/reply-projects/${project.id}/profile`, {profile: $('#rp-profile', box).value}); box.close(); reload(); } catch(e) { toast(e.message, true); } };
}
function replyOperatorForm(project) {
  const box = modal(`${modalHead('Назначить оператора')}<div class="rvm-body"><label class="field">Оператор<select class="inp" id="rp-op">${(RB.st.operators || []).map(o => `<option value="${esc(o.id)}"${o.id === project.operator ? ' selected' : ''}>${esc(o.name)}</option>`).join('')}</select></label><button class="btn primary" id="rp-save">Сохранить</button></div>`);
  $('#rp-save', box).onclick = async () => { try { await rbPost(`/reply-projects/${project.id}/operator`, {operator: $('#rp-op', box).value}); box.close(); reload(); } catch(e) { toast(e.message, true); } };
}
function replyMapForm(project, campaigns) {
  const choices = campaigns.filter(c => c.operator === project.operator);
  const box = modal(`${modalHead('Привязать кампанию')}<div class="rvm-body"><p>Доступны кампании того же оператора.</p><label class="field">Кампания<select class="inp" id="rp-campaign">${choices.map(c => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('')}</select></label><button class="btn primary" id="rp-save"${choices.length ? '' : ' disabled'}>Привязать</button></div>`);
  $('#rp-save', box).onclick = async () => { try { await rbPost(`/reply-projects/${project.id}/campaign`, {campaign_id: Number($('#rp-campaign', box).value)}); box.close(); toast('Кампания привязана'); reload(); } catch(e) { toast(e.message, true); } };
}
function replyEditForm(projectId, item) {
  const box = modal(`${modalHead(item ? 'Изменить ответ' : 'Новый ответ', 'Изменение создаёт новую версию.') }<div class="rvm-body"><label class="field">Фраза клиента<textarea class="inp" id="rp-question" rows="3">${esc(item?.question || '')}</textarea></label><label class="field">Ответ<textarea class="inp" id="rp-answer" rows="4">${esc(item?.answer || '')}</textarea></label>${item?.fact_needed ? `<p class="hint">Требуется проверить факт: ${esc(item.fact_note || 'уточните данные перед утверждением')}</p><label><input type="checkbox" id="rp-fact-checked"> Я проверил и исправил факты в ответе</label>` : ''}${item ? `<label class="field">Варианты фразы клиента (по одному на строку; укажите и основную фразу)<textarea class="inp" id="rp-triggers" rows="3">${esc((item.triggers?.length ? item.triggers : [item.question]).join("\n"))}</textarea></label><label class="field">Действие после ответа<select class="inp" id="rp-action"><option value="reply">Продолжить разговор</option></select></label><label class="field">Обязательные слова из истории разговора (по одному на строку)<textarea class="inp" id="rp-context" rows="2">${esc((item.context_terms || []).join("\n"))}</textarea></label><label class="field">Обязательные сведения о контакте (по одному на строку)<textarea class="inp" id="rp-contact" rows="2">${esc((item.contact_terms || []).join("\n"))}</textarea></label>` : ''}<button class="btn primary" id="rp-save">Сохранить</button></div>`);
  $('#rp-save', box).onclick = async () => { const data = {question: $('#rp-question', box).value, answer: $('#rp-answer', box).value}; try { if (item) await rbPost(`/replies/${item.id}/edit`, {...data, version: item.version, fact_checked: !!$('#rp-fact-checked', box)?.checked, triggers: $('#rp-triggers', box).value.split('\n').map(s => s.trim()).filter(Boolean), next_action: $('#rp-action', box).value, context_terms: $('#rp-context', box).value.split('\n').map(s => s.trim()).filter(Boolean), contact_terms: $('#rp-contact', box).value.split('\n').map(s => s.trim()).filter(Boolean)}); else await rbPost('/replies', {...data, project_id: projectId}); box.close(); reload(); } catch(e) { toast(e.message, true); } };
}
function replyReview(item) {
  const box = modal(`${modalHead('Проверка ответа', `Версия ${esc(item.version)}`)}<div class="rvm-body"><p><b>Клиент:</b> ${esc(item.question)}</p><p><b>Робот:</b> ${esc(item.answer)}</p>${item.fact_needed ? `<p class="hint">Факты не проверены: сначала исправьте ответ и отметьте проверку.</p>` : ""}${item.has_audio ? `<audio id="rp-audio" controls preload="none" src="${esc(rbq(`/replies/${item.id}/audio`, {version:item.version}))}"></audio>` : '<p>Сначала создайте аудио.</p>'}<p class="hint" id="rp-status">Прослушайте запись целиком перед утверждением.</p><button class="btn primary" id="rp-approve" disabled>Утвердить</button> <button class="btn" id="rp-reject"${rbRO()}>Отклонить</button> <button class="btn" id="rp-rollback"${rbRO()}>Вернуть версию</button></div>`);
  $('#rp-audio', box)?.addEventListener('ended', async () => { try { await rbPost(`/replies/${item.id}/preview`, {version:item.version}); $('#rp-status', box).textContent = 'Запись прослушана.'; $('#rp-approve', box).disabled = !!RB.st.readonly || !!item.fact_needed; } catch(e) { toast(e.message, true); } });
  $('#rp-approve', box).onclick = async () => { try { await rbPost(`/replies/${item.id}/approve`, {version:item.version}); box.close(); reload(); } catch(e) { toast(e.message, true); } };
  $('#rp-reject', box).onclick = async () => { const reason = prompt('Причина отклонения'); if (!reason) return; try { await rbPost(`/replies/${item.id}/reject`, {version:item.version, reason}); box.close(); reload(); } catch(e) { toast(e.message, true); } };
  $('#rp-rollback', box).onclick = async () => { const target = Number(prompt('Номер ранее утверждённой версии')); if (!Number.isInteger(target) || target < 1) return; try { await rbPost(`/replies/${item.id}/rollback`, {version:item.version, target_version:target}); box.close(); reload(); } catch(e) { toast(e.message, true); } };
}
async function replyEvidence(projectId, item) {
  try { const {items} = await rbGet(`/replies/${item.id}/evidence`, {project_id:projectId}); modal(`${modalHead('Источники ответа')}<div class="rvm-body">${items.length ? items.map(i => `<p><b>${esc(i.source_type)} · ${esc(i.source_id)}</b> · итог: ${esc(i.outcome || 'не указан')}<br>${replyEvidencePairs(i.evidence)}<br><audio controls preload="none" src="${esc(rbq(`/replies/${item.id}/evidence/${encodeURIComponent(i.source_type)}/${encodeURIComponent(i.source_id)}`, {project_id:projectId}))}"></audio></p>`).join('') : '<p>Связанных источников пока нет.</p>'}</div>`); } catch(e) { toast(e.message, true); }
}

async function replyVersions(item) {
  try { const {items} = await rbGet(`/replies/${item.id}/versions`); modal(`${modalHead('Версии ответа')}<div class="rvm-body">${items.map(v => `<article><b>Версия ${esc(v.version)}</b> ${v.approved_at ? '· утверждена' : ''}<p>${esc(v.question)}</p><p>${esc(v.answer)}</p></article>`).join('')}</div>`); } catch(e) { toast(e.message, true); }
}
async function replySources(project) {
  try {
    const [data, replies] = await Promise.all([rbGet(`/reply-projects/${project.id}/sources`), rbGet('/replies', {project_id: project.id})]);
    const box = modal(`${modalHead('Источники и покрытие', 'Итог звонка сам по себе не доказывает качество ответа.')}<div class="rvm-body"><p>Всего источников по статусам: ${(data.coverage || []).map(c => `${esc(c.source_type)} · ${esc(c.status)}${c.reason ? " · " + esc(c.reason) : ""}: ${esc(c.count)}`).join("; ") || "нет данных"}. Всего: ${esc(data.total ?? data.items.length)}; ниже показаны последние ${esc(data.shown ?? data.items.length)} источников.</p><p>По операторам: ${(data.operator_coverage || []).map(o => `${esc(o.operator || "не определён")}: ${esc(o.count)}`).join("; ") || "нет данных"}.</p>
      <label class="field">Связать источник с ответом<select class="inp" id="rp-link-target">${replies.items.map(i => `<option value="${esc(i.id)}">${esc(i.question)}</option>`).join('')}</select></label>
      ${(data.items || []).map((i, index) => `<article><b>${esc(i.source_type)} · ${esc(i.source_id)}</b> · ${esc(i.status)} ${i.reason ? '· ' + esc(i.reason) : ''}<p>Итог: ${esc(i.outcome || 'не указан')}</p><p>${replyEvidencePairs(i.evidence)}</p><button class="btn small" data-source-link="${index}"${rbRO()}>Связать с ответом</button></article>`).join('') || '<p>Источников пока нет.</p>'}</div>`, true);
    $$('[data-source-link]', box).forEach(b => b.onclick = async () => { const source = data.items[Number(b.dataset.sourceLink)]; const id = Number($('#rp-link-target', box).value); if (!id) return; try { await rbPost(`/replies/${id}/evidence`, {project_id:project.id, source_type:source.source_type, source_id:source.source_id}); toast('Источник связан'); } catch(e) { toast(e.message, true); } });
  } catch(e) { toast(e.message, true); }
}

function replyEvidencePairs(raw) {
  let evidence = raw;
  if (typeof evidence === 'string') { try { evidence = JSON.parse(evidence); } catch(e) { evidence = []; } }
  return (Array.isArray(evidence) ? evidence : []).map(e => `<span>Вопрос ${e.at == null ? '(время неизвестно)' : esc(e.at) + ' с'}: ${esc(e.question || '')}<br>Ответ ${e.answer_at == null ? '(время неизвестно)' : esc(e.answer_at) + ' с'}: ${esc(e.answer || '')}<br>Повтор: ${e.reaction_at == null ? 'время неизвестно' : esc(e.reaction_at) + ' с'}</span>`).join('<br>');
}

function replyContactForm(project) {
  const box = modal(`${modalHead('Согласованный перезвон', 'Укажите только время, действительно согласованное с клиентом.')}<div class="rvm-body"><label class="field">Телефон<input class="inp" type="tel" id="rp-phone"></label><label class="field">Дата и время<input class="inp" type="datetime-local" id="rp-callback"></label><button class="btn primary" id="rp-save">Сохранить</button></div>`);
  $('#rp-save', box).onclick = async () => { try { const date = new Date($('#rp-callback', box).value); if (!Number.isFinite(date.getTime())) throw new Error('Укажите дату и время'); await rbPost(`/reply-projects/${project.id}/contact-memory`, {phone:$('#rp-phone', box).value, callback_at:date.toISOString()}); box.close(); toast('Перезвон сохранён'); } catch(e) { toast(e.message, true); } };
}
async function replyMetrics(project) {
  try { const {items, fallback} = await rbGet(`/reply-projects/${project.id}/metrics`); modal(`${modalHead('Применение записей', 'Время — до передачи аудио, а не до звука в телефоне. Итоги относятся к звонку и не доказывают влияние записи.')}<div class="rvm-body">${items.map(i => `<p>Ответ ${esc(i.reply_id)} · версия ${esc(i.version)}: применений ${esc(i.uses)}, перебиваний ${esc(i.interrupted)}, среднее время до передачи аудио ${i.mean_response_ms == null ? 'нет данных' : esc(Math.round(i.mean_response_ms)) + ' мс'}, до первого кадра AudioSocket ${i.mean_first_audio_ms == null ? 'нет данных' : esc(Math.round(i.mean_first_audio_ms)) + ' мс'} (замеров ${esc(i.first_audio_samples || 0)}); воспроизведений в звонках с повтором ${esc(i.uses_in_repeated_calls || 0)}, с итогом «интерес» ${esc(i.calls_interested_events || 0)}, с итогом «перезвон» ${esc(i.calls_callback_events || 0)}</p>`).join('') || '<p>Применений пока нет.</p>'}<p>Обычный ответ: ${esc(fallback?.samples || 0)} случаев, первый кадр ${fallback?.mean_first_audio_ms == null ? 'нет данных' : esc(Math.round(fallback.mean_first_audio_ms)) + ' мс'} (замеров ${esc(fallback?.first_audio_samples || 0)}).</p></div>`); } catch(e) { toast(e.message, true); }
}
