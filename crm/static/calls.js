// Звонки (журнал с фильтрами и подгрузкой) и Перезвоны (один список договорённостей).
'use strict';

const CALL_CHIP = {result: ['good', 'Результат'], hot: ['bad', 'Горячий, потерян'], lost: ['warn', 'Потерян, шанс был'],
  none: ['', 'Без шанса'], no_answer: ['', 'Не дозвонились']};
const isDir = () => S.me.role === 'director';

// Выбор менеджера в шапке: у директора все люди отдела, у менеджера ничего.
function managerSelect(val, allLabel = 'Все менеджеры') {
  if (!isDir() || S.me.team.length < 2) return '';
  return `<select class="inp" data-ext-pick aria-label="Менеджер"><option value="">${esc(allLabel)}</option>${S.me.team.map(p =>
    `<option value="${esc(p.ext)}"${p.ext === val ? ' selected' : ''}>${esc(p.name)}</option>`).join('')}</select>`;
}
function bindManager(scope, onChange) {
  $('[data-ext-pick]', scope)?.addEventListener('change', e => { S.f.ext = e.target.value; saveFilters(); onChange(); });
}

// ---------- журнал звонков ----------
S.calls = {result: '', kind: '', q: ''};

function callRow(c, r) {
  const [cls, label] = CALL_CHIP[c.result] || ['', ''];
  const chip = c.result === 'none' && !c.reviewed ? '<span class="chip">Без разбора</span>' : `<span class="chip ${cls}">${label}</span>`;
  return `<tr class="click" tabindex="0" data-id="${c.id}" data-css="--r:${r % 50}">
    <td class="nw">${esc(when(c.at))}<div class="s">${c.incoming ? 'входящий' : 'исходящий'}${c.kind ? ' · ' + esc(c.kind) : ''}</div></td>
    ${isDir() ? `<td class="hide-m nw">${esc(c.manager || '—')}</td>` : ''}
    <td><div class="num t">${esc(phone(c.number) || '—')}</div>${c.company ? `<div class="s">${esc(c.company)}</div>` : ''}</td>
    <td class="n hide-m">${c.seconds ? dur(c.seconds) : '—'}</td>
    <td class="wrap">${chip}${c.text ? ` <span class="s">${esc(c.text)}</span>` : ''}${c.line ? `<div class="s hide-m" data-css="margin-top:3px">${esc(c.line)}</div>` : ''}</td>
    <td class="nw">${!c.reviewed && c.rec ? `<button class="btn small" type="button" data-rec="${c.id}">▶ Запись</button>` : ''}</td></tr>`;
}

