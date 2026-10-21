// Сводка директора: что с отделом за период. Пропущенные входящие, четыре цифры, график,
// менеджеры одной таблицей. Карточка менеджера выезжает справа; «когда лучше звонить» спрятано в «Подробнее».
'use strict';

const PERIOD_OPTS = [['today', 'Сегодня'], ['7', '7 дней'], ['30', '30 дней'], ['month', 'Месяц']];
const PERIOD_NAME = {today: 'сегодня', 7: 'за 7 дней', 30: 'за 30 дней', month: 'за месяц'};
const WD_LONG = ['', 'понедельник', 'вторник', 'среду', 'четверг', 'пятницу', 'субботу', 'воскресенье'];

const rate = (a, b) => b ? a / b * 100 : 0;
const shortName = n => { const w = String(n || '').trim().split(/\s+/); return w.length > 1 && n.length > 14 ? w[0] : n; };

// Подписи и подсказки графика из ряда сервера: часы сегодня или дни периода.
function chartRows(data) {
  const nowH = +String(data.now || '').slice(0, 2);
  if (data.hourly) {
    return data.series.map(d => ({...d, x: String(+d.key), label: `${d.key}:00–${String(+d.key + 1).padStart(2, '0')}:00`, future: +d.key > nowH}));
  }
  const rows = data.series.filter(d => d.dials || new Date(d.key + 'T12:00:00Z').getUTCDay() !== 0);
  const short = rows.length <= 10;
  return rows.map(d => ({...d, x: short ? ddmm(d.key) : String(+d.key.slice(8)), label: dayLong(d.key)}));
}

