// Раздел «Автоматизации»: сценарии «событие → условия → действия» (движок crm/automations.py).
// Список сценариев, готовые шаблоны, подключения (Telegram, Google-таблицы), редактор формой, пробный запуск и журнал.
'use strict';

const AU = {d: null};
const AU_STATUS = {ok: ['good', 'выполнен'], failed: ['bad', 'ошибка'], retry: ['warn', 'повтор'], skipped: ['', 'условия не подошли'], limit: ['bad', 'лимит тарифа']};
const auCat = () => AU.d.catalog;
const auLabel = path => auCat().labels[path] || path;
const auReady = kind => AU.d.accounts.filter(a => a.kind === kind && a.status === 'ready');

VIEWS.auto = async view => {
  const ro = S.me.readonly;
  setHead({sub: 'событие → условия → действия', actions: ro ? '' : '<button class="btn primary" type="button" data-au-new>Новый сценарий</button>'});
  AU.d = await loading(view, api('/api/automations'), 'cards');
  const {automations: list, usage: u} = AU.d;
  const used = `${fmt(u.runs)}${u.max_runs_month ? ' из ' + fmt(u.max_runs_month) : ''}`;
  view.innerHTML = `<div class="page">
    <p class="hint blk">Запусков в этом месяце: <b>${used}</b> · включено сценариев: <b>${u.active}${u.max_active ? ' из ' + u.max_active : ''}</b>.
      Сценарий включается только после пробного запуска, а любая правка его выключает.</p>
    ${ro ? '' : '<section class="card blk au-agent" data-css="--i:1" data-agent></section>'}
    <section class="card blk" data-css="--i:1"><div class="card-h"><h2>Сценарии</h2><span class="hint">${list.length}</span></div>
      ${list.length ? `<div class="au-list">${list.map(auCard).join('')}</div>` : '<div class="empty">Сценариев пока нет. Начните с шаблона ниже.</div>'}</section>
    ${ro ? '' : `<section class="card blk" data-css="--i:2"><div class="card-h"><h2>Готовые шаблоны</h2><span class="hint">откроется редактор, останется выбрать чат или таблицу</span></div>
      <div class="au-tpls">${auCat().templates.map(t => `<button class="au-tpl" type="button" data-tpl="${esc(t.id)}"><b>${esc(t.title)}</b><span>${esc(t.about)}</span></button>`).join('')}</div></section>`}
    <section class="card blk" data-css="--i:3"><div class="card-h"><h2>Подключения</h2><span class="sp"></span>
      ${ro ? '' : '<button class="btn small" type="button" data-au-tg>Подключить Telegram</button><button class="btn small" type="button" data-au-sheet>Подключить Google-таблицу</button>'}</div>
      ${AU.d.accounts.length ? `<div class="list">${AU.d.accounts.map(a => `<div class="item"><div class="main-l"><div class="t">${esc(a.title)}</div>
        ${a.status === 'pending' ? '<div class="hint">ждём, когда в чате нажмут «Старт»</div>' : a.status === 'broken' ? `<div class="hint bad-text">${esc(a.error || 'не работает')}</div>` : ''}</div>
        <span class="chip ${a.status === 'ready' ? 'good' : a.status === 'broken' ? 'bad' : 'warn'}">${a.status === 'ready' ? 'работает' : a.status === 'broken' ? 'сломано' : 'ждём'}</span>
        ${ro ? '' : `<button class="x" type="button" data-acc-del="${a.id}" aria-label="Удалить подключение" data-css="width:28px;height:28px">${svg('x', '')}</button>`}</div>`).join('')}</div>`
        : '<div class="empty">Подключите чат Telegram или Google-таблицу, чтобы сценарии могли туда писать.</div>'}
      ${auCat().email_ready ? '' : '<p class="hint">Почта CRM ещё не настроена: сценарии с письмами можно собрать, но письма не уйдут.</p>'}</section></div>`;
  bindAuto(view);
  if (!ro) auAgent($('[data-agent]', view));
  AU.studioRefresh?.();
};

// ---------- помощник: «Что вы хотите сделать?» ----------
// Разговор живёт до перезагрузки страницы; черновик открывается в редакторе, сохраняет человек.
AU.chat = [];
const AU_EXAMPLES = ['Присылай в Telegram каждую оплату больше 100 000 ₽', 'Каждый вечер в 19:00 итоги дня в чат',
  'Записывай новые номера из базы в Google-таблицу', 'Сообщай в чат, когда менеджер назначил встречу'];
