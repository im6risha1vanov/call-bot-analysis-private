// Сотрудники: список людей и карточка человека (Обзор, Тренировки, Доступ). Менеджер видит
// свою карточку в разделе «Моя карточка», без доступов.
'use strict';

const FOCUS_CHIP = {'получилось': 'good', 'частично': 'warn', 'не получилось': 'bad'};
const botChip = p => p.linked ? '<span class="chip good">в боте</span>' : p.invite_until ? '<span class="chip warn">ждёт по ссылке</span>' : '<span class="chip">не подключён</span>';
const crmChip = p => p.crm_links ? `<span class="chip ${p.crm_used ? 'good' : ''}">${p.crm_used ? 'входил ' + esc(fmtWhen(p.crm_used)) : 'ссылка выдана'}</span>` : '<span class="chip">нет ссылки</span>';
const exBtns = list => (list || []).map(c => `<button class="btn small" type="button" data-review="${c.id}">${esc(c.at ? when(c.at) : 'звонок ' + c.id)}</button>`).join('');

VIEWS.staff = async (view, arg) => {
  if (arg) return personView(view, arg);
  setHead({actions: S.me.readonly ? '' : '<button class="btn primary" type="button" data-add>Добавить человека</button>'});
  $('[data-add]', $('#top'))?.addEventListener('click', addPerson);
  const d = await loading(view, api('/api/staff'), 'table');
  const order = {manager: 0, head: 1, owner: 2};
  const items = [...d.items].sort((a, b) => (order[a.role] ?? 3) - (order[b.role] ?? 3));
  view.innerHTML = `<div class="page"><section class="card blk"><div class="tbl-wrap"><table class="tbl anim">
    <thead><tr><th>Сотрудник</th><th>Бот</th><th class="hide-m">Вход в CRM</th><th></th></tr></thead><tbody>
    ${items.map((p, r) => `<tr class="click" tabindex="0" data-person="${esc(p.ext)}" data-css="--r:${r}">
      <td><span class="who">${ava(p.name)}<span>${esc(p.name)}<small>${esc(p.role_title)}${/^\d+$/.test(p.ext) ? ' · доб. ' + esc(p.ext) : ''}${p.kitchen ? ' · свой скрипт' : ''}</small></span></span></td>
      <td>${botChip(p)}</td><td class="hide-m">${crmChip(p)}</td><td>${svg('chev', 'go')}</td></tr>`).join('')}
    </tbody></table></div></section></div>`;
  const open = tr => go('staff', tr.dataset.person);
  $('tbody', view).addEventListener('click', e => { const tr = e.target.closest('[data-person]'); if (tr) open(tr); });
  $('tbody', view).addEventListener('keydown', e => { const tr = e.target.closest('[data-person]'); if (tr && e.key === 'Enter') open(tr); });
};

function addPerson() {
  const box = modal(`${modalHead('Добавить человека', 'Менеджеров из Mango бот добавляет сам. С ником в Telegram хватит простого /start в боте; ссылка нужна, если ника нет.')}
    <div class="rvm-body"><div class="outform">
      <label class="field">Кто<select data-v="role"><option value="manager">Менеджер</option><option value="head">Руководитель</option></select></label>
      <label class="field" data-extf>Добавочный в Mango<input data-v="ext" inputmode="numeric" maxlength="6" placeholder="15"></label>
      <label class="field">Имя<input data-v="name" maxlength="80" placeholder="Например, Пётр Иванов"></label>
      <label class="field">Ник в Telegram, если знаете<input data-v="user" maxlength="33" placeholder="@nick"></label>
      <div class="row wide"><button class="btn primary" type="button" data-go>Добавить и дать ссылку</button></div></div>
      <div data-out></div>
      <p class="hint">Ссылка одноразовая и действует 7 дней. Руководитель получает отчёты по отделу и горячие потери.</p></div>`);
  const v = k => $(`[data-v="${k}"]`, box);
  v('role').addEventListener('change', () => { $('[data-extf]', box).hidden = v('role').value !== 'manager'; });
  $('[data-go]', box).addEventListener('click', async e => {
    busy(e.currentTarget, true);
    try {
      const r = await api('/api/staff', {method: 'POST', body: {role: v('role').value, person_ext: v('ext').value, name: v('name').value, username: v('user').value}});
      linkBox($('[data-out]', box), r.link, 'Отправьте ссылку человеку лично. Он появится в списке после обновления страницы.');
      doneBtn(e.currentTarget, 'Добавлен');
      if (S.page === 'staff' && !S.arg) reload();
    } catch (err) { busy(e.currentTarget, false); toast(err.message, true); }
  });
}

