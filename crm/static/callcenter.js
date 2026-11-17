// Колл-центр (директор): все клиенты обзвона из таблицы «Прозвон лидов» одним экраном.
// Кабинет клиента: то же по одному клиенту, так, как его видит сам клиент.
'use strict';

const LEAD_RESULT = [['interested', 'Заинтересованы', 'var(--good)'], ['callback', 'Перезвон', 'var(--heat)'], ['not_relevant', 'Неактуально', 'var(--bad)'],
  ['no_answer', 'Не дозвонились', 'var(--muted)'], ['other', 'Другое', 'var(--s1)'], ['not_called', 'Ещё не звонили', 'var(--line)']];
const yearDate = iso => iso ? `${ddmm(iso)}.${iso.slice(0, 4)}` : '';

function daysChart(box, days) {
  const rows = days.map(d => ({...d, x: ddmm(d.day), label: dayLong(d.day), calls: (d.first || 0) + (d.repeat || 0)}));
  drawChart(box, rows, {
    bands: [{key: 'first', label: 'Первичные звонки', color: 'var(--s1)'}, {key: 'repeat', label: 'Повторные после недозвона', color: 'var(--s2)'}],
    tipRows: d => [['Первичных', 'var(--s1)', fmt(d.first)], ['Повторных', 'var(--s2)', fmt(d.repeat)], ['Дозвонились', '', fmt(d.reached)], ['Заинтересованы', '', fmt(d.interested)]],
    aria: 'Звонки колл-центра по дням',
  });
}
// «Повторные звонки подняли дозвон с X до Y»: доля дозвона с первой попытки и итоговая.
function repeatInsight(reachedFirst, reached, called) {
  if (!called || reached <= reachedFirst) return '';
  return `<p class="insight">Повторные звонки подняли дозвон с <b>${pctOf(reachedFirst, called)}</b> до <b>${pctOf(reached, called)}</b>: ещё ${plainItems(reached - reachedFirst, 'человек взял', 'человека взяли', 'человек взяли')} трубку со второй или третьей попытки.</p>`;
}
// Сумма, которую нельзя посчитать: показываем прочерк с пояснением, а не выдуманное число.
const rubOrDash = v => v == null ? '<span class="muted">—</span>' : rub(v);
const kpiDash = (k, i) => `<div class="kpi blk" data-css="--i:${i}"><span class="lab">${esc(k.label)}${k.help ? ' ' + help(k.help) : ''}</span>
  <span class="val" data-kind="n">—</span>${k.sub ? `<span class="foot">${k.sub}</span>` : ''}</div>`;
// Сверка звонков Mango с листом: что связано, а что осталось без подтверждения.
const MATCH_KINDS = [['ambiguous', 'Неоднозначно'], ['repeated', 'Повторные сверх попыток'], ['unmatched', 'Звонки без лида'],
  ['missing_phone', 'Без телефона'], ['missing_date', 'Без даты'], ['campaign_mismatch', 'Другая кампания']];
