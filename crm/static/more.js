// Раздел «Ещё»: Источники (подбор доноров и номера в очереди), Отчёты (вопрос агенту и лента отчётов),
// Настройки (промты и доступы).
'use strict';

// ---------- источники ----------
VIEWS.sources = async (view, arg) => {
  const tab = arg === 'base' ? 'base' : 'donors';
  const head = tab === 'base' && S.me.role === 'director' ? managerSelect(S.f.ext || S.me.team[0]?.ext, '') : '';
  setHead({filters: head.replace('<option value=""></option>', '')});
  bindManager($('#top'), () => reload());
  view.innerHTML = `<div class="page">${tabsHtml([['donors', 'Подбор доноров'], ['base', 'Номера в очереди']], tab)}<div data-body></div></div>`;
  $$('[data-tab]', view).forEach(b => b.addEventListener('click', () => go('sources', b.dataset.tab === 'base' ? 'base' : null)));
  const body = $('[data-body]', view);
  try { await (tab === 'donors' ? window.SourcesPicker(body, arg) : leadsView(body)); }
  catch (e) { body.innerHTML = emptyCard(esc(e.message)); } // вкладки остаются, можно перейти на соседнюю
};

const LEAD_ORDER = ['new', 'no_answer', 'callback', 'interested', 'proposal', 'meeting', 'not_interested', 'bad_number'];
S.leadFilter = '';
async function leadsView(box) {
  const ext = S.me.role === 'director' ? (S.f.ext || S.me.team[0]?.ext) : '';
  const data = await api('/api/leads' + qs({status: S.leadFilter, ext}));
  const ro = S.me.readonly;
  const chipCls = st => st === 'new' ? 'acc' : ['meeting', 'proposal'].includes(st) ? 'good' : ['not_interested', 'bad_number'].includes(st) ? 'bad' : 'warn';
  box.innerHTML = `<div class="grid2">
    <section class="card blk"><div class="card-h"><h2>Номера в базе</h2><span class="hint">${fmt(data.total)}</span></div>
      <div class="filters" data-css="margin-bottom:12px"><button type="button" data-f="" aria-pressed="${!S.leadFilter}">Все<span class="c">${data.total}</span></button>${LEAD_ORDER.filter(k => data.counts[k]).map(k =>
        `<button type="button" data-f="${k}" aria-pressed="${S.leadFilter === k}">${esc(data.labels[k])}<span class="c">${data.counts[k]}</span></button>`).join('')}</div>
      ${data.items.length ? `<div class="tbl-wrap"><table class="tbl anim"><thead><tr><th>Номер</th><th>Клиент</th><th class="hide-m">Источник</th><th>Статус</th><th></th></tr></thead><tbody>
      ${data.items.map((l, r) => `<tr class="click" data-id="${l.id}" data-css="--r:${Math.min(r, 30)}"><td class="num nw">${esc(phone(l.number))}</td><td>${esc(l.title || '')}${l.note ? `<div class="s">${esc(l.note)}</div>` : ''}</td>
        <td class="hide-m">${esc(l.source)}</td><td><span class="chip ${chipCls(l.status)}">${esc(l.label)}${l.status === 'no_answer' ? ' · ' + l.attempts : ''}</span></td>
        <td>${ro ? '' : `<button class="x" type="button" data-del="${l.id}" aria-label="Удалить номер" data-css="width:28px;height:28px">${svg('x', '')}</button>`}</td></tr>`).join('')}
      </tbody></table></div>${data.items.length >= 500 ? '<p class="hint">Показаны последние 500.</p>' : ''}` : '<div class="empty">Здесь пока пусто. Вставьте номера в форму.</div>'}</section>
    ${ro ? '' : `<section class="card blk" data-css="--i:1"><div class="card-h"><h2>Добавить номера</h2></div>
      <p class="hint" data-css="margin:0 0 10px">По одному на строку. Можно сразу с именем и компанией через точку с запятой: <span class="num">89991234567; Иван; ООО Ромашка</span>. Повторы и номера, которые уже есть, пропущу.</p>
      <label class="field">Номера<textarea data-l="text" rows="9" placeholder="+7 999 123-45-67; Иван Петров; ООО Ромашка"></textarea></label>
      <label class="field">Откуда база<input data-l="src" maxlength="80" list="l-srcs" placeholder="например, ГЦК 17.10"><datalist id="l-srcs">${data.sources.map(s => `<option value="${esc(s)}">`).join('')}</datalist></label>
      <div class="row" data-css="margin-top:12px"><button class="btn primary" type="button" data-add>Добавить в очередь</button></div><p class="hint" data-res></p></section>`}</div>`;
  $$('[data-f]', box).forEach(b => b.addEventListener('click', () => { S.leadFilter = b.dataset.f; leadsView(box); }));
  const byId = Object.fromEntries(data.items.map(l => [String(l.id), l]));
  $('tbody', box)?.addEventListener('click', async e => {
    const del = e.target.closest('[data-del]');
    if (del) {
      if (!await askConfirm('Удалить номер из базы?', 'Удалить', true)) return;
      try { await api('/api/leads/' + del.dataset.del + qs({ext}), {method: 'DELETE'}); toast('Удалён'); leadsView(box); } catch (err) { toast(err.message, true); }
      return;
    }
    const tr = e.target.closest('tr[data-id]');
    if (tr) workDrawer(byId[tr.dataset.id], {ext, onDone: () => leadsView(box)});
  });
  $('[data-add]', box)?.addEventListener('click', async e => {
    const text = $('[data-l="text"]', box).value;
    if (!text.trim()) { toast('Вставьте номера', true); return; }
    busy(e.currentTarget, true);
    try {
      const r = await api('/api/leads', {method: 'POST', body: {text, source: $('[data-l="src"]', box).value, ext: ext || undefined}});
      const parts = [`Добавлено: ${r.added}`];
      if (r.duplicates) parts.push(`повторы и уже были в базе: ${r.duplicates}`);
      if (r.invalid) parts.push(`не распознано: ${r.invalid}`);
      toast(parts.join(', '));
      S.leadFilter = '';
      await leadsView(box);
      if (r.invalid) $('[data-res]', box).textContent = 'Не распознаны: ' + r.invalid_samples.join(' | ');
      refreshCounts();
    } catch (err) { busy(e.currentTarget, false); toast(err.message, true); }
  });
}