VIEWS.me = async view => personView(view, null);

const PERSON_TABS = [['over', 'Обзор'], ['train', 'Тренировки'], ['access', 'Доступ']];

async function personView(view, ext) {
  const p = await loading(view, api('/api/person' + qs({person: ext || ''})), 'page');
  const tabs = PERSON_TABS.filter(([k]) => k === 'access' ? !!p.access : k === 'train' ? p.manager : true);
  let tab = tabs.some(([k]) => k === S.personTab) ? S.personTab : 'over';
  const own = !ext;
  const dir = S.me.role === 'director';
  const links = p.manager ? `<div class="links">
    <button class="ghost" type="button" data-go="calls"${dir ? ` data-fext="${esc(p.ext)}"` : ''}>Звонки</button>
    <button class="ghost" type="button" data-go="callbacks"${dir ? ` data-fext="${esc(p.ext)}"` : ''}>Перезвоны</button>
    <button class="ghost" type="button" data-go="deals"${dir ? ` data-fext="${esc(p.ext)}"` : ''}>Сделки</button>
    ${dir ? `<button class="ghost" type="button" data-go="queue" data-arg="${esc(p.ext)}">Очередь</button>` : ''}</div>` : '';
  setHead({title: own ? 'Моя карточка' : p.name, sub: `${esc(p.role_title)}${/^\d+$/.test(p.ext) ? ' · доб. ' + esc(p.ext) : ''}`,
    back: own ? null : () => go('staff'), filters: links});
  const draw = () => {
    view.innerHTML = `<div class="page">${tabs.length > 1 ? tabsHtml(tabs, tab) : ''}<div class="page" data-body></div></div>`;
    $$('[data-tab]', view).forEach(b => b.addEventListener('click', () => { tab = S.personTab = b.dataset.tab; draw(); }));
    const body = $('[data-body]', view);
    Promise.resolve(PERSON_TAB[tab](body, p, () => personView(view, ext))).catch(e => { body.innerHTML = emptyCard(esc(e.message)); });
  };
  draw();
}

const kitchenNote = () => '<div class="card hint blk">Звонки этого сотрудника бот разбирает по отдельному скрипту (кухни), поэтому разговоров, результатов и ошибок здесь нет.</div>';

// Слабые места и сильные стороны из памяти бота; если памяти ещё нет — частые ошибки по цифрам.
function weakHtml(p, card) {
  const weak = card?.weak_spots || [];
  if (weak.length) {
    return weak.map((x, k) => `<div class="mem-item"><div class="mem-title"><b>${k + 1}. ${esc(x.title)}</b>${x.counts ? `<span class="hint">за 7 дн. ${x.counts.week} · неделей раньше ${x.counts.prev}</span>` : ''}</div>
      ${x.pattern ? `<p>${esc(x.pattern)}</p>` : ''}${x.advice ? `<p class="mem-say">Как лучше: ${esc(x.advice)}</p>` : ''}
      ${x.examples?.length ? `<div class="p-ex">${exBtns(x.examples)}</div>` : ''}</div>`).join('');
  }
  if (!p.mistakes.length) return '<div class="empty">Потерь с понятной причиной за 30 дней нет.</div>';
  return p.mistakes.slice(0, 5).map(x => `<div class="mem-item"><div class="mem-title"><b>${esc(x.label)}</b><span class="hint">за 30 дн. ${x.month} · за 7 дн. ${x.week}</span></div>
    ${x.examples.length ? `<div class="p-ex">${exBtns(x.examples)}</div>` : ''}</div>`).join('');
}
function strongHtml(p, card) {
  const good = card?.strengths || [];
  if (good.length) return good.map(x => `<div class="p-pos"><span>${esc(x.title)}</span><span class="p-ex">${exBtns(x.examples)}</span></div>`).join('');
  if (p.positives.length) return p.positives.slice(0, 4).map(x => `<div class="p-pos"><span>${esc(x.why)}</span><button class="btn small" type="button" data-review="${x.id}">${esc(when(x.at))}</button></div>`).join('');
  return '<div class="empty">Пока не отмечено.</div>';
}