function matchSamples(m) {
  const rows = [];
  for (const [kind, label] of MATCH_KINDS) {
    (m.samples?.[kind] || []).forEach(s => rows.push(`<tr><td class="nw">${esc(s.phone || '—')}</td><td class="nw">${s.date ? ddmm(s.date) : '—'}</td>
      <td>${esc(s.extension || '—')}</td><td>${esc(s.campaign || '—')}</td><td class="wrap">${esc(label)}${s.note ? ': ' + esc(s.note) : ''}</td></tr>`));
  }
  return rows.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Телефон</th><th>Дата</th><th>Оператор</th><th>Лист/кампания</th><th>Что не подтвердилось</th></tr></thead><tbody>${rows.join('')}</tbody></table></div>` : '';
}
function matchNote(m) {
  if (!m || !m.available) return m?.reason ? esc(m.reason) : '';
  const parts = [];
  parts.push(`связано ${plainItems(m.linked, 'попытка', 'попытки', 'попыток')}`);
  if (m.ambiguous) parts.push(`неоднозначно ${plainItems(m.ambiguous, 'звонок', 'звонка', 'звонков')}`);
  if (m.repeated) parts.push(`повторных сверх попыток ${m.repeated}`);
  if (m.unmatched) parts.push(`без лида ${m.unmatched}`);
  if (m.campaign_mismatch) parts.push(`другая кампания ${m.campaign_mismatch}`);
  if (m.missing.phone) parts.push(`без телефона ${m.missing.phone}`);
  if (m.missing.date) parts.push(`без даты ${m.missing.date}`);
  if (m.attempts_without_call) parts.push(`попыток без звонка ${m.attempts_without_call}`);
  return `Сверка с Mango: ${parts.join(', ')}.`;
}
const leadRows = (list, withClient, withRemind) => `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Телефон</th>${withClient ? '<th class="hide-m">Клиент</th>' : ''}<th>Звонок</th>
  ${withRemind ? '<th>Напоминание</th>' : ''}<th class="hide-m">Источник</th><th>Комментарий оператора</th></tr></thead><tbody>
  ${list.map(l => `<tr><td class="num nw">${esc(phone(l.phone) || l.phone)}</td>${withClient ? `<td class="hide-m">${esc(l.client)}</td>` : ''}
    <td class="nw">${l.last ? ddmm(l.last) : ''}</td>${withRemind ? `<td>${esc(l.remind || '')}</td>` : ''}<td class="hide-m">${esc(l.source)}</td><td class="wrap">${esc(l.comment)}</td></tr>`).join('')}
  </tbody></table></div>`;
// Длинный список: первые строки сразу, остальные под кнопкой.
function longList(list, render, first = 10) {
  if (list.length <= first + 2) return render(list);
  return render(list.slice(0, first)) + `<details class="inline" data-css="margin-top:10px"><summary>Показать ещё ${list.length - first}</summary>${render(list.slice(first))}</details>`;
}

function ccHighlightCard(id, item, start, end) {
  const name = {olga: 'Ольга', oksana: 'Оксана'}[id] || id;
  const dates = /^\d{4}-\d{2}-\d{2}$/.test(start || '') && /^\d{4}-\d{2}-\d{2}$/.test(end || '');
  const route = `:analysis:operator:${id}${dates ? ':' + start + ':' + end : ''}`;
  const review = item?.status === 'ready' ? item.brief?.operator_review : null;
  const script = review?.script, base = review?.base;
  const fact = base?.contact_fact?.status === 'supported' ? base.contact_fact : null;
  const show = part => part?.status === 'supported' && part.observation;
  const status = item?.status === 'error' ? 'Не удалось подготовить вывод. Откройте полный разбор, чтобы проверить состояние.'
    : item?.status === 'stale' ? 'Обновляем выводы по новым разговорам.'
    : item?.status === 'running' ? 'Анализируем разговоры.' : 'Готовим выводы.';
  return `<article class="cc-highlight-card"><div class="cc-highlight-name">${esc(name)}</div>
    ${review ? `<div class="cc-highlight-row"><h3>Работа по скрипту</h3><p>${esc(show(script) ? script.observation : script?.observation || 'Недостаточно подтверждённых данных.')}</p>
      ${show(script) && script.action ? `<p class="cc-highlight-action"><b>Что исправить:</b> ${esc(script.action)}</p>` : ''}</div>
      <div class="cc-highlight-row"><h3>Качество базы</h3><p>${esc(base?.observation || 'Недостаточно подтверждённых данных.')}</p>
      ${show(base) && base.action ? `<p class="cc-highlight-action"><b>Что сделать:</b> ${esc(base.action)}</p>` : ''}
      ${fact ? `<div class="cc-contact-fact"><b>Факт из разговора:</b> ${esc(fact.observation || '')}${fact.action ? `<br><b>Что проверить:</b> ${esc(fact.action)}` : ''}</div>` : ''}</div>`
      : `<p class="cc-highlight-wait">${esc(item?.status === 'ready' ? 'Недостаточно подтверждённых данных для персонального вывода.' : status)}</p>`}
    <a class="cc-highlight-link" href="#callcenter/${esc(route)}" data-go="callcenter" data-arg="${esc(route)}">Полный разбор →</a></article>`;
}
function ccHighlights(data, selected) {
  const people = selected === 'olga' || selected === 'oksana' ? [selected] : selected ? [] : ['olga', 'oksana'];
  if (!people.length) return '';
  const dates = data?.start && data?.end ? `${esc(data.start)} — ${esc(data.end)}` : '';
  return `<section class="cc-highlights" aria-label="Выводы по операторам"><div class="cc-highlights-head"><div><h2>Выводы по операторам</h2>
    <p>Автоматический разбор · последняя неделя${dates ? ' · ' + dates : ''}</p></div><span>Период выводов не зависит от фильтра статистики</span></div>
    <div class="cc-highlights-grid">${people.map(id => ccHighlightCard(id, data?.operators?.[id], data?.start, data?.end)).join('')}</div></section>`;
}

VIEWS.callcenter = async (view, arg) => {
  if (S.me.role === 'manager' || arg === ':work') return ccWork(view);
  if (arg === ':setup') return ccSetup(view);
  if (arg === ':analysis' || arg?.startsWith(':analysis:')) return callAnalysisView(view, arg);
  if (arg) return cabinetView(view, arg);
  const db = S.me.callcenter_mode === 'db';
  const actions = [];
  if (db) actions.push('<button class="btn primary" type="button" data-go="callcenter" data-arg=":work">Прозвон</button>');
  if (db && !S.me.readonly) actions.push('<button class="btn" type="button" data-go="callcenter" data-arg=":setup">Клиенты и номера</button>');
  if (S.me.role === 'director' && !S.me.readonly) {
    actions.push('<button class="btn" type="button" data-go="callcenter" data-arg=":analysis:findings">Выводы агента</button>');
    actions.push('<button class="btn" type="button" data-go="callcenter" data-arg=":analysis:stats">Разобрать звонки</button>');
  }
  // Период и сотрудник — как в «Сводке». Фильтры показываем только тем, кому разрешил сервер.
  const canFilter = !!S.me.callcenter_filters;
  S.cc = S.cc || {period: '', operator: ''};
  const canHighlights = S.me.role === 'director' && S.me.privileged && !S.me.test && !S.me.support;
  let seq = 0, highlightSeq = 0, data = null;
  view.innerHTML = '<div class="page"><div data-cc-highlights></div><div data-cc-stats></div></div>';
  const highlightsBox = $('[data-cc-highlights]', view), statsBox = $('[data-cc-stats]', view);
  const head = () => {
    const f = data?.filters || {};
    const ops = (f.operators || []).filter(o => o.available);
    const others = ops.filter(o => !['olga', 'oksana'].includes(o.id));
    const who = canHighlights ? seg('ccop', [['', 'Весь отдел'], ['olga', 'Ольга'], ['oksana', 'Оксана'], ...others.map(o => [o.id, shortName(o.name)])], S.cc.operator, 'Сотрудник')
      : f.operator_available && ops.length ? seg('ccop', [['', 'Весь отдел'], ...ops.map(o => [o.id, shortName(o.name)])], S.cc.operator, 'Сотрудник') : '';
    setHead({actions: actions.join(''), filters: canFilter
      ? seg('period', [['', 'Всё время'], ...PERIOD_OPTS], S.cc.period, 'Период') + who : ''});
  };
  const bind = () => bindSeg($('#top'), {
    period: v => { S.cc.period = v; draw(); },
    ccop: v => { S.cc.operator = v; draw(); },
  });

  async function loadHighlights(selected) {
    const current = ++highlightSeq;
    clearTimeout(S.ccHighlightTimer);
    if (!canHighlights) { highlightsBox.innerHTML = ''; return; }
    highlightsBox.innerHTML = ccHighlights(null, selected);
    try {
      const result = await api('/api/callcenter/analysis/operator-highlights');
      if (!view.isConnected || current !== highlightSeq) return;
      highlightsBox.innerHTML = ccHighlights(result, selected);
      if (Object.values(result.operators || {}).some(x => ['queued', 'running', 'stale', 'waiting'].includes(x.status)))
        S.ccHighlightTimer = setTimeout(() => { if (view.isConnected && current === highlightSeq) loadHighlights(selected); }, 30000);
    } catch (e) {
      if (!view.isConnected || current !== highlightSeq) return;
      highlightsBox.innerHTML = '<section class="cc-highlights"><h2>Выводы по операторам</h2><p class="hint">Выводы сейчас недоступны. Попробуйте обновить страницу позже.</p></section>';
    }
  }
  async function draw() {
    const my = ++seq;
    head(); bind();
    loadHighlights(canFilter ? S.cc.operator : '');
    statsBox.innerHTML = '<div class="empty">Загружаем статистику…</div>';
    const selectedState = (data?.filters?.operators || []).find(o => o.id === S.cc.operator);
    if (canFilter && S.cc.operator && selectedState && !selectedState.available) {
      const who = {olga: 'Ольге', oksana: 'Оксане'}[S.cc.operator] || 'выбранному сотруднику';
      statsBox.innerHTML = `<div class="empty">В таблице нет подтверждённых данных по ${esc(who)}.</div>`;
      return;
    }
    let d;
    try {
      d = await api('/api/callcenter' + qs(canFilter ? {period: S.cc.period, operator: S.cc.operator} : {}));
    } catch (e) {
      if (my === seq && view.isConnected) statsBox.innerHTML = `<div class="empty">Не удалось загрузить статистику: ${esc(e.message)}</div>`;
      return;
    }
    if (my !== seq || !view.isConnected) return;
    data = d;
    const f = d.filters || {};
    head(); bind();
    if (canFilter && S.cc.operator && f.operator_available === false) {
      const who = {olga: 'Ольге', oksana: 'Оксане'}[S.cc.operator] || 'выбранному сотруднику';
      statsBox.innerHTML = `<div class="empty">В таблице нет подтверждённых данных по ${esc(who)}.</div>`;
      return;
    }
    const t = d.total;
    let i = 0;
    const filtering = !!(S.cc.period || S.cc.operator);
    const rob = f.robot || {};
    // Что источник не хранит — так и пишем: прочерк и причина лучше выдуманного числа.
    const notes = [];
    if (f.operator_sheet) notes.push(`Источник статистики — таблица «${esc(f.operator_sheet)}».`);
    if (canFilter && f.operator_available === false) notes.push(esc(f.operator_reason || 'Разрез по сотрудникам недоступен.'));
    const off = (f.operators || []).filter(o => !o.available && (o.kind !== 'robot' || filtering));
    if (canFilter && f.operator_available && off.length) notes.push('Разрез недоступен: ' + off.map(o => `<b>${esc(o.name)}</b> — ${esc(o.reason)}`).join('; '));
    if (canFilter && f.operator_available && f.unattributed) notes.push(`Без подтверждённого оператора: ${plainItems(f.unattributed, 'попытка', 'попытки', 'попыток')} — они входят только в «Весь отдел».`);
    if (canFilter && f.undated) notes.push(`Без даты звонка: ${plainItems(f.undated, 'попытка', 'попытки', 'попыток')} — в выбранный период они не попали.`);
    if (canFilter && filtering && f.matching) notes.push(matchNote(f.matching));
    if (canFilter && filtering && f.matching?.truncated) notes.push('Сверка ограничена последними звонками журнала Mango: выберите период, чтобы «без звонка» считалось по всему срезу.');
    if (canFilter && S.cc.operator === 'robot' && rob.available) notes.push('Звонки робота не входят в попытки листа: они показаны отдельно из вкладки «Робот» и не засчитываются лидами листа.');
    const ms = f.mango_stats;
    const mangoKpis = ms ? [
      {key: 'cc-mango', label: 'Звонков Mango', value: ms.calls, sub: 'Исходящие, включая повторные'},
      {key: 'cc-mango-contacted', label: 'Обзвонено номеров', value: ms.contacted, sub: 'Уникальные номера в Mango'},
      {key: 'cc-mango-connected', label: 'Дозвон Mango', value: ms.contacted ? ms.connected / ms.contacted * 100 : 0,
       kind: 'pct', sub: `${fmt(ms.connected)} номеров с ненулевой длительностью; возможны автоответчики`},
      ...(f.crm_marked_leads != null ? [{key: 'cc-crm-leads', label: 'Лидов отмечено в CRM', value: f.crm_marked_leads,
        sub: 'Уникальные номера, отмеченные сотрудником за период'}] : []),
    ] : [];
    const kpis = [
      ...mangoKpis,
      {key: 'cc-called', label: ms ? 'Лидов обзвонено (таблица)' : 'Лидов обзвонено', value: t.called || 0, sub: `из ${fmt(t.total)} переданных`},
      {key: 'cc-rate', label: ms ? 'Дозвон (таблица)' : 'Дозвон', value: t.called ? t.reached / t.called * 100 : 0, kind: 'pct', sub: `${fmt(t.reached)} взяли трубку`},
      {key: 'cc-int', label: ms ? 'Заинтересованных (таблица)' : 'Заинтересованных', value: t.interested || 0}];
    // Робот — из своей вкладки: попытки и лиды считаются по разным определениям и не смешиваются с листом.
    const robotCard = (canFilter && filtering && rob.source) ? `<section class="card blk" data-css="--i:${i++}">
      <div class="card-h"><h2>Робот</h2><span class="hint">${esc(rob.source)}${rob.cabinet ? ' · ' + esc(rob.cabinet) : ''}</span></div>
      ${rob.available ? `<div class="tbl-wrap"><table class="tbl"><tbody>
        <tr><td>Попытки робота — звонки за ${esc(f.period_label || 'всё время')}</td><td class="n">${fmt(rob.attempts)}</td></tr>
        <tr><td>Лиды — подтверждённые отметки CRM по этим номерам</td><td class="n">${fmt(rob.leads)}</td></tr>
        <tr><td>Номеров без подтверждённого лида</td><td class="n">${fmt(rob.phones_without_lead)}</td></tr>
        ${(rob.outcomes || []).map(o => `<tr><td class="hint">Итог робота: ${esc(o.label)}</td><td class="n">${fmt(o.count)}</td></tr>`).join('')}
      </tbody></table></div><p class="hint">${esc(rob.note || '')}</p>`
        : `<div class="empty">${esc(rob.reason || 'Данных робота за период нет.')}</div>`}</section>` : '';
    const matchBlock = canFilter && filtering && f.matching?.available ? `<h3>Сверка звонков с листом</h3>
      <p class="hint">${esc(matchNote(f.matching))} Связь ставится только при совпадении телефона, дня звонка и кампании; телефоны в примерах замаскированы.</p>
      ${matchSamples(f.matching)}` : '';
    statsBox.innerHTML = `<div>
      ${notes.filter(Boolean).map(n => `<p class="hint blk" data-css="--i:${i++}">${n}</p>`).join('')}
      <div class="kpis">${kpis.map(k => kpiHtml(k, i++)).join('')}</div>
      ${robotCard}
      ${repeatInsight(t.reached_first || 0, t.reached || 0, t.called || 0).replace('class="insight"', `class="insight blk" data-css="--i:${i++}"`)}
      <details class="cc-client-chooser"><summary>Кабинеты клиентов (${fmt(d.clients.length)})</summary>
        ${d.clients.length ? `<div class="cc-client-list">${d.clients.map(c => `<button class="btn" type="button" data-go="callcenter" data-arg="${esc(c.name)}">${esc(c.name)}</button>`).join('')}</div>`
          : `<p class="hint">${db ? 'Клиентов пока нет: добавьте их в «Клиенты и номера».' : 'В таблице колл-центра пока нет клиентов.'}</p>`}</details>
      ${d.days.length > 1 ? `<section class="card blk" data-css="--i:${i++}"><div class="card-h"><h2>Звонки по дням</h2><span class="hint">последние ${d.days.length} ${plural(d.days.length, 'день', 'дня', 'дней')} со звонками</span></div><div class="chart" data-chart></div></section>` : ''}
      <section class="card blk" data-css="--i:${i++}"><div class="card-h"><h2>${db ? 'Кому перезвонить' : 'Кому Ольге перезвонить'}</h2><span class="hint">${fmt(d.callbacks.length)}</span></div>
        ${d.callbacks.length ? longList(d.callbacks, l => leadRows(l, true, true)) : '<div class="empty">Перезвонов нет.</div>'}</section>
      ${moreBlock('Подробнее', 'по базам, источникам и сверке', `
        ${matchBlock}
        <h3>По базам</h3>${d.bases.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Клиент</th><th>База от</th><th class="n">Номеров</th><th class="n">Обзвонено</th><th class="n">Дозвон</th><th class="n">Заинтерес.</th></tr></thead><tbody>
          ${d.bases.map(b => `<tr><td>${esc(b.client)}</td><td class="nw">${yearDate(b.date)}</td><td class="n">${fmt(b.total)}</td><td class="n">${fmt(b.called)}</td><td class="n">${pctOf(b.reached || 0, b.called || 0)}</td><td class="n">${fmt(b.interested)}</td></tr>`).join('')}</tbody></table></div>` : '<p class="hint">Дат баз в таблице нет.</p>'}
        <h3>Источники</h3>${d.sources.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Клиент</th><th>Источник</th><th class="n">Номеров</th><th class="n">Дозвон</th><th class="n">Заинтерес.</th></tr></thead><tbody>
          ${d.sources.map(s => `<tr><td>${esc(s.client)}</td><td>${esc(s.name || 'без тега')}</td><td class="n">${fmt(s.total)}</td><td class="n">${pctOf(s.reached, s.total)}</td><td class="n">${fmt(s.interested)}</td></tr>`).join('')}</tbody></table></div>` : '<p class="hint">Источников нет.</p>'}`, i++)}
    </div>`;
    if ($('[data-chart]', view)) daysChart($('[data-chart]', view), d.days);
    countUp(view);
  }
  await draw();
};

// Кабинет клиента: у клиента — свой, у директора — любой из колл-центра.
async function cabinetView(view, name) {
  const dir = S.me.role === 'director';
  const operator = dir && S.me.callcenter_filters && S.cc?.operator === 'oksana' ? 'oksana' : '';
  setHead({title: dir ? name : 'Кабинет', back: dir ? () => go('callcenter') : null,
    actions: dir && !S.me.readonly && !operator ? '<button class="btn" type="button" data-cl-link>Ссылка для клиента</button>' : ''});
  $('[data-cl-link]', $('#top'))?.addEventListener('click', e => clientLink(e.currentTarget, name));
  const c = await loading(view, api('/api/cabinet' + qs({client: dir ? name : '', operator})), 'page');
  if (!dir) setHead({title: c.name || 'Кабинет', sub: c.since ? `данные с ${esc(yearDate(c.since))}` : ''});
  else $('#top .sub-t')?.remove();
  const k = c.counts, s = c.summary;
  const tariff = Object.fromEntries((c.tariffs || []).map(x => [x.name, x.price]));
  const parts = LEAD_RESULT.filter(([key]) => k[key]);
  const days = [...c.days].reverse().slice(-45);
  let i = 0;
  view.innerHTML = `<div class="page">
    <div class="kpis">${[
      {key: 'cb-total', label: 'Номеров передано', value: k.total},
      {key: 'cb-called', label: 'Обзвонено', value: k.called, sub: `${pctOf(k.called, k.total)} базы`},
      {key: 'cb-reached', label: 'Дозвонились', value: k.reached, sub: `${pctOf(k.reached, k.called)} обзвоненных`},
      {key: 'cb-int', label: 'Заинтересованных', value: k.interested}].map(x => kpiHtml(x, i++)).join('')}</div>
    <section class="card blk" data-css="--i:${i++}"><div class="card-h"><h2>Итог по номерам</h2><span class="hint">${fmt(k.total)}</span></div>
      <div class="split" role="img" aria-label="Итог по номерам">${parts.map(([key, , color]) => `<i data-css="width:${k[key] / k.total * 100}%;background:${color}"></i>`).join('')}</div>
      <div class="legend" data-css="margin-top:12px">${parts.map(([key, label, color]) => `<span><i data-css="background:${color}"></i>${esc(label)} <b>${fmt(k[key])}</b> <span class="hint">${pctOf(k[key], k.total)}</span></span>`).join('')}</div>
      ${repeatInsight(c.reached_first || 0, k.reached, k.called).replace('class="insight"', 'class="insight" data-css="margin-top:12px"')}</section>
    <div class="grid2 even">
      <section class="card blk" data-css="--i:${i++}"><div class="card-h"><h2>Заинтересованные</h2><span class="hint">${fmt(c.interested.length)}</span></div>
        ${c.interested.length ? longList(c.interested, l => leadRows(l, false, false), 8) : '<div class="empty">Пока нет.</div>'}</section>
      <section class="card blk" data-css="--i:${i++}"><div class="card-h"><h2>Перезвоны</h2><span class="hint">${fmt(c.callbacks.length)}</span></div>
        ${c.callbacks.length ? longList(c.callbacks, l => leadRows(l, false, true), 8) : '<div class="empty">Перезвонов сейчас нет.</div>'}</section></div>
    ${days.length > 1 ? `<section class="card blk" data-css="--i:${i++}"><div class="card-h"><h2>Звонки по дням</h2></div><div class="chart" data-chart></div></section>` : ''}
    ${moreBlock('Подробнее', 'расчёт и базы', `
      <h3>Расчёт</h3><table class="tbl"><tbody>
        <tr><td>Первичных звонков</td><td class="n">${fmt(s.first)}</td><td class="n hint">${tariff['Первичный звонок'] != null ? '× ' + rub(tariff['Первичный звонок']) : ''}</td></tr>
        <tr><td>Повторных звонков</td><td class="n">${fmt(s.repeat)}</td><td class="n hint">${tariff['Повторный звонок (после НДЗ)'] != null ? '× ' + rub(tariff['Повторный звонок (после НДЗ)']) : ''}</td></tr>
        <tr><td>Заинтересованных</td><td class="n">${fmt(s.interested)}</td><td class="n hint">${tariff['Звонок с результатом «Заинтересованный»'] != null ? '× ' + rub(tariff['Звонок с результатом «Заинтересованный»']) : ''}</td></tr>
        <tr><td><b>Итого к оплате</b></td><td></td><td class="n"><b>${rub(s.sum)}</b></td></tr></tbody></table>
      ${c.bases.some(b => b.date) ? `<h3>По базам</h3><div class="tbl-wrap"><table class="tbl"><thead><tr><th>База от</th><th class="n">Номеров</th><th class="n">Обзвонено</th><th class="n">Дозвон</th><th class="n">Заинтерес.</th></tr></thead><tbody>
        ${c.bases.filter(b => b.date).map(b => `<tr><td>${yearDate(b.date)}</td><td class="n">${fmt(b.total)}</td><td class="n">${fmt(b.called || 0)}</td><td class="n">${pctOf(b.reached || 0, b.called || 0)}</td><td class="n">${fmt(b.interested || 0)}</td></tr>`).join('')}
      </tbody></table></div>` : ''}`, i++)}
  </div>`;
  if ($('[data-chart]', view)) daysChart($('[data-chart]', view), days);
  countUp(view);
}
VIEWS.cab = async view => cabinetView(view, null);

async function clientLink(btn, name) {
  busy(btn, true);
  try {
    const r = await api('/api/access', {method: 'POST', body: {role: 'client', client: name}});
    const box = modal(`${modalHead('Ссылка для клиента', esc(name))}<div class="rvm-body"><div data-out></div></div>`);
    linkBox($('[data-out]', box), r.link, 'По ссылке клиент входит в свой кабинет без пароля. Ссылку видно только сейчас: скопируйте и отправьте. Отозвать можно в «Настройки → Доступы».');
  } catch (e) { toast(e.message, true); }
  busy(btn, false);
}

// ---------- колл-центр в базе (компания без Google Таблицы) ----------
const CC_BUTTONS = [['no_answer', 'Не взял трубку', ''], ['callback', 'Перезвонить', ''], ['interested', 'Заинтересован', 'primary'], ['not_relevant', 'Неактуально', '']];

// Прозвон: кому звонить сейчас. Сначала перезвоны, потом новые номера, потом недозвоны прошлых дней.
async function ccWork(view) {
  const dir = S.me.role === 'director';
  setHead({title: 'Прозвон', back: dir ? () => go('callcenter') : null});
  S.ccClient = S.ccClient || '';
  const d = await loading(view, api('/api/callcenter/work' + qs({client: S.ccClient})), 'page');
  const ro = S.me.readonly;
  view.innerHTML = `<div class="page">
    <section class="card blk"><div class="card-h"><h2>Кому звонить</h2><span class="hint">${fmt(d.total)}</span><span class="sp"></span>
      ${d.clients.length > 1 ? `<select data-cc-client aria-label="Клиент"><option value="">Все клиенты</option>${d.clients.map(c => `<option${c === S.ccClient ? ' selected' : ''}>${esc(c)}</option>`).join('')}</select>` : ''}</div>
      ${d.items.length ? `<div class="cc-list">${d.items.map(l => `<div class="cc-lead" data-id="${l.id}">
        <div class="row"><a class="num" href="tel:+${esc(l.phone)}">${esc(phone(l.phone) || l.phone)}</a><span class="chip">${esc(l.client)}</span>
          ${l.attempts.map(a => `<span class="chip ${a === 'Перезвон' ? 'warn' : ''}">${esc(a)}</span>`).join('')}${l.remind ? `<span class="hint">${esc(l.remind)}</span>` : ''}</div>
        ${l.note || l.tag ? `<div class="hint">${esc([l.tag, l.note].filter(Boolean).join(' · '))}</div>` : ''}
        ${ro ? '' : `<label class="field">Комментарий<input data-comment maxlength="500" value="${esc(l.comment)}"></label>
        <label class="field" data-remind-f hidden>Когда перезвонить<input data-remind maxlength="80" placeholder="например, 28.10 после 15:00"></label>
        <div class="row">${CC_BUTTONS.map(([k, t, cls]) => `<button class="btn small ${cls}" type="button" data-res="${k}">${esc(t)}</button>`).join('')}</div>`}
      </div>`).join('')}</div>${d.total > d.items.length ? `<p class="hint">Показаны первые ${d.items.length}.</p>` : ''}`
        : `<div class="empty">${d.clients.length ? 'Сейчас звонить некому: новые номера, перезвоны и недозвоны прошлых дней появятся здесь.' : 'Клиентов колл-центра пока нет.'}</div>`}</section>
    <p class="hint">Недозвон: номер вернётся завтра, всего три попытки. «Заинтересован» и «Неактуально» закрывают номер.</p></div>`;
  $('[data-cc-client]', view)?.addEventListener('change', e => { S.ccClient = e.target.value; reload(); });
  $$('.cc-lead', view).forEach(box => $$('[data-res]', box).forEach(b => b.addEventListener('click', async () => {
    const remindF = $('[data-remind-f]', box);
    if (b.dataset.res === 'callback' && remindF.hidden) { remindF.hidden = false; $('[data-remind]', box).focus(); return; }
    busy(b, true);
    try {
      await api(`/api/callcenter/leads/${box.dataset.id}/attempt`, {method: 'POST', body: {result: b.dataset.res,
        comment: $('[data-comment]', box).value, remind: $('[data-remind]', box).value}});
      box.remove(); toast('Записано');
    } catch (e) { busy(b, false); toast(e.message, true); }
  })));
}

// Клиенты колл-центра, их номера, цены и выгрузка листа в CSV (открывается в Google Таблицах).
async function ccSetup(view) {
  setHead({title: 'Клиенты и номера', back: () => go('callcenter')});
  const [d, w] = await loading(view, Promise.all([api('/api/callcenter'), api('/api/callcenter/work')]), 'page');
  const p = w.prices;
  view.innerHTML = `<div class="page"><div class="grid2">
    <section class="card blk"><div class="card-h"><h2>Добавить номера</h2></div>
      ${d.clients.length ? `<label class="field">Клиент<select data-v="client">${d.clients.map(c => `<option>${esc(c.name)}</option>`).join('')}</select></label>
      <label class="field">Номера<textarea data-v="text" rows="8" placeholder="+7 999 123-45-67; Иван Петров; ООО Ромашка"></textarea></label>
      <label class="field">Тег базы<input data-v="tag" maxlength="80" placeholder="например, МПП или 2ГИС 24.10"></label>
      <div class="row"><button class="btn primary" type="button" data-add-leads>Добавить</button></div>` : '<div class="empty">Сначала добавьте клиента.</div>'}</section>
    <section class="card blk" data-css="--i:1"><div class="card-h"><h2>Клиенты</h2><span class="hint">${fmt(d.clients.length)}</span></div>
      ${d.clients.length ? `<div class="tbl-wrap"><table class="tbl"><tbody>${d.clients.map(c => `<tr><td>${esc(c.name)}</td><td class="n">${fmt(c.leads)} номеров</td>
        <td class="nw"><a class="btn small" href="/api/callcenter/export${qs({client: c.name})}" download>Скачать CSV</a></td></tr>`).join('')}</tbody></table></div>` : ''}
      <label class="field" data-css="margin-top:12px">Новый клиент<input data-v="name" maxlength="80" placeholder="например, Кухни Краснодар"></label>
      <div class="row"><button class="btn" type="button" data-add-client>Добавить клиента</button></div>
      <h3>Цены</h3>
      <div class="row"><label class="field">Первичный, ₽<input data-p="first" inputmode="decimal" value="${p.first}"></label>
        <label class="field">Повторный, ₽<input data-p="repeat" inputmode="decimal" value="${p.repeat}"></label>
        <label class="field">Заинтересованный, ₽<input data-p="interested" inputmode="decimal" value="${p.interested}"></label></div>
      <div class="row"><button class="btn" type="button" data-prices>Сохранить цены</button></div></section></div></div>`;
  const v = k => $(`[data-v="${k}"]`, view);
  $('[data-add-client]', view).addEventListener('click', async e => {
    busy(e.currentTarget, true);
    try { await api('/api/callcenter/clients', {method: 'POST', body: {name: v('name').value}}); toast('Клиент добавлен'); reload(); }
    catch (err) { busy(e.currentTarget, false); toast(err.message, true); }
  });
  $('[data-add-leads]', view)?.addEventListener('click', async e => {
    busy(e.currentTarget, true);
    try {
      const r = await api('/api/callcenter/leads', {method: 'POST', body: {client: v('client').value, text: v('text').value, tag: v('tag').value}});
      toast(`Добавлено: ${r.added}` + (r.duplicates ? `, повторы: ${r.duplicates}` : '') + (r.invalid ? `, не распознано: ${r.invalid}` : ''));
      reload();
    } catch (err) { busy(e.currentTarget, false); toast(err.message, true); }
  });
  $('[data-prices]', view).addEventListener('click', async e => {
    busy(e.currentTarget, true);
    try {
      await api('/api/callcenter/prices', {method: 'POST', body: Object.fromEntries($$('[data-p]', view).map(i => [i.dataset.p, i.value]))});
      busy(e.currentTarget, false); toast('Цены сохранены');
    } catch (err) { busy(e.currentTarget, false); toast(err.message, true); }
  });
}
