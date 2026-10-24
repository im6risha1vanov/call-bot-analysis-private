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
};

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

function auEditor(draft) {
  const cat = auCat();
  const body = openDrawer({title: draft.id ? 'Сценарий' : 'Новый сценарий', wide: true, html: '<div data-ed></div>'});
  const box = $('[data-ed]', body);
  let lastTpl = null;
  const fieldsOf = () => cat.triggers[draft.spec.trigger.type].fields;
  const groups = [...new Set(Object.values(cat.triggers).map(t => t.group))];
  const accSelect = (a, kind) => {
    const list = AU.d.accounts.filter(x => x.kind === kind && x.status === 'ready');
    return list.length ? `<label class="field">${kind === 'telegram' ? 'Чат' : 'Таблица'}<select data-k="account">${list.map(x =>
      `<option value="${x.id}"${x.id === a.account ? ' selected' : ''}>${esc(x.title)}</option>`).join('')}</select></label>`
      : `<p class="hint bad-text">${kind === 'telegram' ? 'Нет подключённого чата.' : 'Нет подключённой таблицы.'} <button class="linkish" type="button" data-connect="${kind}">Подключить</button></p>`;
  };
  const actionHtml = (a, i) => {
    const meta = cat.actions[a.type];
    let inner = '';
    if (a.type === 'telegram.send') inner = accSelect(a, 'telegram') + `<label class="field">Текст сообщения<textarea data-k="text" data-tpl rows="5">${esc(a.text)}</textarea></label>`;
    else if (a.type === 'email.send') inner = `${cat.email_ready ? '' : '<p class="hint bad-text">Почта CRM не настроена: письмо не уйдёт.</p>'}
      <label class="field">Кому (через запятую)<input data-k="to" value="${esc((a.to || []).join(', '))}" placeholder="boss@company.ru"></label>
      <label class="field">Тема<input data-k="subject" data-tpl value="${esc(a.subject)}"></label>
      <label class="field">Текст письма<textarea data-k="text" data-tpl rows="5">${esc(a.text)}</textarea></label>`;
    else if (a.type === 'sheets.append') inner = accSelect(a, 'sheets') + `<label class="field">Столбцы строки, по одному на строку<textarea data-k="row" data-tpl rows="5">${esc((a.row || []).join('\n'))}</textarea></label>`;
    else inner = `<label class="field">Адрес (https)<input data-k="url" value="${esc(a.url)}" placeholder="https://example.ru/hook"></label>
      <p class="hint">Уйдёт POST с JSON: {"event": "...", "data": {все поля события}}.</p>`;
    return `<div class="au-act" data-i="${i}"><div class="au-top"><b>${i + 1}. ${esc(meta.title)}</b><span class="sp"></span>
      <button class="x" type="button" data-rm-act="${i}" aria-label="Убрать действие" data-css="width:28px;height:28px">${svg('x', '')}</button></div>${inner}</div>`;
  };
  const valueInput = c => {
    const hide = ['empty', 'not_empty'].includes(c.op) ? ' hidden' : '', opts = cat.choices[c.field];
    return opts ? `<select class="inp" data-ck="value"${hide}>${Object.entries(opts).map(([v, l]) => `<option value="${esc(v)}"${String(c.value) === v ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select>`
      : `<input class="inp" data-ck="value" value="${esc(c.value ?? '')}"${hide}>`;
  };
  const condHtml = (c, i) => `<div class="au-cond" data-c="${i}">
    <select class="inp" data-ck="field">${fieldsOf().map(f => `<option value="${esc(f)}"${f === c.field ? ' selected' : ''}>${esc(auLabel(f))}</option>`).join('')}</select>
    <select class="inp" data-ck="op">${cat.ops.map(o => `<option value="${esc(o)}"${o === c.op ? ' selected' : ''}>${esc(cat.op_labels[o] || o)}</option>`).join('')}</select>
    ${valueInput(c)}
    <button class="x" type="button" data-rm-cond="${i}" aria-label="Убрать условие" data-css="width:28px;height:28px">${svg('x', '')}</button></div>`;
  const draw = () => {
    const t = draft.spec.trigger;
    box.innerHTML = `<label class="field">Название<input data-name value="${esc(draft.name)}" maxlength="120"></label>
      <h3 class="au-h">Когда</h3>
      <div class="row"><select class="inp" data-trigger>${groups.map(g => `<optgroup label="${esc(g)}">${Object.entries(cat.triggers).filter(([, v]) => v.group === g).map(([k, v]) =>
        `<option value="${esc(k)}"${k === t.type ? ' selected' : ''}>${esc(v.title)}</option>`).join('')}</optgroup>`).join('')}</select>
        ${t.type === 'schedule.daily' ? `<input class="inp" type="time" data-time value="${esc(t.time || '19:00')}">` : ''}</div>
      ${t.type === 'webhook.in' ? `<p class="hint">${draft.hook ? 'Адрес для вызова показан в карточке сценария.' : 'Адрес для вызова появится после сохранения.'}</p>` : ''}
      <h3 class="au-h">Если <span class="hint">необязательно</span></h3>
      ${draft.spec.if.map(condHtml).join('')}<button class="btn small ghost" type="button" data-add-cond>+ условие</button>
      <h3 class="au-h">Сделать</h3>
      ${draft.spec.actions.map(actionHtml).join('')}
      <div class="row"><select class="inp" data-new-act>${Object.entries(cat.actions).map(([k, v]) => `<option value="${esc(k)}">${esc(v.title)}</option>`).join('')}</select>
        <button class="btn small" type="button" data-add-act>+ действие</button></div>
      <h3 class="au-h">Поля события <span class="hint">нажмите, чтобы вставить в текст</span></h3>
      <div class="chips">${fieldsOf().map(f => `<button class="chip au-field" type="button" data-field="${esc(f)}">${esc(auLabel(f))}</button>`).join('')}</div>
      <div class="row au-save"><button class="btn primary" type="button" data-save>Сохранить</button><span class="hint" data-msg>${draft.id ? 'Сохранение выключит сценарий до новой пробы.' : ''}</span></div>`;
  };
  // Поля формы в draft: перерисовываем только при смене состава (событие, добавить, убрать).
  const read = () => {
    draft.name = $('[data-name]', box).value;
    const time = $('[data-time]', box); if (time) draft.spec.trigger.time = time.value;
    $$('[data-c]', box).forEach(el => {
      const c = draft.spec.if[+el.dataset.c];
      c.field = $('[data-ck="field"]', el).value; c.op = $('[data-ck="op"]', el).value;
      if (['empty', 'not_empty'].includes(c.op)) delete c.value; else c.value = $('[data-ck="value"]', el).value;
    });
    $$('[data-i]', box).forEach(el => {
      const a = draft.spec.actions[+el.dataset.i];
      $$('[data-k]', el).forEach(inp => {
        const k = inp.dataset.k, v = inp.value;
        a[k] = k === 'account' ? +v : k === 'to' ? v.split(/[,;\s]+/).filter(Boolean) : k === 'row' ? v.split('\n').map(x => x.trim()).filter(Boolean) : v;
      });
    });
  };
  draw();
  box.addEventListener('focusin', e => { if (e.target.matches('[data-tpl]')) lastTpl = e.target; });
  box.addEventListener('change', e => {
    if (e.target.matches('[data-trigger]')) {
      read();
      draft.spec.trigger = {type: e.target.value, ...(e.target.value === 'schedule.daily' ? {time: '19:00'} : {})};
      draft.spec.if = draft.spec.if.filter(c => fieldsOf().includes(c.field));
      draw();
    } else if (e.target.matches('[data-ck="field"]')) {
      read();
      const c = draft.spec.if[+e.target.closest('[data-c]').dataset.c];
      const opts = cat.choices[c.field]; c.value = opts ? Object.keys(opts)[0] : '';
      draw();
    } else if (e.target.matches('[data-ck="op"]')) {
      $('[data-ck="value"]', e.target.closest('[data-c]')).hidden = ['empty', 'not_empty'].includes(e.target.value);
    }
  });
  box.addEventListener('click', async e => {
    const b = e.target.closest('button'); if (!b) return;
    const d = b.dataset;
    if (d.field) {
      const el = lastTpl && box.contains(lastTpl) ? lastTpl : $('[data-tpl]', box);
      if (!el) return;
      const ins = `{${d.field}}`, s = el === lastTpl ? el.selectionStart : el.value.length; // ещё не ставили курсор: в конец
      el.value = el.value.slice(0, s) + ins + el.value.slice(el === lastTpl ? el.selectionEnd : s);
      el.focus(); el.selectionStart = el.selectionEnd = s + ins.length;
      return;
    }
    if (d.connect) { closeDrawer(); d.connect === 'telegram' ? auTelegram() : auSheet(); return; }
    read();
    if ('addCond' in d) { draft.spec.if.push({field: fieldsOf()[0], op: '=', value: ''}); draw(); }
    else if (d.rmCond) { draft.spec.if.splice(+d.rmCond, 1); draw(); }
    else if ('addAct' in d) { draft.spec.actions.push(auBlank($('[data-new-act]', box).value)); draw(); }
    else if (d.rmAct) { draft.spec.actions.splice(+d.rmAct, 1); draw(); }
    else if ('save' in d) {
      busy(b, true);
      try {
        const saved = await api('/api/automations', {method: 'POST', body: {id: draft.id, name: draft.name, spec: draft.spec}});
        draft.id = saved.id; draft.hook = saved.hook;
        AU.d = await api('/api/automations');
        busy(b, false);
        const row = $('.au-save', box);
        row.innerHTML = `<span class="chip good">сохранено</span><button class="btn primary" type="button" data-try>Пробный запуск</button>
          <button class="btn" type="button" data-save>Сохранить снова</button><button class="btn" type="button" data-close>Готово</button>`;
        $('[data-try]', row).onclick = ev => auTest(ev.currentTarget, saved.id, closeDrawer);
        $('[data-close]', row).onclick = () => { closeDrawer(); reload(); };
        $('#dr-x').onclick = () => { closeDrawer(); reload(); }; // список за панелью должен показать новый сценарий
      } catch (err) { busy(b, false); toast(err.message, true); }
    }
  });
}