function memoryModal(c) {
  const box = modal(`${modalHead('Память бота от ' + ddmm(c.day), `по ${c.talks} ${plural(c.talks, 'разговору', 'разговорам', 'разговорам')} за 30 дней`)}
    <div class="rvm-body">${c.focus ? `<section><h3>Фокус</h3><div>${esc(c.focus)}</div>${c.focus_result ? ` <span class="chip ${FOCUS_CHIP[c.focus_result] || ''}">${esc(c.focus_result)}</span>` : ''}</section>` : ''}
      ${c.trend ? `<section><h3>Как идёт</h3><div>${esc(c.trend)}</div></section>` : ''}
      <section><h3>Слабые места</h3>${weakHtml({mistakes: []}, c)}</section>
      <section><h3>Что получается</h3>${strongHtml({positives: []}, c)}</section></div>`, true);
  return box;
}

const PERSON_TAB = {
  over(el, p) {
    if (!p.manager) {
      el.innerHTML = `<div class="card blk hint">${esc(p.role_title)}: цифр по звонкам нет. Доступы — на вкладке «Доступ».</div>`;
      return;
    }
    const dw = p.dials.week, dp = p.dials.prev;
    if (p.kitchen) {
      el.innerHTML = kitchenNote() + `<div class="kpis k3">${[
        {key: 'kd', label: 'Наборов за 7 дней', value: dw.dials, delta: delta(dw.dials, dp.dials), vs: dp.dials ? 'к прошлой неделе' : ''},
        {key: 'kd30', label: 'Наборов за 30 дней', value: p.dials.month.dials},
        {key: 'kl', label: 'На линии за 7 дней', value: dw.seconds, kind: 'time'}].map((k, i) => kpiHtml(k, i + 1)).join('')}</div>`;
      countUp(el);
      return;
    }
    const w = p.periods.week, pr = p.periods.prev;
    const cards = p.memory || [];
    const mem = cards[0];
    let i = 0;
    const html = [];
    if (mem && mem.focus) {
      html.push(`<section class="card focus blk" data-css="--i:${i++}"><span class="lab">Фокус от бота · ${esc(ddmm(mem.day))}</span><div class="big">${esc(mem.focus)}</div>
        ${mem.previous_focus ? `<p class="hint" data-css="margin:8px 0 0">Прошлый фокус: «${esc(mem.previous_focus)}»${mem.focus_result ? ` <span class="chip ${FOCUS_CHIP[mem.focus_result] || ''}">${esc(mem.focus_result)}</span>` : ''}</p>` : ''}</section>`);
    }
    const vs = pr.talks || dp.dials ? 'к прошлой неделе' : '';
    html.push(`<div class="kpis">${[
      {key: 'pd', label: 'Наборов за 7 дней', value: dw.dials, delta: delta(dw.dials, dp.dials), vs},
      {key: 'pt', label: 'Разговоров разобрано', value: w.talks, delta: delta(w.talks, pr.talks), vs},
      {key: 'ps', label: 'С результатом', value: w.success, delta: delta(w.success, pr.success), vs, sub: w.talks ? `${pctOf(w.success, w.talks)} разговоров` : ''},
      {key: 'ph', label: 'Горячих потерь', value: w.hot, sub: pr.hot != null ? `неделей раньше ${pr.hot}` : ''}].map(k => kpiHtml(k, i++)).join('')}</div>`);
    html.push(`<div class="grid2"><section class="card blk" data-css="--i:${i++}"><div class="card-h"><h2>Слабые места</h2><span class="hint">${mem ? 'память бота от ' + esc(ddmm(mem.day)) : 'за 30 дней'}</span></div>${weakHtml(p, mem)}</section>
      <section class="card blk" data-css="--i:${i++}"><div class="card-h"><h2>Что получается</h2></div>${strongHtml(p, mem)}</section></div>`);
    if (cards.length > 1) {
      html.push(moreBlock('История памяти', `${cards.length} ${plural(cards.length, 'карточка', 'карточки', 'карточек')}`, `<div class="list">${cards.map((c, k) => `<button class="item" type="button" data-mem="${k}">
        <div class="main-l"><div class="t">${esc(ddmm(c.day))}</div><div class="s">${esc(c.focus || c.trend || '')}</div></div>
        <div class="r">${c.focus_result ? `<span class="chip ${FOCUS_CHIP[c.focus_result] || ''}">${esc(c.focus_result)}</span>` : ''}</div></button>`).join('')}</div>`, i++));
    } else if (!cards.length) {
      html.push(`<p class="hint blk" data-css="--i:${i++}">${p.memory_on ? `Бот пишет память каждый день после ${p.memory_hour}:00, если были новые разборы.` : 'Память бота пока выключена.'}</p>`);
    }
    el.innerHTML = html.join('');
    countUp(el);
    $$('[data-mem]', el).forEach(b => b.addEventListener('click', () => memoryModal(cards[+b.dataset.mem])));
  },

  train(el, p, again) {
    const t = p.trainings, top = p.mistakes[0];
    const canAssign = S.me.role === 'director' && !S.me.test;
    el.innerHTML = `${canAssign ? `<section class="card blk"><div class="card-h"><h2>Назначить тренировку</h2></div>
        <p class="lead" data-css="margin-bottom:12px">Менеджеру придёт сообщение в боте с кнопкой старта. Тренажёр работает в Telegram, там можно отвечать голосом.</p>
        <div class="row">${top ? `<button class="btn primary" type="button" data-tr="drill">Отработать «${esc(top.label)}»</button>` : ''}
          <button class="btn" type="button" data-tr="dialog">Разговор целиком</button></div></section>` : ''}
      ${t.pending.length ? `<section class="card blk" data-css="--i:1"><div class="card-h"><h2>Назначено, ещё не начато</h2><span class="hint">${t.pending.length}</span></div><div class="list">
        ${t.pending.map(x => `<div class="item"><div class="main-l"><div class="t">${esc(x.mode)}</div>${x.topic ? `<div class="s">${esc(x.topic)}</div>` : ''}</div>
          ${x.link ? `<a class="btn small primary" href="${esc(x.link)}" target="_blank" rel="noopener">Начать в тренажёре</a>` : ''}</div>`).join('')}</div></section>` : ''}
      <section class="card blk" data-css="--i:2"><div class="card-h"><h2>Пройденные тренировки</h2><span class="hint">${t.items.length}</span></div>${t.items.length ? `<div class="tbl-wrap"><table class="tbl anim">
        <thead><tr><th>Когда</th><th>Режим</th><th class="hide-m">Тема</th><th>Итог</th></tr></thead><tbody>
        ${t.items.map((x, r) => `<tr class="click" data-tid="${x.id}" data-css="--r:${Math.min(r, 20)}"><td class="nw">${fmtWhen(x.at)}</td><td>${esc(x.mode)}</td><td class="hide-m">${esc(x.topic || '')}</td><td>${esc(x.result)}${x.test ? ' <span class="s">· пробная</span>' : ''}</td></tr>`).join('')}
        </tbody></table></div>` : `<div class="empty">Тренировок пока не было.${S.me.role === 'manager' ? ' Откройте разбор звонка и нажмите «Потренировать этот момент».' : ''}</div>`}</section>`;
    $$('tr[data-tid]', el).forEach(tr => tr.addEventListener('click', () => trainingDetail(tr.dataset.tid)));
    $$('[data-tr]', el).forEach(b => b.addEventListener('click', async () => {
      busy(b, true);
      try {
        await api('/api/trainings', {method: 'POST', body: {person_ext: p.ext, mode: b.dataset.tr, topic: b.dataset.tr === 'drill' ? top.label : ''}});
        doneBtn(b, 'Назначено'); setTimeout(again, 900);
      } catch (e) { busy(b, false); toast(e.message, true); }
    }));
  },

  access(el, p, again) {
    const a = p.access;
    el.innerHTML = `<section class="card blk"><div class="card-h"><h2>Telegram-бот</h2>${botChip(a)}</div>
        <p class="lead">${a.linked ? `Подключён${a.username ? ' как @' + esc(a.username) : ''}. Получает разборы и уведомления.` : a.invite_until ? `Ждёт подключения по ссылке до ${fmtWhen(a.invite_until)}.` : 'Не подключён.'}
          ${a.reviewed === null ? '' : a.reviewed ? ' Звонки разбираются ботом.' : ' Звонки не разбираются ботом.'}</p>
        <div class="row" data-css="margin-top:12px">${a.linked ? '' : '<button class="btn" type="button" data-inv>Ссылка в бота</button>'}
          ${a.can_unlink ? '<button class="btn danger" type="button" data-unlink>Отключить от бота</button>' : ''}</div><div data-inv-out></div></section>
      ${a.can_copy ? `<section class="card blk" data-css="--i:1"><label class="rb-sw"><input type="checkbox" data-copies${a.copies ? ' checked' : ''}><span class="rb-sw-ui"></span>
        <span><b>Копии всех разборов</b><span class="muted">Присылать в Telegram полные разборы всех менеджеров. Только руководителям.</span></span></label></section>` : ''}
      <section class="card blk" data-css="--i:2"><div class="card-h"><h2>Вход в CRM</h2></div>
        <p class="lead">${a.crm_links ? `Личных ссылок: ${a.crm_links}. Последний вход: ${a.crm_used ? fmtWhen(a.crm_used) : 'ещё не входил'}.` : 'Личной ссылки нет.'}</p>
        <div class="row" data-css="margin-top:12px">${p.manager && !S.me.readonly ? '<button class="btn" type="button" data-crm>Выдать ссылку в CRM</button>' : ''}
          <button class="btn ghost" type="button" data-go="settings" data-arg="access">Все ссылки →</button></div><div data-crm-out></div></section>`;
    $('[data-inv]', el)?.addEventListener('click', async e => {
      busy(e.currentTarget, true);
      try { const r = await api('/api/staff/invite', {method: 'POST', body: {person_ext: p.ext}}); linkBox($('[data-inv-out]', el), r.link, 'Ссылка одноразовая, действует 7 дней.'); }
      catch (err) { toast(err.message, true); }
      busy(e.currentTarget, false);
    });
    $('[data-crm]', el)?.addEventListener('click', async e => {
      busy(e.currentTarget, true);
      try { const r = await api('/api/access', {method: 'POST', body: {role: 'manager', person_ext: p.ext}}); linkBox($('[data-crm-out]', el), r.link, 'Ссылку видно только сейчас: скопируйте и отправьте лично.'); }
      catch (err) { toast(err.message, true); }
      busy(e.currentTarget, false);
    });
    $('[data-unlink]', el)?.addEventListener('click', async () => {
      if (!await askConfirm(`Отключить ${p.name} от бота?`, 'Отключить', true)) return;
      try { await api('/api/staff/unlink', {method: 'POST', body: {person_ext: p.ext}}); toast('Отключён'); again(); }
      catch (err) { toast(err.message, true); }
    });
    $('[data-copies]', el)?.addEventListener('change', async e => {
      try { await api('/api/staff/copies', {method: 'POST', body: {person_ext: p.ext, on: e.target.checked}}); toast(e.target.checked ? 'Копии включены' : 'Копии выключены'); }
      catch (err) { toast(err.message, true); e.target.checked = !e.target.checked; }
    });
  },
};