// ---------- отчёты и вопросы ----------
S.repFilter = 'all';
const qaHtml = (q, i = 0) => `<article class="card qa" data-css="--i:${i}"><div class="q"><span class="hint">${fmtWhen(q.at)} · вопрос</span>${esc(q.question)}</div>
  <div class="a">${q.html || '<span class="hint">Ответа нет.</span>'}</div></article>`;
const reportHtml = (r, i) => `<article class="card report click" tabindex="0" data-css="--i:${i}">
  <div class="rep-head"><b>${esc(r.title)}</b>${r.who ? `<span>${esc(r.who)}</span>` : ''}<span class="hint">${fmtWhen(r.at)}</span>${r.note ? `<span class="chip">${esc(r.note)}</span>` : ''}</div>
  <div class="rep-body">${r.html}</div>
  ${r.calls.length ? `<div class="rep-calls">${r.calls.map(c => `<button class="btn small" type="button" data-review="${c.id}">${esc(c.label)}</button>`).join('')}</div>` : ''}</article>`;

VIEWS.reports = async view => {
  const dir = S.me.role === 'director';
  const groups = dir ? [['all', 'Всё'], ['rop', 'Отделу'], ['day_manager', 'Итоги менеджеров'], ['qa', 'Вопросы']] : [['all', 'Всё'], ['qa', 'Вопросы']];
  setHead({filters: seg('rep', groups, S.repFilter, 'Что показать')});
  const [rep, asked] = await loading(view, Promise.all([api('/api/reports'), api('/api/ask')]), 'cards');
  const examples = dir
    ? ['Кто из менеджеров чаще всего теряет клиента на возражении «пришлите на почту»?', 'Что общего у звонков с результатом за неделю?', 'Какие три темы взять на планёрку завтра?']
    : ['Где я чаще всего теряю клиента?', 'Что у меня получилось лучше всего на этой неделе?', 'Какие перезвоны я обещал на эту неделю?'];
  const canAsk = !(S.me.readonly && !dir);
  view.innerHTML = `<div class="page">
    ${canAsk ? `<section class="card ask-box blk"><div class="card-h"><h2>Спросить про звонки</h2><span class="hint">отвечает агент РОПа по разобранным звонкам${dir ? '' : ' (видит только ваши)'}, до минуты</span></div>
      <textarea data-q rows="2" maxlength="2000" placeholder="${esc(examples[0])}"></textarea>
      <div class="row" data-css="margin-top:10px"><button class="btn primary" type="button" data-ask>Спросить</button>${examples.map(x => `<button class="btn small ghost" type="button" data-ex="${esc(x)}">${esc(x)}</button>`).join('')}</div></section>` : ''}
    <div class="feed" data-feed></div></div>`;
  const feed = $('[data-feed]', view);
  const draw = () => {
    const f = S.repFilter;
    const list = [
      ...(f === 'qa' ? [] : rep.items.filter(r => f === 'all' || (f === 'rop' ? r.kind !== 'day_manager' : r.kind === f)).map(r => ({at: r.at, html: i => reportHtml(r, i)}))),
      ...(f === 'all' || f === 'qa' ? asked.items.map(q => ({at: q.at, html: i => qaHtml(q, i)})) : []),
    ].sort((a, b) => String(b.at).localeCompare(String(a.at)));
    feed.innerHTML = list.length ? list.slice(0, 120).map((x, i) => x.html(Math.min(i, 10))).join('')
      : emptyCard(rep.items.length || asked.items.length ? 'Здесь пока пусто.' : 'Отчёты появятся здесь, когда бот пришлёт следующий: итоги дня вечером, сводки утром, по понедельникам и 1 числа.');
  };
  draw();
  bindSeg($('#top'), {rep: v => { S.repFilter = v; draw(); }});
  feed.addEventListener('click', e => {
    if (e.target.closest('button,a')) return;
    const r = e.target.closest('.report'); if (r) r.classList.toggle('open');
  });
  feed.addEventListener('keydown', e => { const r = e.target.closest('.report'); if (r && e.key === 'Enter') r.classList.toggle('open'); });
  const ta = $('[data-q]', view);
  $$('[data-ex]', view).forEach(b => b.addEventListener('click', () => { ta.value = b.dataset.ex; ta.focus(); }));
  $('[data-ask]', view)?.addEventListener('click', async e => {
    const btn = e.currentTarget;
    if (!ta.value.trim()) { ta.focus(); return; }
    busy(btn, true); btn.firstChild.textContent = 'Смотрю данные';
    try {
      const q = await api('/api/ask', {method: 'POST', body: {question: ta.value}});
      const item = {...q, at: q.at || new Date().toISOString()};
      asked.items.unshift(item);
      if (S.repFilter !== 'all' && S.repFilter !== 'qa') { S.repFilter = 'all'; setHead({filters: seg('rep', groups, 'all', 'Что показать')}); bindSeg($('#top'), {rep: v => { S.repFilter = v; draw(); }}); }
      feed.insertAdjacentHTML('afterbegin', qaHtml(item));
      $('.empty', feed)?.remove();
      ta.value = '';
    } catch (err) { toast(err.message, true); }
    busy(btn, false); btn.firstChild.textContent = 'Спросить';
  });
};

