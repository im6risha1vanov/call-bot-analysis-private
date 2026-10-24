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

VIEWS.callcenter = async (view, arg) => {
  if (arg) return cabinetView(view, arg);
  const d = await loading(view, api('/api/callcenter'), 'page');
  const t = d.total;
  let i = 0;
  view.innerHTML = `<div class="page">
    <div class="kpis">${[
      {key: 'cc-called', label: 'Лидов обзвонено', value: t.called || 0, sub: `из ${fmt(t.total)} переданных`},
      {key: 'cc-rate', label: 'Дозвон', value: t.called ? t.reached / t.called * 100 : 0, kind: 'pct', sub: `${fmt(t.reached)} взяли трубку`},
      {key: 'cc-int', label: 'Заинтересованных', value: t.interested || 0},
      {key: 'cc-sum', label: 'Сумма по тарифу', value: t.sum || 0, kind: 'rub', help: 'Первичные и повторные звонки и заинтересованные по ценам из листа «Сводка».'}].map(k => kpiHtml(k, i++)).join('')}</div>
    ${repeatInsight(t.reached_first || 0, t.reached || 0, t.called || 0).replace('class="insight"', `class="insight blk" data-css="--i:${i++}"`)}
    <section class="card blk" data-css="--i:${i++}"><div class="card-h"><h2>По клиентам</h2><span class="hint">нажмите, чтобы открыть кабинет так, как его видит клиент</span></div>
      ${d.clients.length ? `<div class="tbl-wrap"><table class="tbl anim"><thead><tr><th>Клиент</th><th class="n">Лидов</th><th class="n">Дозвон</th><th class="n">Заинтересованы</th>
        <th class="n hide-m">Перезвон</th><th class="n hide-m">Ждут попытки ${help('Не взяли трубку, а третьей попытки ещё не было.')}</th><th class="n">Сумма</th><th></th></tr></thead><tbody>
        ${d.clients.map((c, r) => `<tr class="click" tabindex="0" data-name="${esc(c.name)}" data-css="--r:${r}"><td><div class="t">${esc(c.name)}</div>${c.since ? `<div class="s">с ${esc(ddmm(c.since))}</div>` : ''}</td>
          <td class="n">${fmt(c.leads)}</td><td class="n">${pctOf(c.reached, c.called)}</td><td class="n">${fmt(c.interested)}</td><td class="n hide-m">${fmt(c.callback)}</td>
          <td class="n hide-m">${fmt(c.waiting)}</td><td class="n nw">${rub(c.sum)}</td><td>${svg('chev', 'go')}</td></tr>`).join('')}</tbody></table></div>`
        : '<div class="empty">В таблице колл-центра пока нет клиентов.</div>'}</section>
    ${d.days.length > 1 ? `<section class="card blk" data-css="--i:${i++}"><div class="card-h"><h2>Звонки по дням</h2><span class="hint">последние ${d.days.length} ${plural(d.days.length, 'день', 'дня', 'дней')} со звонками</span></div><div class="chart" data-chart></div></section>` : ''}
    <section class="card blk" data-css="--i:${i++}"><div class="card-h"><h2>Кому Ольге перезвонить</h2><span class="hint">${fmt(d.callbacks.length)}</span></div>
      ${d.callbacks.length ? longList(d.callbacks, l => leadRows(l, true, true)) : '<div class="empty">Перезвонов нет.</div>'}</section>
    ${moreBlock('Подробнее', 'по базам и источникам', `
      <h3>По базам</h3>${d.bases.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Клиент</th><th>База от</th><th class="n">Номеров</th><th class="n">Обзвонено</th><th class="n">Дозвон</th><th class="n">Заинтерес.</th></tr></thead><tbody>
        ${d.bases.map(b => `<tr><td>${esc(b.client)}</td><td class="nw">${yearDate(b.date)}</td><td class="n">${fmt(b.total)}</td><td class="n">${fmt(b.called)}</td><td class="n">${pctOf(b.reached || 0, b.called || 0)}</td><td class="n">${fmt(b.interested)}</td></tr>`).join('')}</tbody></table></div>` : '<p class="hint">Дат баз в таблице нет.</p>'}
      <h3>Источники</h3>${d.sources.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Клиент</th><th>Источник</th><th class="n">Номеров</th><th class="n">Дозвон</th><th class="n">Заинтерес.</th></tr></thead><tbody>
        ${d.sources.map(s => `<tr><td>${esc(s.client)}</td><td>${esc(s.name || 'без тега')}</td><td class="n">${fmt(s.total)}</td><td class="n">${pctOf(s.reached, s.total)}</td><td class="n">${fmt(s.interested)}</td></tr>`).join('')}</tbody></table></div>` : '<p class="hint">Источников нет.</p>'}`, i++)}
  </div>`;
  if ($('[data-chart]', view)) daysChart($('[data-chart]', view), d.days);
  countUp(view);
  const open = tr => go('callcenter', tr.dataset.name);
  $('tbody', view)?.addEventListener('click', e => { const tr = e.target.closest('[data-name]'); if (tr) open(tr); });
  $('tbody', view)?.addEventListener('keydown', e => { const tr = e.target.closest('[data-name]'); if (tr && e.key === 'Enter') open(tr); });
};

// Кабинет клиента: у клиента — свой, у директора — любой из колл-центра.
async function cabinetView(view, name) {
  const dir = S.me.role === 'director';
  setHead({title: dir ? name : 'Кабинет', back: dir ? () => go('callcenter') : null,
    actions: dir && !S.me.readonly ? '<button class="btn" type="button" data-cl-link>Ссылка для клиента</button>' : ''});
  $('[data-cl-link]', $('#top'))?.addEventListener('click', e => clientLink(e.currentTarget, name));
  const c = await loading(view, api('/api/cabinet' + qs({client: dir ? name : ''})), 'page');
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