function auAgent(box) {
  const msg = m => m.role === 'user' ? `<div class="au-say me">${esc(m.text)}</div>`
    : `<div class="au-say">${esc(m.text)}${m.draft ? `<div class="au-draft"><b>${esc(m.draft.name)}</b><span class="hint">${esc(m.desc || '')}</span>
        ${m.missing?.length ? `<span class="hint bad-text">Нужно подключить: ${m.missing.map(k => k === 'telegram' ? 'чат Telegram' : 'Google-таблицу').join(', ')}.</span>` : ''}
        <div class="row"><button class="btn small primary" type="button" data-open="${m.n}">Открыть в редакторе</button></div></div>` : ''}</div>`;
  const draw = () => {
    box.innerHTML = `<div class="card-h"><h2>Что вы хотите сделать?</h2><span class="hint">помощник соберёт сценарий, а вы проверите и включите</span></div>
      ${AU.chat.length ? `<div class="au-chat">${AU.chat.map(msg).join('')}</div>` : `<div class="chips au-ex">${AU_EXAMPLES.map(t => `<button class="chip au-field" type="button" data-ex="${esc(t)}">${esc(t)}</button>`).join('')}</div>`}
      <div class="au-ask"><textarea class="inp" data-q rows="2" maxlength="1500" placeholder="Например: когда подписан договор, напиши в группу «Продажи» имя клиента и менеджера"></textarea>
        <button class="btn primary" type="button" data-send>Собрать</button></div>
      ${AU.chat.length ? '<button class="btn small ghost" type="button" data-reset>Начать заново</button>' : ''}`;
    const chat = $('.au-chat', box); if (chat) chat.scrollTop = chat.scrollHeight;
  };
  const send = async () => {
    const q = $('[data-q]', box), text = q.value.trim();
    if (!text) { q.focus(); return; }
    const btn = $('[data-send]', box);
    busy(btn, true);
    const history = AU.chat.map(m => ({role: m.role, text: m.text}));
    try {
      const r = await api('/api/automations/agent', {method: 'POST', body: {text, history}});
      AU.chat.push({role: 'user', text}, {role: 'assistant', text: r.reply, draft: r.draft, desc: r.text, missing: r.missing, n: AU.chat.length + 1});
      draw();
    } catch (err) { busy(btn, false); toast(err.message, true); }
  };
  draw();
  box.addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    if (b.dataset.ex) { $('[data-q]', box).value = b.dataset.ex; send(); }
    else if ('send' in b.dataset) send();
    else if ('reset' in b.dataset) { AU.chat = []; draw(); }
    else if (b.dataset.open) { const m = AU.chat[+b.dataset.open]; auEditor({name: m.draft.name, spec: JSON.parse(JSON.stringify(m.draft.spec))}); }
  });
  box.addEventListener('keydown', e => { if (e.target.matches('[data-q]') && e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } });
}

function auCard(a, i) {
  const ro = S.me.readonly;
  const chip = a.enabled ? '<span class="chip good">включён</span>' : a.paused_reason ? '<span class="chip bad">остановлен</span>' : '<span class="chip">выключен</span>';
  return `<article class="au-card" data-css="--i:${i}" data-id="${a.id}"><div class="au-top"><b>${esc(a.name)}</b>${chip}</div>
    <p class="hint">${esc(a.text)}</p>
    ${a.paused_reason ? `<p class="hint bad-text">${esc(a.paused_reason)}</p>` : ''}
    ${a.hook ? `<div class="au-hook" data-hook="${esc(location.origin + a.hook)}"></div>` : ''}
    <div class="row">${ro ? '' : `<button class="btn small" type="button" data-test="${a.id}">Пробный запуск</button>
      <button class="btn small${a.enabled ? '' : ' primary'}" type="button" data-on="${a.id}" data-v="${a.enabled ? '' : '1'}">${a.enabled ? 'Выключить' : 'Включить'}</button>
      <button class="btn small" type="button" data-edit="${a.id}">Изменить</button>`}
      <button class="btn small ghost" type="button" data-runs="${a.id}">Журнал</button>
      ${ro ? '' : `<button class="btn small ghost danger" type="button" data-del="${a.id}">Удалить</button>`}</div></article>`;
}