// ---------- настройки: промты и доступы ----------
VIEWS.settings = async (view, arg) => {
  const tab = arg === 'access' ? 'access' : 'prompts';
  view.innerHTML = `<div class="page">${tabsHtml([['prompts', 'Промты'], ['access', 'Доступы']], tab)}<div class="page" data-body></div></div>`;
  $$('[data-tab]', view).forEach(b => b.addEventListener('click', async () => {
    if (b.dataset.tab === tab) return;
    if (S.pm && S.pm.body !== S.pm.start && !await askConfirm('Несохранённые правки промта пропадут. Перейти?', 'Перейти')) return;
    go('settings', b.dataset.tab);
  }));
  const body = $('[data-body]', view);
  try { await (tab === 'access' ? accessView(body) : promptsView(body)); }
  catch (e) { body.innerHTML = emptyCard(esc(e.message)); }
};

async function accessView(box) {
  const d = await api('/api/access');
  const roleName = {director: 'Руководитель', manager: 'Менеджер', client: 'Клиент'};
  const who = i => i.role === 'manager' ? `${esc(i.name)} <span class="s">· доб. ${esc(i.ext)}</span>` : esc(i.name || roleName[i.role]) + (i.tg ? ' <span class="s">· с Telegram</span>' : '');
  box.innerHTML = `<section class="card blk"><div class="card-h"><h2>Действующие ссылки</h2><span class="hint">${d.invites.length}</span><span class="sp"></span>
      ${S.me.readonly ? '' : '<button class="btn primary" type="button" data-new>Выдать ссылку</button>'}</div>
    ${d.invites.length ? `<div class="tbl-wrap"><table class="tbl anim"><thead><tr><th>Кто</th><th class="hide-m">Вход</th><th class="hide-m">Создана</th><th>Последний вход</th><th></th></tr></thead><tbody>
      ${d.invites.map((i, r) => `<tr data-css="--r:${Math.min(r, 30)}"><td>${who(i)}</td><td class="hide-m">${roleName[i.role]}</td><td class="hide-m nw">${fmtWhen(i.created)}</td><td class="nw">${i.used ? fmtWhen(i.used) : '<span class="s">ещё не входил</span>'}</td>
        <td>${S.me.readonly ? '' : `<button class="btn small" type="button" data-revoke="${i.id}">Отозвать</button>`}</td></tr>`).join('')}</tbody></table></div>`
      : '<div class="empty">Ссылок ещё нет.</div>'}
    <p class="hint" data-css="margin:12px 0 0">По ссылке человек входит в CRM без пароля. Отозванная ссылка сразу перестаёт работать на всех устройствах.</p></section>`;
  $('[data-new]', box)?.addEventListener('click', () => newLink(d, () => accessView(box)));
  $$('[data-revoke]', box).forEach(b => b.addEventListener('click', async () => {
    if (!await askConfirm('Отозвать ссылку? Человек сразу выйдет из CRM.', 'Отозвать', true)) return;
    try { await api(`/api/access/${b.dataset.revoke}/revoke`, {method: 'POST', body: {}}); toast('Ссылка отозвана'); accessView(box); }
    catch (e) { toast(e.message, true); }
  }));
}