async function trainingDetail(id) {
  const box = modal(`${modalHead('Загружаю…')}`);
  let t;
  try { t = await api('/api/trainings/' + id); } catch (e) { $('h2', box).textContent = e.message; return; }
  if (!box.isConnected) return;
  box.innerHTML = `${modalHead(`${t.who} · ${t.mode}`, `${esc(fmtWhen(t.at))}${t.topic ? ' · ' + esc(t.topic) : ''}${t.test ? ' · пробная' : ''}`, `<span class="rvm-chip">${esc(t.result)}</span>`)}
    <div class="rvm-body">${t.checks.length ? t.checks.map((c, i) => `<div class="rvm-item ${c.passed ? 'good' : 'miss'}">
        <div><b>${i + 1}. Клиент:</b> ${esc(c.objection)}</div><div><b>Ответ:</b> ${esc(c.answer)}</div><div class="rvm-q">${c.passed ? 'Зачтено' : 'Не зачтено'}${c.comment ? ': ' + esc(c.comment) : ''}</div></div>`).join('')
      : t.turns.length ? t.turns.map(x => `<div class="rvm-line"><b>${esc(x.who)}</b> ${esc(x.text)}</div>`).join('') : '<div class="rvm-mut">Реплик нет.</div>'}</div>`;
}