VIEWS.calls = async view => {
  const st = S.calls;
  let seq = 0, data = null, items = [], timer = null, obs = null;
  setHead({filters: seg('period', PERIOD_OPTS, S.f.period, 'Период') + managerSelect(S.f.ext) +
    `<label class="search">${svg('search', '')}<input class="inp" type="search" data-q placeholder="Номер или компания" value="${esc(st.q)}" aria-label="Поиск"></label>`});
  const top = $('#top');
  bindSeg(top, {period: v => { S.f.period = v; saveFilters(); load(); }});
  bindManager(top, () => load());
  $('[data-q]', top).addEventListener('input', e => { clearTimeout(timer); timer = setTimeout(() => { st.q = e.target.value.trim(); load(); }, 350); });

  const params = offset => qs({period: S.f.period, ext: isDir() ? S.f.ext : '', result: st.result, kind: st.kind, q: st.q, offset});
  async function load() {
    const my = ++seq;
    obs?.disconnect();
    try {
      data = await loading(view, api('/api/journal' + params(0)), 'table');
    } catch (e) { if (my === seq && view.isConnected) view.innerHTML = errCard(e.message); return; }
    if (my !== seq || !view.isConnected) return;
    items = data.items;
    render();
  }
  function render() {
    const results = [['', 'Все'], ...Object.entries(data.results)];
    const kinds = Object.entries(data.kinds);
    view.innerHTML = `<div class="page">
      <div class="row blk" data-css="--i:0"><div class="filters">${results.map(([k, t]) => `<button type="button" data-r="${k}" aria-pressed="${st.result === k}">${esc(t)}</button>`).join('')}</div>
        <span class="sp"></span><select class="inp" data-kind aria-label="Тип звонка"><option value="">Любой тип</option>${kinds.map(([k, t]) =>
          `<option value="${k}"${st.kind === k ? ' selected' : ''}>${esc(t[0].toUpperCase() + t.slice(1))}</option>`).join('')}</select></div>
      ${items.length ? `<section class="card blk" data-css="--i:1"><div class="tbl-wrap"><table class="tbl anim"><thead><tr><th>Когда</th>${isDir() ? '<th class="hide-m">Менеджер</th>' : ''}
        <th>Клиент</th><th class="n hide-m">Длит.</th><th>Итог</th><th></th></tr></thead><tbody>${items.map(callRow).join('')}</tbody></table></div>
        ${data.more ? '<div class="empty" data-more><div class="sk line" data-css="max-width:240px;margin:0 auto"></div></div>' : ''}</section>`
        : emptyCard(st.q || st.result || st.kind ? 'Таких звонков нет. Попробуйте другой фильтр.' : 'За этот период звонков нет.', 1)}</div>`;
    $$('[data-r]', view).forEach(b => b.addEventListener('click', () => { st.result = b.dataset.r; load(); }));
    $('[data-kind]', view).addEventListener('change', e => { st.kind = e.target.value; load(); });
    bindRows($('tbody', view));
    watchMore();
  }
  function bindRows(tbody) {
    if (!tbody || tbody.dataset.bound) return;
    tbody.dataset.bound = '1';
    const open = tr => {
      const c = items.find(x => String(x.id) === tr.dataset.id);
      if (!c) return;
      if (c.reviewed && window.CRMReview) CRMReview.open(c.id);
      else if (c.number) workDrawer({kind: 'call', number: c.number, title: c.company}, {ext: c.ext});
    };
    tbody.addEventListener('click', e => { if (e.target.closest('button,audio')) return; const tr = e.target.closest('tr[data-id]'); if (tr) open(tr); });
    tbody.addEventListener('keydown', e => { const tr = e.target.closest('tr[data-id]'); if (tr && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); open(tr); } });
  }
  // Следующие 50 подгружаются, когда список докручен до конца.
  function watchMore() {
    const more = $('[data-more]', view);
    if (!more || !('IntersectionObserver' in window)) return;
    let busyMore = false;
    obs = new IntersectionObserver(async ent => {
      if (!ent[0].isIntersecting || busyMore) return;
      busyMore = true;
      const my = seq;
      try {
        const next = await api('/api/journal' + params(data.offset));
        if (my !== seq || !view.isConnected) return;
        const start = items.length;
        items = items.concat(next.items);
        data.offset = next.offset; data.more = next.more;
        $('tbody', view).insertAdjacentHTML('beforeend', next.items.map((c, k) => callRow(c, start + k)).join(''));
        if (!next.more) { obs.disconnect(); more.remove(); }
      } catch (e) { more.innerHTML = `<span class="hint">${esc(e.message)}</span>`; obs.disconnect(); }
      busyMore = false;
    }, {rootMargin: '300px'});
    obs.observe(more);
  }
  await load();
};

// ---------- перезвоны ----------
function cbRow(t, today) {
  const late = t.due_date < today;
  const days = late ? Math.round((new Date(today) - new Date(t.due_date)) / 864e5) : 0;
  const ro = S.me.readonly;
  return `<div class="cb blk" data-id="${t.id}" data-css="--i:${Math.min(t._i, 12)}">
    <div class="when"><b>${esc(t.due_date === today ? 'сегодня' : t.due_date === addDays(today, 1) ? 'завтра' : ddmm(t.due_date))}${t.due_time ? ' ' + esc(t.due_time) : ''}</b>
      <span>${late ? `просрочен ${days} ${plural(days, 'день', 'дня', 'дней')}` : t.estimate ? 'дата примерная' : esc(dayLong(t.due_date).split(',')[0])}</span></div>
    <div class="what"><div class="t"><span class="num">${esc(phone(t.number) || '—')}</span>${t.title ? `<span>${esc(t.title)}</span>` : ''}
      <span class="chip ${t.label === 'Встреча' ? 'acc' : ''}">${esc(t.label)}</span></div>
      <div class="s">${[isDir() && t.manager ? esc(t.manager) : '', t.source === 'crm' ? 'отметка в CRM' : 'из разговора', t.note ? '«' + esc(t.note) + '»' : '',
        t.more ? `ещё ${t.more} ${plural(t.more, 'договорённость', 'договорённости', 'договорённостей')} по номеру` : ''].filter(Boolean).join(' · ')}</div></div>
    <div class="acts"><button class="btn small primary" type="button" data-call>${svg('phone', '')}Позвонить</button>
      ${ro ? '' : '<button class="btn small" type="button" data-move>Перенести</button><button class="btn small" type="button" data-done>Выполнено</button>'}</div></div>`;
}