function bindAuto(view) {
  $$('.au-hook', view).forEach(el => linkBox(el, el.dataset.hook, 'Адрес для другой системы: она отправляет сюда POST с JSON, поля доступны как {data.имя}.'));
  const byId = id => AU.d.automations.find(a => a.id === +id);
  $('#top [data-au-new]')?.addEventListener('click', () => auEditor({name: '', spec: {trigger: {type: 'deal.paid'}, if: [], actions: [auBlank('telegram.send')]}}));
  view.addEventListener('click', async e => {
    const b = e.target.closest('button'); if (!b) return;
    const d = b.dataset;
    if (d.tpl) {
      const t = auCat().templates.find(x => x.id === d.tpl);
      const spec = JSON.parse(JSON.stringify(t.spec));
      spec.actions.forEach(a => { if ('account' in a) a.account = auReady(auCat().actions[a.type].account)[0]?.id ?? null; });
      auEditor({name: t.title, spec});
    } else if (d.edit) {
      const a = byId(d.edit); auEditor({id: a.id, name: a.name, spec: JSON.parse(JSON.stringify(a.spec)), hook: a.hook});
    } else if (d.test) auTest(b, d.test);
    else if (d.on !== undefined) {
      busy(b, true);
      try { await api(`/api/automations/${d.on}/enable`, {method: 'POST', body: {on: !!d.v}}); toast(d.v ? 'Сценарий включён' : 'Сценарий выключен'); reload(); }
      catch (err) { busy(b, false); toast(err.message, true); }
    } else if (d.runs) auRuns(byId(d.runs));
    else if (d.del) {
      if (!await askConfirm('Удалить сценарий вместе с журналом?', 'Удалить', true)) return;
      try { await api(`/api/automations/${d.del}/delete`, {method: 'POST', body: {}}); toast('Сценарий удалён'); reload(); } catch (err) { toast(err.message, true); }
    } else if (d.accDel) {
      if (!await askConfirm('Удалить подключение?', 'Удалить', true)) return;
      try { await api(`/api/automations/accounts/${d.accDel}/delete`, {method: 'POST', body: {}}); toast('Подключение удалено'); reload(); } catch (err) { toast(err.message, true); }
    } else if ('auTg' in d) auTelegram();
    else if ('auSheet' in d) auSheet();
  });
}

// ---------- пробный запуск и журнал ----------
const auStep = s => `<div class="au-step"><span class="chip ${s.ok ? 'good' : 'bad'}">${s.ok ? 'готово' : 'ошибка'}</span>
  <div><b>${esc(auCat().actions[s.type]?.title || s.type)}</b>${s.error ? `<div class="hint bad-text">${esc(s.error)}</div>` : ''}
  ${s.preview ? `<pre class="au-pre">${esc(typeof s.preview === 'string' ? s.preview : JSON.stringify(s.preview, null, 2))}</pre>` : ''}</div></div>`;

async function auTest(btn, id, after) {
  if (!await askConfirm('Пробный запуск отправит сообщение по-настоящему. Запустить?', 'Запустить')) return;
  busy(btn, true);
  let r;
  try { r = await api(`/api/automations/${id}/test`, {method: 'POST', body: {}}); }
  catch (err) { busy(btn, false); toast(err.message, true); return; }
  busy(btn, false);
  const ok = r.status === 'ok';
  const box = modal(`${modalHead(ok ? 'Пробный запуск прошёл' : 'Пробный запуск с ошибкой', r.real_data ? 'на последнем настоящем событии' : 'на примере данных: настоящих событий этого вида ещё не было')}
    <div class="rvm-body">${r.conditions_pass ? '' : '<p class="hint">На этих данных условия не выполнились бы. Для пробы действия всё равно выполнены.</p>'}
    ${r.steps.map(auStep).join('')}
    <div class="row" data-css="margin-top:14px">${ok ? `<button class="btn primary" type="button" data-en>Включить сценарий</button>` : ''}<button class="btn" type="button" data-shut>Закрыть</button></div></div>`, true);
  $('[data-shut]', box).onclick = () => box.close();
  $('[data-en]', box)?.addEventListener('click', async e => {
    busy(e.currentTarget, true);
    try { await api(`/api/automations/${id}/enable`, {method: 'POST', body: {on: true}}); box.close(); toast('Сценарий включён'); after?.(); reload(); }
    catch (err) { busy(e.currentTarget, false); toast(err.message, true); }
  });
}

async function auRuns(a) {
  const body = openDrawer({title: 'Журнал', sub: esc(a.name), wide: true, html: '<div class="sk" data-css="height:200px"></div>'});
  try {
    const runs = await api(`/api/automations/${a.id}/runs`);
    body.innerHTML = runs.length ? runs.map(r => { const [c, t] = AU_STATUS[r.status] || ['', r.status];
      return `<div class="au-run"><div class="row"><b>${fmtWhen(r.at)}</b><span class="chip ${c}">${esc(t)}</span>${r.test ? '<span class="chip acc">проба</span>' : ''}
        ${r.attempts > 1 ? `<span class="hint">попыток: ${r.attempts}</span>` : ''}</div>${r.steps.map(auStep).join('')}</div>`; }).join('')
      : '<div class="empty">Запусков ещё не было.</div>';
  } catch (err) { body.innerHTML = emptyCard(esc(err.message)); }
}