function heatHtml(data) {
  const cells = {};
  data.heat.forEach(c => { cells[c.wd + ':' + c.hour] = c; });
  const days = [1, 2, 3, 4, 5, 6].concat(data.heat.some(c => c.wd === 7) ? [7] : []);
  const hours = Array.from({length: 11}, (_, i) => 9 + i);
  const rated = data.heat.filter(c => c.dials >= 5).map(c => ({...c, r: c.live / c.dials}));
  if (rated.length < 3) return '<p class="hint">Пока мало звонков, чтобы сравнить часы.</p>';
  const best = rated.reduce((a, b) => b.r > a.r ? b : a), worst = rated.reduce((a, b) => b.r < a.r ? b : a);
  const span = Math.max(.01, best.r - worst.r);
  const WDS = ['', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс'];
  return `<p class="insight">Чаще всего берут трубку в ${WD_LONG[best.wd]} в ${best.hour}:00 (${pctTxt(best.r * 100)}), реже всего в ${WD_LONG[worst.wd]} в ${worst.hour}:00 (${pctTxt(worst.r * 100)}).</p>
    <div class="heat-wrap"><div class="heat" role="img" aria-label="Дозвон по дням недели и часам за ${data.heat_days} дней">
      <span></span>${hours.map(h => `<span>${h}</span>`).join('')}
      ${days.map((d, i) => `<span class="d">${WDS[d]}</span>` + hours.map((h, j) => {
        const c = cells[d + ':' + h];
        if (!c || c.dials < 3) return `<div class="cell none" data-css="--k:${i * 11 + j}" title="${WDS[d]} ${h}:00 · мало звонков"></div>`;
        const r = c.live / c.dials;
        return `<div class="cell" data-css="--p:${Math.round(10 + (r - worst.r) / span * 90)}%;--k:${i * 11 + j}" title="${WDS[d]} ${h}:00 · дозвон ${pctTxt(r * 100)} из ${c.dials}"></div>`;
      }).join('')).join('')}
    </div></div>
    <div class="heat-leg">${pctTxt(worst.r * 100)}<i></i>${pctTxt(best.r * 100)} берут трубку · за ${data.heat_days} дней, серым — меньше 3 звонков</div>`;
}

// Где менеджер теряет клиентов: из карточки сотрудника (30 дней), с кнопками-примерами.
function lossHtml(p) {
  if (p.kitchen) return '<p class="hint">Звонки этого сотрудника бот разбирает по отдельному скрипту, ошибок здесь нет.</p>';
  const list = (p.mistakes || []).slice(0, 5);
  if (!list.length) return '<p class="hint">Потерь с понятной причиной за 30 дней нет.</p>';
  const max = Math.max(...list.map(x => x.month));
  return hbars(list.map(x => [`${esc(x.label)}${x.examples.length ? `<span class="p-ex">${x.examples.map(c => `<button class="btn small" type="button" data-review="${c.id}">${esc(when(c.at))}</button>`).join('')}</span>` : ''}`, x.month, x.month / max]));
}
const personLinks = ext => `<div class="links">
  <button class="ghost" type="button" data-go="calls" data-fext="${esc(ext)}">Звонки →</button>
  <button class="ghost" type="button" data-go="callbacks" data-fext="${esc(ext)}">Перезвоны →</button>
  <button class="ghost" type="button" data-go="deals" data-fext="${esc(ext)}">Сделки →</button>
  <button class="ghost" type="button" data-go="queue" data-arg="${esc(ext)}">Очередь →</button>
  <button class="ghost" type="button" data-go="staff" data-arg="${esc(ext)}">Полная карточка →</button></div>`;

async function managerDrawer(m, data) {
  const body = openDrawer({title: m.name, avatar: m.name, sub: `доб. ${esc(m.ext)} · ${PERIOD_NAME[data.period]}`, html: `
    <div class="minis m4">
      <div class="mini"><span>Наборов</span><b>${fmt(m.dials)}</b></div>
      <div class="mini"><span>Дозвон</span><b>${pctOf(m.live, m.dials)}</b></div>
      <div class="mini"><span>С результатом</span><b>${fmt(m.result)}</b></div>
      <div class="mini"><span>Встречи и КП</span><b>${fmt(m.meet)}</b></div></div>
    <section data-loss><h3>Где теряем · 30 дней</h3><div class="sk line"></div><div class="sk line" data-css="width:60%;margin-top:8px"></div></section>
    <section><h3>Открыть</h3>${personLinks(m.ext)}</section>`});
  let p;
  try { p = await api('/api/person' + qs({person: m.ext})); } catch (e) { $('[data-loss]', body).innerHTML = `<h3>Где теряем</h3><p class="hint">${esc(e.message)}</p>`; return; }
  const mem = (p.memory || [])[0];
  const loss = $('[data-loss]', body);
  if (!loss) return;
  loss.innerHTML = `<h3>Где теряем · 30 дней</h3>${lossHtml(p)}`;
  if (mem && mem.focus) loss.insertAdjacentHTML('afterend', `<section class="card focus"><span class="lab">Фокус от бота · ${esc(ddmm(mem.day))}</span><div class="big">${esc(mem.focus)}</div></section>`);
  const pos = (p.positives || [])[0];
  if (pos) $('section:last-child', body).insertAdjacentHTML('beforebegin', `<section><h3>Что получается</h3><div>${esc(pos.why)}</div></section>`);
}

VIEWS.summary = async view => {
  let seq = 0, team = null;
  const head = () => setHead({filters: seg('period', PERIOD_OPTS, S.f.period, 'Период') +
    (team && team.length > 1 ? seg('ext', [['', 'Весь отдел'], ...team.map(p => [p.ext, shortName(p.name)])], S.f.ext, 'Менеджер') : '')});
  const bind = () => bindSeg($('#top'), {
    period: v => { S.f.period = v; saveFilters(); redraw(); },
    ext: v => { S.f.ext = v; saveFilters(); redraw(); },
  });
  head(); bind();
  const redraw = () => draw().catch(e => { if (view.isConnected) view.innerHTML = errCard(e.message); });

  async function draw() {
    const my = ++seq;
    let data;
    try {
      data = await loading(view, api('/api/summary' + qs({period: S.f.period, ext: S.f.ext})), 'page');
    } catch (e) {
      if (S.f.ext) { S.f.ext = ''; saveFilters(); head(); bind(); return draw(); }
      throw e;
    }
    if (my !== seq || !view.isConnected) return;
    S.today = data.today || S.today;
    if (!team) { team = data.team; head(); bind(); }
    const c = data.cur, p = data.prev;
    const hasPrev = p.dials > 0;
    const vs = hasPrev ? data.vs : '';
    let i = 0;
    const html = [];
    if (data.missed.length) {
      const n = data.missed.length;
      html.push(`<div class="alert blk" data-css="--i:${i++}"><span class="pdot"></span>
        <p><b>${plainItems(n, 'пропущенный входящий', 'пропущенных входящих', 'пропущенных входящих')}</b>, никто не перезвонил ${help('Клиент звонил, трубку не взяли, и после этого с ним никто не поговорил. Нажмите на номер, чтобы перезвонить.')}</p>
        <div class="alert-list">${data.missed.slice(0, 12).map(m => `<button type="button" data-num="${esc(m.number)}">${esc(phone(m.number))} · ${esc(when(m.at))}</button>`).join('')}${n > 12 ? `<span>и ещё ${n - 12}</span>` : ''}</div></div>`);
    }
    const kpis = [
      {key: 'dials', label: 'Наборов', value: c.dials, delta: hasPrev ? delta(c.dials, p.dials) : '', vs},
      {key: 'rate', label: 'Дозвон', value: rate(c.live, c.dials), kind: 'pct', delta: hasPrev ? delta(c.live / (c.dials || 1), p.live / (p.dials || 1), true) : '', vs,
        sub: `${plainItems(c.live, 'живой разговор', 'живых разговора', 'живых разговоров')}`,
        help: `Исходящий разговор от ${data.live_seconds} секунд. Автоответчики и сбросы не считаем.`},
      {key: 'result', label: 'Разговоров с результатом', value: c.result, delta: hasPrev ? delta(c.result, p.result) : '', vs,
        help: 'Встреча, перезвон с датой, отправленное КП или контакт ЛПР. Считает бот по разбору разговора.'},
      {key: 'meet', label: 'Встречи и КП', value: c.meet, delta: hasPrev ? delta(c.meet, p.meet) : '', vs},
    ];
    html.push(`<div class="kpis">${kpis.map(k => kpiHtml(k, i++)).join('')}</div>`);
    const rows = chartRows(data);
    const cut = !data.hourly && rows.length < data.series.length;
    html.push(`<section class="card blk" data-css="--i:${i++}"><div class="card-h"><h2>${data.hourly ? 'Сегодня по часам' : 'По дням'}</h2>
      <span class="hint">${data.hourly ? `сейчас ${esc(data.now)}` : cut ? 'воскресенья без звонков не показаны' : ''}</span></div><div class="chart" data-chart></div></section>`);
    const ms = data.managers;
    if (!S.f.ext && ms.length) {
      html.push(`<section class="card blk" data-css="--i:${i++}"><div class="card-h"><h2>Менеджеры</h2><span class="hint">нажмите на строку, чтобы открыть карточку</span></div>
        <div class="tbl-wrap"><table class="tbl anim"><thead><tr><th>Менеджер</th><th class="n">Наборов</th><th class="n">Дозвон</th><th class="n hide-m">На линии</th>
          <th class="n">С результатом</th><th class="n hide-m">Встречи и КП</th><th class="n">Потеряно с шансом ${help('Разговор без результата, в котором, по разбору бота, шанс был.')}</th><th class="hide-m">Частая ошибка</th><th></th></tr></thead>
        <tbody>${ms.map((m, r) => `<tr class="click" tabindex="0" data-ext="${esc(m.ext)}" data-css="--r:${r}">
          <td><span class="who">${ava(m.name)}<span>${esc(m.name)}<small>доб. ${esc(m.ext)}</small></span></span></td>
          <td class="n">${fmt(m.dials)}</td><td class="n">${pctOf(m.live, m.dials)}</td><td class="n hide-m">${hm(m.seconds)}</td>
          <td class="n">${fmt(m.result)}</td><td class="n hide-m">${fmt(m.meet)}</td>
          <td class="n">${m.lost ? '<span class="dot"></span>' : ''}${fmt(m.lost)}</td>
          <td class="hide-m wrap">${m.mistake ? esc(m.mistake.label) : '<span class="muted">—</span>'}</td>
          <td>${svg('chev', 'go')}</td></tr>`).join('')}</tbody></table></div></section>`);
    } else if (S.f.ext && ms.length) {
      html.push(`<section class="card blk" data-css="--i:${i++}" data-one><div class="card-h"><h2>Где теряем · 30 дней</h2></div><div data-loss><div class="sk line"></div></div>
        <div data-css="margin-top:14px">${personLinks(ms[0].ext)}</div></section>`);
    }
    html.push(moreBlock('Подробнее', 'когда лучше дозваниваться', heatHtml(data), i++));
    view.innerHTML = `<div class="page">${html.join('')}</div>`;
    drawChart($('[data-chart]', view), rows, {
      bands: [{key: 'dials', label: 'Наборы', color: 'var(--s1)'}, {key: 'result', label: 'Разговоры с результатом', color: 'var(--s2)'}],
      tipRows: d => [['Наборов', 'var(--s1)', fmt(d.dials)], ['Дозвон', '', pctOf(d.live, d.dials)], ['С результатом', 'var(--s2)', fmt(d.result)]],
      aria: 'Наборы и разговоры с результатом ' + (data.hourly ? 'по часам' : 'по дням'),
    });
    countUp(view);
    $$('tr[data-ext]', view).forEach(tr => {
      const open = () => managerDrawer(ms.find(m => m.ext === tr.dataset.ext), data);
      tr.addEventListener('click', open);
      tr.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } });
    });
    $$('[data-num]', view).forEach(b => b.addEventListener('click', () => workDrawer({kind: 'call', number: b.dataset.num, title: 'Пропущенный входящий'})));
    if ($('[data-one]', view)) {
      api('/api/person' + qs({person: S.f.ext})).then(pp => { const el = $('[data-one] [data-loss]', view); if (el) el.innerHTML = lossHtml(pp); })
        .catch(e => { const el = $('[data-one] [data-loss]', view); if (el) el.innerHTML = `<p class="hint">${esc(e.message)}</p>`; });
    }
  }
  await draw();
};