function newLink(d, onDone) {
  const box = modal(`${modalHead('Выдать личную ссылку', 'Ссылку видно только сейчас: скопируйте и отправьте лично. Ссылка руководителя, привязанная к его Telegram, открывает ему и «Промты».')}
    <div class="rvm-body"><div class="outform">
      <label class="field">Кому<select data-v="role"><option value="manager">Менеджеру</option><option value="client">Клиенту колл-центра</option><option value="director">Руководителю</option></select></label>
      <label class="field" data-for="manager">Менеджер<select data-v="ext">${d.managers.map(m => `<option value="${esc(m.ext)}">${esc(m.name)} · доб. ${esc(m.ext)}</option>`).join('')}</select></label>
      <label class="field" data-for="client" hidden>Клиент<select data-v="client">${d.clients.map(c => `<option>${esc(c)}</option>`).join('')}</select></label>
      <label class="field" data-for="director" hidden>Чья ссылка<select data-v="tg">${d.directors.map(p => `<option value="${esc(p.tg)}">${esc(p.name)}</option>`).join('')}<option value="">Другой человек, без Telegram</option></select></label>
      <label class="field" data-for="name" hidden>Имя<input data-v="name" maxlength="80" placeholder="Например, Евгений"></label>
      <div class="row wide"><button class="btn primary" type="button" data-go>Создать ссылку</button></div></div><div data-out></div></div>`);
  const v = k => $(`[data-v="${k}"]`, box);
  const sync = () => {
    const r = v('role').value;
    $$('[data-for]', box).forEach(f => { f.hidden = f.dataset.for === 'name' ? r !== 'director' || v('tg').value !== '' : f.dataset.for !== r; });
  };
  v('role').addEventListener('change', sync); v('tg').addEventListener('change', sync); sync();
  $('[data-go]', box).addEventListener('click', async e => {
    busy(e.currentTarget, true);
    try {
      const r = await api('/api/access', {method: 'POST', body: {role: v('role').value, person_ext: v('ext').value, client: v('client').value,
        name: v('tg').value ? '' : v('name').value, tg: v('tg').value}});
      linkBox($('[data-out]', box), r.link);
      doneBtn(e.currentTarget, 'Ссылка создана');
      onDone && onDone();
    } catch (err) { busy(e.currentTarget, false); toast(err.message, true); }
  });
}