// ---------- подключения ----------
async function auTelegram() {
  let r;
  try { r = await api('/api/automations/accounts/telegram', {method: 'POST', body: {}}); } catch (err) { toast(err.message, true); return; }
  const box = modal(`${modalHead('Подключить Telegram', 'ссылка работает сутки')}<div class="rvm-body">
    <p>Откройте нужную ссылку и нажмите «Старт». Бот CRM напишет, что чат подключён.</p>
    <div class="row"><a class="btn primary" href="${esc(r.private)}" target="_blank" rel="noopener">Личный чат</a><a class="btn" href="${esc(r.group)}" target="_blank" rel="noopener">Добавить в группу</a></div>
    <p class="hint" data-wait>Жду подключения…</p></div>`);
  const t0 = Date.now();
  const poll = async () => {
    if (!box.isConnected) return;
    try {
      const d = await api('/api/automations');
      const acc = d.accounts.find(a => a.id === r.id);
      if (acc?.status === 'ready') { box.close(); toast('Подключено: ' + acc.title.replace(/^Telegram: /, '')); reload(); return; }
    } catch (e) { /* подождём */ }
    if (Date.now() - t0 < 180000) setTimeout(poll, 3000); else $('[data-wait]', box).textContent = 'Подключение появится в списке, когда в чате нажмут «Старт».';
  };
  setTimeout(poll, 3000);
  new MutationObserver((_, obs) => { if (!box.isConnected) { obs.disconnect(); reload(); } }).observe(document.body, {childList: true});
}

function auSheet() {
  const mail = auCat().sheets_email;
  const box = modal(`${modalHead('Подключить Google-таблицу')}<div class="rvm-body">
    ${mail ? `<p>Сначала откройте в таблице доступ «Редактор» для адреса <code>${esc(mail)}</code>.</p>` : '<p class="bad-text">У CRM нет сервисного аккаунта Google: таблицу подключить нельзя.</p>'}
    <label class="field">Ссылка на таблицу<input data-url placeholder="https://docs.google.com/spreadsheets/d/..."></label>
    <label class="field" data-css="margin-top:10px">Лист<input data-sheet placeholder="первый лист, если пусто"></label>
    <div class="row" data-css="margin-top:14px"><button class="btn primary" type="button" data-go-sheet>Подключить</button><button class="btn" type="button" data-shut>Отмена</button></div></div>`);
  $('[data-shut]', box).onclick = () => box.close();
  $('[data-url]', box).focus();
  $('[data-go-sheet]', box).addEventListener('click', async e => {
    busy(e.currentTarget, true);
    try {
      const acc = await api('/api/automations/accounts/sheets', {method: 'POST', body: {url: $('[data-url]', box).value, sheet: $('[data-sheet]', box).value}});
      box.close(); toast('Подключено: ' + acc.title); reload();
    } catch (err) { busy(e.currentTarget, false); toast(err.message, true); }
  });
}

// ---------- редактор ----------
function auBlank(type) {
  const acc = auCat().actions[type].account;
  return {telegram: {type, account: auReady('telegram')[0]?.id ?? null, text: ''}, sheets: {type, account: auReady('sheets')[0]?.id ?? null, row: []}}[acc]
    || (type === 'email.send' ? {type, to: [], subject: '', text: ''} : {type, url: ''});
}

// ---------- визуальный редактор ----------
// Сценарий схемой: событие → условия (все должны выполниться) → действия по порядку. Слева схема, справа
// настройки выбранного блока. Схема рисуется HTML-блоками и линиями SVG; стиль только через классы и el.style
// (политика безопасности не пускает атрибут style).
const AU_NODE = {w: 280, h: 74, gap: 58};
const AU_ICON = {
  trigger: '<path d="M13 3 5 14h6l-1 7 8-11h-6z"/>', cond: '<path d="M12 3 21 12 12 21 3 12z"/>',
  'telegram.send': '<path d="m21 4-18 7 6 2 2 6 3-4 5 4z"/><path d="m9 13 8-6"/>', 'email.send': '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',
  'sheets.append': '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M4 9h16M4 15h16M10 3v18"/>', 'webhook.send': '<circle cx="6" cy="17" r="2.5"/><circle cx="18" cy="17" r="2.5"/><circle cx="12" cy="6" r="2.5"/><path d="M8.5 17h7M10.8 8.2 7 14.8M13.2 8.2l3.8 6.6"/>',
};
const auSvg = k => `<svg class="au-ic" viewBox="0 0 24 24" aria-hidden="true">${AU_ICON[k] || AU_ICON.cond}</svg>`;