VIEWS.callbacks = async view => {
  setHead({filters: managerSelect(S.f.ext)});
  bindManager($('#top'), () => reload());
  const data = await loading(view, api('/api/callbacks' + qs({ext: isDir() ? S.f.ext : ''})), 'cards');
  const today = data.today;
  S.today = today || S.today;
  const items = data.items;
  const dueNow = () => items.filter(t => t.due_date <= today).length;
  if (!isDir() || !S.f.ext) setCount('callbacks', dueNow());
  if (!items.length) {
    view.innerHTML = `<div class="page">${emptyCard('<b>Открытых перезвонов нет.</b><br>Они появляются сами: бот ставит их из разговоров, а менеджер — из итога звонка в CRM.')}</div>`;
    return;
  }
  const groups = [['late', 'Просрочено', t => t.due_date < today], ['today', 'Сегодня', t => t.due_date === today], ['next', 'Дальше', t => t.due_date > today]];
  let n = 0;
  view.innerHTML = `<div class="page"><section class="card blk">${groups.map(([k, name, test]) => {
    const list = items.filter(test);
    return list.length ? `<div class="group${k === 'late' ? ' late' : ''}" data-g="${k}">${name}<span class="count">${list.length}</span></div>
      <div class="list">${list.map(t => cbRow({...t, _i: n++}, today)).join('')}</div>` : '';
  }).join('')}</section></div>`;
  const byId = Object.fromEntries(items.map(t => [String(t.id), t]));
  const change = (t, body) => api(`/api/tasks/${t.id}`, {method: 'POST', body: {...body, ext: isDir() ? t.ext : undefined}});
  // Строка уезжает, счётчики группы и меню обновляются без перезагрузки.
  const drop = row => {
    const t = byId[row.dataset.id];
    items.splice(items.indexOf(t), 1);
    row.classList.add('gone');
    setTimeout(() => {
      const list = row.parentElement; row.remove();
      const g = list.previousElementSibling;
      if (!list.children.length) { g.remove(); list.remove(); } else $('.count', g).textContent = list.children.length;
      if (!items.length) reload();
    }, 380);
    if (!isDir() || !S.f.ext) setCount('callbacks', dueNow());
  };
  view.addEventListener('click', async e => {
    const row = e.target.closest('.cb'); if (!row) return;
    const t = byId[row.dataset.id];
    if (e.target.closest('[data-call]')) { workDrawer(t, {ext: t.ext, onDone: () => reload()}); return; }
    const done = e.target.closest('[data-done]');
    if (done) {
      busy(done, true);
      try { await change(t, {action: 'done'}); doneBtn(done, 'Выполнено'); setTimeout(() => drop(row), 450); }
      catch (err) { busy(done, false); toast(err.message, true); }
      return;
    }
    if (e.target.closest('[data-move]')) {
      if ($('.move', row)) { $('.move', row).remove(); return; }
      row.insertAdjacentHTML('beforeend', `<div class="move"><input class="inp" type="date" min="${today}" value="${nextWorkday(today)}" aria-label="Новая дата">
        <input class="inp" type="time" step="300" aria-label="Время" value="${esc(t.due_time || '')}"><button class="btn small primary" type="button" data-save>Сохранить</button>
        <span class="sp"></span><button class="btn small danger ghost" type="button" data-cancel>Отменить перезвон</button></div>`);
      $('.move input', row).focus();
      return;
    }
    const save = e.target.closest('[data-save]');
    if (save) {
      const [d, tm] = $$('.move input', row).map(x => x.value);
      busy(save, true);
      try { await change(t, {action: 'move', due_date: d, due_time: tm || null}); toast(`Перенесён на ${ddmm(d)}${tm ? ' ' + tm : ''}`); reload(); }
      catch (err) { busy(save, false); toast(err.message, true); }
      return;
    }
    if (e.target.closest('[data-cancel]')) {
      if (!await askConfirm('Отменить перезвон?', 'Отменить перезвон', true)) return;
      try { await change(t, {action: 'cancelled'}); toast('Перезвон отменён'); drop(row); } catch (err) { toast(err.message, true); }
    }
  });
};