// Режим промта: черновик живёт на странице, сохраняется только текст, по которому собран пример.
async function promptsView(view) {
  const o = await api('/api/prompts');
  const P = S.pm && o.kinds.some(k => k.kind === S.pm.kind) ? S.pm : (S.pm = {kind: o.kinds[0].kind});
  const st = await api('/api/prompts/' + P.kind);
  if (P.start === undefined || P.loadedFor !== P.kind) Object.assign(P, {start: st.body, body: st.body, history: [], note: '', token: null, tokenBody: null, preview: null, diff: [], loadedFor: P.kind});
  const changed = () => P.body !== P.start;
  const canSave = () => changed() && P.token && P.tokenBody === P.body;
  const diffHtml = lines => lines.length ? `<div class="pm-diff">${lines.map(l => `<div class="${l.op === '+' ? 'add' : 'del'}">${l.op === '+' ? '+' : '−'} ${esc(l.text)}</div>`).join('')}</div>` : '';
  const again = () => { P.loadedFor = null; promptsView(view); };
  const render = () => {
    view.innerHTML = `${seg('pk', o.kinds.map(k => [k.kind, k.title]), P.kind, 'Какой промт')}
    <section class="card blk"><div class="card-h"><h2>${esc(st.title)}</h2><span class="hint">сейчас действует ${esc(st.source)}${st.version ? ' (сохранение #' + st.version + ')' : ''}</span></div>
      <p class="lead">Правки действуют и в боте.${st.hint ? ' ' + esc(st.hint) : ''}</p>
      ${P.history.map(h => `<div class="pm-qa"><div class="q">${esc(h.ask)}</div><div class="hint">${esc(h.reply)}</div></div>`).join('')}
      ${diffHtml(P.diff)}
      <textarea id="pm-ask" rows="2" maxlength="2000" placeholder="Что поменять, обычными словами: например, «пиши короче и добавь, сколько звонков у каждого менеджера»"></textarea>
      <div class="row" data-css="margin-top:10px"><button class="btn primary" type="button" data-edit>Поправить</button></div>
      <details class="pm-text"${P.open ? ' open' : ''}><summary>Текст промта${changed() ? ' · изменён' : ''}</summary>
        <textarea id="pm-body" rows="16" maxlength="20000">${esc(P.body)}</textarea></details>
      <div class="row">
        <button class="btn" type="button" data-prev${changed() ? '' : ' disabled'}>Пример по новому тексту</button>
        <button class="btn" type="button" data-undo${changed() ? '' : ' disabled'}>Отменить правки</button>
        <button class="btn primary" type="button" data-save="me"${canSave() ? '' : ' disabled'}>Сохранить для себя</button>
        <button class="btn primary" type="button" data-save="all"${canSave() ? '' : ' disabled'}>Сохранить для всех</button></div>
      ${changed() && !canSave() ? '<p class="hint">Сохранить можно после примера по этому тексту.</p>' : ''}
      ${P.preview ? `<div class="card report open" data-css="margin-top:12px">${P.preview.problem ? `<p><b>Пример не получился:</b> ${esc(P.preview.problem)}. Если сохранить так, при сбое бот пришлёт причину и стандартную версию. Лучше поправить текст ещё раз.</p>`
        : `<div class="rep-head"><b>${P.kind === 'review' ? 'Пример разбора последнего звонка по новому тексту' : 'Пример отчёта по новому тексту'}</b>${P.preview.call_id ? `<button class="btn small" type="button" data-review="${P.preview.call_id}">Как было</button>` : ''}</div><div class="rep-body">${P.preview.html}</div>`}</div>` : ''}
    </section>
    ${moreBlock('Сохранения', `${st.versions.length}`, `<p class="hint" data-css="margin:0">✅ действует для вас, 🌐 для всех. Если 🌐 нет ни у одного, для всех действует исходная.</p>
      ${st.personal ? '<div><button class="btn small" type="button" data-common>Убрать личную, получать общую</button></div>' : ''}
      <div class="pm-vers">${st.versions.map(v => `<div class="pm-ver"><div><b>${v.mine ? '✅ ' : ''}${v.common ? '🌐 ' : ''}#${v.id}</b> · ${fmtWhen(v.at)} · ${esc(v.scope)} · ${esc(v.who)}</div>
        ${v.note ? `<div class="hint">${esc(v.note)}</div>` : ''}
        <div class="row"><button class="btn small" type="button" data-vt="${v.id}">Текст</button><button class="btn small" type="button" data-use="${v.id}" data-scope="me">Вернуть для себя</button><button class="btn small" type="button" data-use="${v.id}" data-scope="all">Вернуть для всех</button></div></div>`).join('')}</div>`, 1)}`;
    bind();
  };
  const bind = () => {
    bindSeg(view, {pk: async k => {
      if (changed() && !await askConfirm('Несохранённые правки пропадут. Перейти?', 'Перейти')) { render(); return; }
      S.pm = {kind: k}; promptsView(view);
    }});
    const body = $('#pm-body', view);
    body.addEventListener('input', () => { P.body = body.value; P.open = true; });
    body.addEventListener('change', render);
    $('details.pm-text', view).addEventListener('toggle', e => { P.open = e.target.open; });
    $('[data-edit]', view).addEventListener('click', async e => {
      const ask = $('#pm-ask', view).value, btn = e.currentTarget;
      busy(btn, true);
      try {
        const r = await api(`/api/prompts/${P.kind}/edit`, {method: 'POST', body: {body: P.body, history: P.history, ask}});
        P.history.push({ask, reply: r.reply});
        P.diff = r.diff;
        if (r.prompt) { P.body = r.prompt; P.note = r.changes || ask; P.preview = null; }
        render();
      } catch (err) { busy(btn, false); toast(err.message, true); }
    });
    $('[data-undo]', view).addEventListener('click', () => { Object.assign(P, {body: P.start, diff: [], note: '', preview: null, token: null}); render(); });
    $('[data-prev]', view).addEventListener('click', async e => {
      const btn = e.currentTarget; busy(btn, true); btn.firstChild.textContent = 'Собираю пример, до минуты';
      const text = P.body;
      try {
        const r = await api(`/api/prompts/${P.kind}/preview`, {method: 'POST', body: {body: text}});
        Object.assign(P, {preview: r, token: r.token, tokenBody: text});
      } catch (err) { P.preview = {problem: err.message}; }
      render();
    });
    $$('[data-save]', view).forEach(b => b.addEventListener('click', async () => {
      if (b.dataset.save === 'all' && !await askConfirm(P.kind === 'review' ? 'Сохранить для всех? Так будут разбираться все звонки, в том числе менеджерам.' : 'Сохранить для всех?', 'Сохранить для всех')) return;
      busy(b, true);
      try {
        const r = await api(`/api/prompts/${P.kind}/save`, {method: 'POST', body: {body: P.body, note: P.note, scope: b.dataset.save, token: P.token}});
        toast(`Сохранено ${b.dataset.save === 'all' ? 'для всех' : 'для вас'}: сохранение #${r.version}.`);
        again();
      } catch (err) { busy(b, false); toast(err.message, true); }
    }));
    $('[data-common]', view)?.addEventListener('click', async () => {
      try { await api(`/api/prompts/${P.kind}/personal-off`, {method: 'POST', body: {}}); again(); } catch (err) { toast(err.message, true); }
    });
    $$('[data-vt]', view).forEach(b => b.addEventListener('click', async () => {
      try {
        const v = await api('/api/prompts/version/' + b.dataset.vt);
        modal(`${modalHead('Сохранение #' + v.id, esc(v.note || ''))}<div class="rvm-body"><div class="rvm-pre">${esc(v.body)}</div></div>`, true);
      } catch (err) { toast(err.message, true); }
    }));
    $$('[data-use]', view).forEach(b => b.addEventListener('click', async () => {
      const all = b.dataset.scope === 'all';
      if (!await askConfirm(`Вернуть сохранение #${b.dataset.use} ${all ? 'для всех' : 'для себя'}?`, 'Вернуть')) return;
      try { await api(`/api/prompts/version/${b.dataset.use}/use`, {method: 'POST', body: {scope: b.dataset.scope}}); toast('Готово'); again(); }
      catch (err) { toast(err.message, true); }
    }));
  };
  render();
}