function auEditor(draft) {
  const cat = auCat();
  let sel = 'trigger', lastTpl = null, dirty = false, saved = null;
  const fieldsOf = () => cat.triggers[draft.spec.trigger.type].fields;
  const groups = [...new Set(Object.values(cat.triggers).map(t => t.group))];
  const acc = id => AU.d.accounts.find(x => x.id === id);
  const opText = c => {
    const v = cat.choices[c.field]?.[String(c.value)] ?? c.value;
    return `${auLabel(c.field)} ${cat.op_labels[c.op] || c.op}${['empty', 'not_empty'].includes(c.op) ? '' : ` «${v ?? ''}»`}`;
  };
  const actSummary = a => {
    if (a.type === 'telegram.send') return [acc(a.account)?.title.replace(/^Telegram: /, '') || 'чат не выбран', (a.text || '').split('\n')[0]];
    if (a.type === 'sheets.append') return [acc(a.account)?.title || 'таблица не выбрана', `${(a.row || []).length} ${plural((a.row || []).length, 'столбец', 'столбца', 'столбцов')}`];
    if (a.type === 'email.send') return [(a.to || []).join(', ') || 'адрес не указан', a.subject || ''];
    return [a.url || 'адрес не указан', 'POST JSON'];
  };
  const actBad = a => (cat.actions[a.type].account && !acc(a.account)) || (a.type === 'webhook.send' && !a.url)
    || (a.type === 'email.send' && !(a.to || []).length) || (['telegram.send', 'email.send'].includes(a.type) && !(a.text || '').trim());
  // Узлы схемы по порядку: id выбирается и подсвечивается, kind решает, что показывать справа.
  const nodes = () => {
    const t = draft.spec.trigger;
    return [{id: 'trigger', kind: 'trigger', icon: 'trigger', cap: 'Когда', title: cat.triggers[t.type].title + (t.time ? ' в ' + t.time : ''), sub: cat.triggers[t.type].group},
      ...draft.spec.if.map((c, i) => ({id: 'c' + i, kind: 'cond', i, icon: 'cond', cap: 'Если', title: opText(c), bad: !['empty', 'not_empty'].includes(c.op) && String(c.value ?? '').trim() === ''})),
      ...draft.spec.actions.map((a, i) => { const [t1, t2] = actSummary(a); return {id: 'a' + i, kind: 'act', i, icon: a.type, cap: `${i + 1}. ${cat.actions[a.type].title}`, title: t1, sub: t2, bad: actBad(a)}; })];
  };

  const root = document.createElement('div');
  root.className = 'au-studio'; root.setAttribute('role', 'dialog'); root.setAttribute('aria-modal', 'true'); root.setAttribute('aria-label', 'Редактор сценария');
  root.innerHTML = `<header class="au-bar"><input class="inp au-name" data-name maxlength="120" placeholder="Название сценария" aria-label="Название">
      <span class="sp"></span><span class="au-acts" data-acts></span>
      <button class="btn primary" type="button" data-save>Сохранить</button><button class="x" type="button" data-shut aria-label="Закрыть">${svg('x', '')}</button></header>
    <div class="au-split"><div class="au-canvas" data-canvas><div class="au-board" data-board></div></div>
      <aside class="au-insp" data-insp></aside></div>`;
  document.body.appendChild(root);
  document.documentElement.classList.add('au-lock');
  $('[data-name]', root).value = draft.name || '';
  const board = $('[data-board]', root), insp = $('[data-insp]', root);

  // ---------- схема ----------
  const drawBoard = () => {
    const list = nodes(), {w, h, gap} = AU_NODE;
    const width = Math.max(w + 80, board.parentElement.clientWidth - 2);
    const x = (width - w) / 2;
    const ys = list.map((_, i) => 24 + i * (h + gap));
    const height = ys[ys.length - 1] + h + gap + 40;
    board.style.width = width + 'px'; board.style.height = height + 'px';
    const firstAct = 1 + draft.spec.if.length;
    // «+» на каждой связи: до первого действия можно вставить условие, с первого действия — только действия.
    const plus = (k, y) => `<button class="au-plus" type="button" data-plus="${k}" aria-label="Добавить шаг" data-css="left:${width / 2 - 13}px;top:${y - 13}px">+</button>`;
    const lines = list.map((n, k) => k ? `<path d="M${width / 2} ${ys[k - 1] + h} V${ys[k] - 4}"/><path class="au-arr" d="M${width / 2 - 5} ${ys[k] - 10} l5 6 5-6"/>` : '').join('')
      + `<path class="au-tail" d="M${width / 2} ${ys[ys.length - 1] + h} V${ys[ys.length - 1] + h + gap - 14}"/>`;
    board.innerHTML = `<svg class="au-lines" width="${width}" height="${height}" aria-hidden="true">${lines}</svg>`
      + list.map((n, k) => `<button type="button" class="au-node ${n.kind}${n.id === sel ? ' on' : ''}${n.bad ? ' bad' : ''}" data-node="${n.id}"${n.kind === 'act' ? ` data-drag="${n.i}"` : ''}
          data-css="left:${x}px;top:${ys[k]}px;width:${w}px;height:${h}px">${auSvg(n.icon)}<span class="au-nt"><small>${esc(n.cap)}</small><b>${esc(n.title)}</b>${n.sub ? `<span>${esc(n.sub)}</span>` : ''}</span>${n.bad ? '<i class="au-warn" title="Нужно дописать">!</i>' : ''}</button>`).join('')
      + list.slice(1).map((_, k) => plus(k + 1 <= firstAct ? 'mid' + (k + 1) : 'act' + (k + 1 - firstAct), ys[k] + h + gap / 2)).join('')
      + plus('end', ys[ys.length - 1] + h + gap - 2);
  };

  // ---------- настройки выбранного блока ----------
  const accSelect = (a, kind) => {
    const list = AU.d.accounts.filter(x => x.kind === kind && x.status === 'ready');
    return list.length ? `<label class="field">${kind === 'telegram' ? 'Чат' : 'Таблица'}<select data-k="account">${a.account == null ? '<option value="">выберите</option>' : ''}${list.map(x =>
      `<option value="${x.id}"${x.id === a.account ? ' selected' : ''}>${esc(x.title)}</option>`).join('')}</select></label>
      <button class="linkish" type="button" data-connect="${kind}">${kind === 'telegram' ? 'Подключить ещё чат' : 'Подключить ещё таблицу'}</button>`
      : `<p class="hint bad-text">${kind === 'telegram' ? 'Нет подключённого чата.' : 'Нет подключённой таблицы.'} <button class="linkish" type="button" data-connect="${kind}">Подключить</button></p>`;
  };
  const valueInput = c => {
    const hide = ['empty', 'not_empty'].includes(c.op) ? ' hidden' : '', opts = cat.choices[c.field];
    return opts ? `<label class="field"${hide}>Значение<select data-ck="value">${Object.entries(opts).map(([v, l]) => `<option value="${esc(v)}"${String(c.value) === v ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select></label>`
      : `<label class="field"${hide}>Значение<input data-ck="value" value="${esc(c.value ?? '')}"></label>`;
  };
  const chips = () => `<h3 class="au-h">Поля события <span class="hint">нажмите, чтобы вставить в текст</span></h3>
    <div class="chips">${fieldsOf().map(f => `<button class="chip au-field" type="button" data-field="${esc(f)}">${esc(auLabel(f))}</button>`).join('')}</div>`;
  const drawInsp = () => {
    const n = nodes().find(x => x.id === sel) || nodes()[0]; sel = n.id;
    let html = '';
    if (n.kind === 'trigger') {
      const t = draft.spec.trigger;
      html = `<h2>Когда запускать</h2><label class="field">Событие<select data-trigger>${groups.map(g => `<optgroup label="${esc(g)}">${Object.entries(cat.triggers).filter(([, v]) => v.group === g).map(([k, v]) =>
        `<option value="${esc(k)}"${k === t.type ? ' selected' : ''}>${esc(v.title)}</option>`).join('')}</optgroup>`).join('')}</select></label>
        ${t.type === 'schedule.daily' ? `<label class="field">Время<input type="time" data-time value="${esc(t.time || '19:00')}"></label>` : ''}
        ${t.type === 'webhook.in' ? `<p class="hint">${draft.hook ? 'Адрес для вызова показан в карточке сценария.' : 'Адрес для вызова появится после сохранения.'}</p>` : ''}
        <p class="hint">Поля этого события можно проверять в условиях и вставлять в тексты действий.</p>
        <div class="chips">${fieldsOf().map(f => `<span class="chip">${esc(auLabel(f))}</span>`).join('')}</div>`;
    } else if (n.kind === 'cond') {
      const c = draft.spec.if[n.i];
      html = `<h2>Условие</h2><p class="hint">Действия выполнятся, только если выполнены все условия.</p>
        <label class="field">Поле<select data-ck="field">${fieldsOf().map(f => `<option value="${esc(f)}"${f === c.field ? ' selected' : ''}>${esc(auLabel(f))}</option>`).join('')}</select></label>
        <label class="field">Проверка<select data-ck="op">${cat.ops.map(o => `<option value="${esc(o)}"${o === c.op ? ' selected' : ''}>${esc(cat.op_labels[o] || o)}</option>`).join('')}</select></label>
        ${valueInput(c)}<div class="row au-del"><button class="btn small danger" type="button" data-rm>Убрать условие</button></div>`;
    } else {
      const a = draft.spec.actions[n.i];
      let inner = '';
      if (a.type === 'telegram.send') inner = accSelect(a, 'telegram') + `<label class="field">Текст сообщения<textarea data-k="text" data-tpl rows="6">${esc(a.text)}</textarea></label>`;
      else if (a.type === 'email.send') inner = `${cat.email_ready ? '' : '<p class="hint bad-text">Почта CRM не настроена: письмо не уйдёт.</p>'}
        <label class="field">Кому (через запятую)<input data-k="to" value="${esc((a.to || []).join(', '))}" placeholder="boss@company.ru"></label>
        <label class="field">Тема<input data-k="subject" data-tpl value="${esc(a.subject)}"></label>
        <label class="field">Текст письма<textarea data-k="text" data-tpl rows="6">${esc(a.text)}</textarea></label>`;
      else if (a.type === 'sheets.append') inner = accSelect(a, 'sheets') + `<label class="field">Столбцы строки, по одному на строку<textarea data-k="row" data-tpl rows="6">${esc((a.row || []).join('\n'))}</textarea></label>`;
      else inner = `<label class="field">Адрес (https)<input data-k="url" value="${esc(a.url)}" placeholder="https://example.ru/hook"></label>
        <p class="hint">Уйдёт POST с JSON: {"event": "...", "data": {все поля события}}.</p>`;
      html = `<h2>${esc(cat.actions[a.type].title)}</h2><p class="hint">${esc(cat.actions[a.type].about)}</p>${inner}
        ${a.type === 'webhook.send' ? '' : chips()}
        <div class="row au-del">${n.i > 0 ? '<button class="btn small" type="button" data-move="-1">Выше</button>' : ''}${n.i < draft.spec.actions.length - 1 ? '<button class="btn small" type="button" data-move="1">Ниже</button>' : ''}
          ${draft.spec.actions.length > 1 ? '<button class="btn small danger" type="button" data-rm>Убрать действие</button>' : ''}</div>`;
    }
    insp.innerHTML = html;
  };
  const read = () => {
    draft.name = $('[data-name]', root).value;
    const n = nodes().find(x => x.id === sel);
    if (!n) return;
    if (n.kind === 'trigger') { const time = $('[data-time]', insp); if (time) draft.spec.trigger.time = time.value; }
    else if (n.kind === 'cond') {
      const c = draft.spec.if[n.i];
      c.field = $('[data-ck="field"]', insp).value; c.op = $('[data-ck="op"]', insp).value;
      if (['empty', 'not_empty'].includes(c.op)) delete c.value; else c.value = $('[data-ck="value"]', insp).value;
    } else {
      const a = draft.spec.actions[n.i];
      $$('[data-k]', insp).forEach(inp => {
        const k = inp.dataset.k, v = inp.value;
        a[k] = k === 'account' ? (v ? +v : null) : k === 'to' ? v.split(/[,;\s]+/).filter(Boolean) : k === 'row' ? v.split('\n').map(x => x.trim()).filter(Boolean) : v;
      });
    }
  };
  const redraw = () => { drawBoard(); drawInsp(); };
  const select = id => { read(); sel = id; redraw(); insp.scrollTop = 0; if (matchMedia('(max-width:860px)').matches) insp.scrollIntoView({behavior: reduce ? 'auto' : 'smooth', block: 'start'}); };
  const touch = () => {
    dirty = true;
    if (saved) { saved = null; $('[data-acts]', root).innerHTML = ''; $('[data-save]', root).textContent = 'Сохранить'; }
  };
  const close = async () => {
    if (dirty && !await askConfirm('Закрыть без сохранения? Правки пропадут.', 'Закрыть', true)) return;
    root.remove(); document.documentElement.classList.remove('au-lock'); document.removeEventListener('keydown', onKey, true);
    AU.studioRefresh = null;
    if (saved || draft.id) reload();
  };
  const onKey = e => { if (e.key === 'Escape' && !$('.rvm') && root.isConnected) { e.stopImmediatePropagation(); close(); } };
  document.addEventListener('keydown', onKey, true);
  AU.studioRefresh = () => redraw(); // подключили чат или таблицу: обновить выбор
  redraw();
  addEventListener('resize', () => root.isConnected && drawBoard());

  // меню «+»: что можно вставить в это место
  const plusMenu = (btn, where) => {
    $('.au-menu', root)?.remove();
    const canCond = where.startsWith('mid'), at = +where.replace(/\D/g, '') || 0;
    const items = [...(canCond ? [['cond', 'Условие']] : []), ...Object.entries(cat.actions).map(([k, v]) => [k, v.title])];
    const m = document.createElement('div'); m.className = 'au-menu'; m.setAttribute('role', 'menu');
    m.innerHTML = items.map(([k, t]) => `<button type="button" role="menuitem" data-ins="${esc(k)}">${auSvg(k === 'cond' ? 'cond' : k)}${esc(t)}</button>`).join('');
    board.appendChild(m);
    m.style.left = Math.max(8, btn.offsetLeft + 34) + 'px'; m.style.top = Math.max(8, btn.offsetTop - 10) + 'px';
    m.addEventListener('click', e => {
      const b = e.target.closest('[data-ins]'); if (!b) return;
      read();
      const k = b.dataset.ins, firstAct = 1 + draft.spec.if.length;
      if (k === 'cond') {
        const f = fieldsOf()[0], opts = cat.choices[f];
        const pos = Math.min(at - 1, draft.spec.if.length);
        draft.spec.if.splice(pos, 0, {field: f, op: '=', value: opts ? Object.keys(opts)[0] : ''}); sel = 'c' + pos;
      } else {
        const pos = where === 'end' ? draft.spec.actions.length : where.startsWith('mid') ? Math.max(0, at - firstAct) : at;
        draft.spec.actions.splice(pos, 0, auBlank(k)); sel = 'a' + pos;
      }
      m.remove(); touch(); redraw();
    });
  };

  // перетаскивание действий: меняем порядок
  let drag = null;
  board.addEventListener('pointerdown', e => {
    const n = e.target.closest('[data-drag]'); if (!n || e.button) return;
    drag = {el: n, i: +n.dataset.drag, y0: e.clientY, top: n.offsetTop, moved: false};
  });
  board.addEventListener('pointermove', e => {
    if (!drag) return;
    const dy = e.clientY - drag.y0;
    if (!drag.moved && Math.abs(dy) < 6) return;
    if (!drag.moved) { drag.moved = true; try { drag.el.setPointerCapture(e.pointerId); } catch (err) { /* без захвата тоже работает */ } drag.el.classList.add('dragging'); }
    drag.el.style.top = drag.top + dy + 'px';
  });
  const drop = e => {
    if (!drag) return;
    const d = drag; drag = null;
    if (!d.moved) return;
    d.el.classList.remove('dragging');
    const step = AU_NODE.h + AU_NODE.gap, to = Math.max(0, Math.min(draft.spec.actions.length - 1, d.i + Math.round((e.clientY - d.y0) / step)));
    read();
    const [a] = draft.spec.actions.splice(d.i, 1); draft.spec.actions.splice(to, 0, a);
    sel = 'a' + to; d.el.dataset.skipClick = '1'; if (to !== d.i) touch(); redraw();
  };
  board.addEventListener('pointerup', drop);
  board.addEventListener('pointercancel', () => { if (drag?.moved) redraw(); drag = null; }); // палец начал прокрутку: порядок не трогаем

  root.addEventListener('click', async e => {
    if (!e.target.closest('.au-menu,[data-plus]')) $('.au-menu', root)?.remove();
    const b = e.target.closest('button'); if (!b || !root.contains(b)) return;
    const d = b.dataset;
    if (d.node) { if (d.skipClick) { delete d.skipClick; return; } select(d.node); return; }
    if (d.plus) { plusMenu(b, d.plus); return; }
    if ('shut' in d) { close(); return; }
    if (d.field) {
      const el = lastTpl && insp.contains(lastTpl) ? lastTpl : $('[data-tpl]', insp);
      if (!el) return;
      const ins = `{${d.field}}`, s = el === lastTpl ? el.selectionStart : el.value.length; // ещё не ставили курсор: в конец
      el.value = el.value.slice(0, s) + ins + el.value.slice(el === lastTpl ? el.selectionEnd : s);
      el.focus(); el.selectionStart = el.selectionEnd = s + ins.length;
      read(); touch(); drawBoard();
      return;
    }
    if (d.connect) { read(); d.connect === 'telegram' ? auTelegram() : auSheet(); return; }
    read();
    const n = nodes().find(x => x.id === sel);
    if ('rm' in d && n) {
      if (n.kind === 'cond') { draft.spec.if.splice(n.i, 1); sel = n.i ? 'c' + (n.i - 1) : 'trigger'; }
      else { draft.spec.actions.splice(n.i, 1); sel = 'a' + Math.max(0, n.i - 1); }
      touch(); redraw();
    } else if (d.move && n) {
      const to = n.i + +d.move, [a] = draft.spec.actions.splice(n.i, 1);
      draft.spec.actions.splice(to, 0, a); sel = 'a' + to; touch(); redraw();
    } else if ('save' in d) {
      busy(b, true);
      try {
        const r = await api('/api/automations', {method: 'POST', body: {id: draft.id, name: draft.name, spec: draft.spec}});
        draft.id = r.id; draft.hook = r.hook; saved = r; dirty = false;
        AU.d = await api('/api/automations');
        busy(b, false); b.textContent = 'Сохранено';
        $('[data-acts]', root).innerHTML = '<button class="btn" type="button" data-try>Пробный запуск</button>';
        $('[data-try]', root).onclick = ev => auTest(ev.currentTarget, r.id, () => { root.remove(); document.documentElement.classList.remove('au-lock'); AU.studioRefresh = null; });
        toast(draft.hook ? 'Сохранено. Адрес вебхука — в карточке сценария.' : 'Сохранено. Теперь пробный запуск.');
      } catch (err) { busy(b, false); toast(err.message, true); }
    }
  });
  root.addEventListener('focusin', e => { if (e.target.matches('[data-tpl]')) lastTpl = e.target; });
  root.addEventListener('input', e => { if (insp.contains(e.target) || e.target.matches('[data-name]')) { read(); touch(); drawBoard(); } });
  root.addEventListener('change', e => {
    if (!insp.contains(e.target)) return;
    if (e.target.matches('[data-trigger]')) {
      draft.spec.trigger = {type: e.target.value, ...(e.target.value === 'schedule.daily' ? {time: '19:00'} : {})};
      draft.spec.if = draft.spec.if.filter(c => fieldsOf().includes(c.field));
      touch(); redraw();
    } else if (e.target.matches('[data-ck="field"]')) {
      const c = draft.spec.if[+sel.slice(1)], opts = cat.choices[e.target.value];
      c.field = e.target.value; c.value = opts ? Object.keys(opts)[0] : ''; touch(); redraw();
    } else { read(); touch(); redraw(); }
  });
  setTimeout(() => (draft.name ? board : $('[data-name]', root)).focus?.({preventScroll: true}), 50);
}
