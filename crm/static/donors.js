// Вкладка «Агент доноров» в разделе «Источники»: менеджер задаёт нишу и географию, агент на сервере сам
// ищет доноров (crm/donor_agent.py) и проверяет номера. Страница показывает прогоны, ход работы и итог.
// Работает поверх app.js (api, toast, go, busy, esc, fmtWhen, askConfirm).
'use strict';

(() => {
  const VERDICT = {go: ['В парсинг', 'good'], test: ['Проверить тестом', 'warn'], no: ['Не брать', 'bad']};
  const STATUS = {queued: ['В очереди', 'acc'], running: ['Идёт', 'acc'], done: ['Готово', 'good'], failed: ['Ошибка', 'bad'], stopped: ['Остановлен', 'warn']};
  const chip = ([t, c]) => `<span class="chip ${c}">${esc(t)}</span>`;
  const rub = v => fmt(v) + ' ₽';
  let timer = null;
  const live = box => box.isConnected;
  const poll = (box, fn) => { clearTimeout(timer); timer = setTimeout(() => { if (live(box)) fn(); }, 4000); };

  async function list(box) {
    const d = await api('/api/donors');
    const ro = S.me.readonly;
    const notReady = [!d.ready.ai && 'ИИ', !d.ready.search && 'поиск Яндекса'].filter(Boolean);
    box.innerHTML = `<div class="grid2">
      <section class="card blk"><div class="card-h"><h2>Новый поиск</h2><span class="hint">сегодня ${rub(d.limit.spent_rub)} из ${rub(d.limit.day_rub)}</span></div>
        <p class="hint" data-css="margin:0 0 10px">Агент сам составит план, пройдёт по выдаче Яндекса и сайтам конкурентов, найдёт номера людей и проверит их. Прогон идёт на сервере 20–60 минут, страницу можно закрыть.</p>
        ${notReady.length ? `<p class="bad-text">Не подключено: ${esc(notReady.join(' и '))}. Ключи ставит администратор в .env сервера.</p>` : ''}
        <label class="field" data-css="margin-top:10px">Ниша клиента<input data-d="niche" maxlength="200" placeholder="например: кухни на заказ, банкротство физлиц"></label>
        <label class="field" data-css="margin-top:10px">География<input data-d="city" maxlength="200" placeholder="Вся Россия, или город, или несколько через запятую"></label>
        <label class="field" data-css="margin-top:10px">Что продаёт клиент (необязательно)<input data-d="offer" maxlength="500"></label>
        <label class="field" data-css="margin-top:10px">Сам клиент, чтобы не взять его донором (необязательно)<input data-d="client" maxlength="200" placeholder="сайт или название"></label>
        <label class="field" data-css="margin-top:10px">Сколько проверенных номеров нужно<select data-d="target">${d.targets.map(t => `<option${t === 20 ? ' selected' : ''}>${t}</option>`).join('')}</select></label>
        <div class="row" data-css="margin-top:12px"><button class="btn primary" type="button" data-start${ro ? ' disabled' : ''}>Найти номера</button></div>
        <p class="hint">Один прогон стоит не больше ${rub(d.limit.run_rub)}. Общий лимит на день ${rub(d.limit.day_rub)}.</p></section>
      <section class="card blk" data-css="--i:1"><div class="card-h"><h2>Прогоны</h2><span class="hint">${d.runs.length || ''}</span></div>
        ${d.runs.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Ниша</th><th>Статус</th><th>Номера</th><th class="hide-m">Кто</th></tr></thead><tbody>
        ${d.runs.map(r => `<tr class="click" data-run="${r.id}"><td>${esc(r.niche)}<div class="s">${esc(r.city || 'вся Россия')} · ${fmtWhen(r.created_at)}</div></td>
          <td>${chip(STATUS[r.status] || [r.status, ''])}${r.status === 'running' ? `<div class="s">${esc(r.stage)}</div>` : ''}</td>
          <td class="num">${r.good != null ? `${r.good} из ${r.target}` : '—'}</td><td class="hide-m">${esc(r.author || '')}</td></tr>`).join('')}
        </tbody></table></div>` : '<div class="empty">Прогонов пока нет.</div>'}
        ${d.can_settings ? `<details class="more" data-css="margin-top:14px"><summary>${svg('chev')}<span>Сообщение продавцам на Авито <span class="hint">понадобится на этапе Авито</span></span></summary>
          <div class="more-in"><label class="field" data-css="margin-top:10px">Текст первого сообщения<textarea data-d="avito" rows="2" maxlength="500">${esc(d.avito_text)}</textarea></label>
          <p class="hint">Агент не выдаёт себя за покупателя и не спрашивает про товар. Если продавец ответит как покупателю, следующее сообщение говорит, что это Averon.</p>
          <div class="row"><button class="btn small" type="button" data-avito>Сохранить</button></div></div></details>` : ''}</section></div>`;
    $$('[data-run]', box).forEach(tr => tr.addEventListener('click', () => go('sources', 'run-' + tr.dataset.run)));
    $('[data-start]', box).addEventListener('click', async e => {
      const val = k => $(`[data-d="${k}"]`, box).value.trim();
      if (val('niche').length < 3) { toast('Напишите нишу клиента', true); return; }
      busy(e.currentTarget, true);
      try {
        const r = await api('/api/donors', {method: 'POST', body: {niche: val('niche'), city: val('city'), offer: val('offer'), client: val('client'), target: +val('target')}});
        toast('Агент начал поиск');
        go('sources', 'run-' + r.id);
      } catch (err) { busy(e.currentTarget, false); toast(err.message, true); }
    });
    $('[data-avito]', box)?.addEventListener('click', async e => {
      try { await api('/api/donors/settings', {method: 'POST', body: {avito_text: $('[data-d="avito"]', box).value}}); doneBtn(e.currentTarget, 'Сохранено'); }
      catch (err) { toast(err.message, true); }
    });
    if (d.runs.some(r => r.status === 'queued' || r.status === 'running')) poll(box, () => list(box));
  }

  const copy = async (text, what) => {
    if (!text) { toast('Нечего копировать', true); return; }
    try { await navigator.clipboard.writeText(text); toast('Скопировано: ' + what); } catch (e) { toast('Браузер не дал скопировать', true); }
  };
  const csvCell = v => /[";\n]/.test(String(v)) ? '"' + String(v).replace(/"/g, '""') + '"' : String(v);
  function csv(run) {
    const head = ['Номер', 'Вердикт', 'Почему', 'Тип', 'Чей', 'Компания', 'Сайт', 'Город', 'Оператор', 'Источник'];
    const rows = run.result.phones.map(p => ['+' + p.n, VERDICT[p.verdict][0], p.why.join('; '), p.type, p.who, p.company, p.site, p.city, p.operator, p.src]);
    const blob = new Blob(['﻿' + [head, ...rows].map(r => r.map(csvCell).join(';')).join('\n')], {type: 'text/csv;charset=utf-8'});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `доноры-${run.niche.slice(0, 40)}.csv`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  let filter = 'go';
  async function detail(box, id) {
    const run = await api('/api/donors/' + id);
    const res = run.result || {phones: [], sites: [], good: 0, live: 0, summary: ''};
    const going = run.status === 'queued' || run.status === 'running';
    const counts = {go: 0, test: 0, no: 0};
    res.phones.forEach(p => { counts[p.verdict]++; });
    if (!counts[filter] && res.phones.length) filter = counts.go ? 'go' : counts.test ? 'test' : 'no';
    const phones = res.phones.filter(p => p.verdict === filter);
    const plan = run.plan;
    const manage = run.can_manage && !S.me.readonly;
    box.innerHTML = `<div data-css="display:grid;gap:16px"><section class="card blk"><div class="card-h"><h2>${esc(run.niche)}</h2>${chip(STATUS[run.status] || [run.status, ''])}<span class="sp"></span>
        ${going && manage ? '<button class="btn small" type="button" data-stop>Остановить</button>' : ''}
        ${!going && manage ? '<button class="btn small" type="button" data-del>Удалить</button>' : ''}
        <button class="btn small" type="button" data-back>Все прогоны</button></div>
      <p class="hint" data-css="margin:0">${esc(run.city || 'Вся Россия')}${run.offer ? ' · ' + esc(run.offer) : ''} · запустил ${esc(run.author || '—')} ${fmtWhen(run.created_at)}</p>
      <p data-css="margin:10px 0 0"><b>${esc(run.stage || '')}</b></p>
      <div class="kpis" data-css="margin-top:12px"><div class="kpi"><span class="lab">В парсинг</span><span class="val">${counts.go}</span><span class="foot">нужно ${run.target}</span></div>
        <div class="kpi"><span class="lab">Живые люди</span><span class="val">${res.live}</span><span class="foot">мобильный, имя или WhatsApp</span></div>
        <div class="kpi"><span class="lab">Компаний разобрано</span><span class="val">${res.sites.length}</span><span class="foot">запросов ${run.queries.length}</span></div>
        <div class="kpi"><span class="lab">Расход</span><span class="val">${fmt(run.rub)}<small> ₽</small></span><span class="foot">поисков ${run.searches}</span></div></div></section>
      ${res.phones.length ? `<section class="card blk" data-css="--i:1"><div class="card-h"><h2>Номера</h2><span class="sp"></span>
          <button class="btn small" type="button" data-copy="calls">Номера для звонков</button><button class="btn small" type="button" data-copy="sms">Для SMS</button>
          <button class="btn small" type="button" data-copy="sites">Сайты</button><button class="btn small" type="button" data-csv>CSV</button></div>
        <div class="filters" data-css="margin-bottom:12px">${Object.keys(VERDICT).map(k => `<button type="button" data-f="${k}" aria-pressed="${filter === k}">${VERDICT[k][0]}<span class="c">${counts[k]}</span></button>`).join('')}</div>
        <div class="tbl-wrap"><table class="tbl"><thead><tr><th>Номер</th><th>Чей</th><th class="hide-m">Компания</th><th>Почему</th></tr></thead><tbody>
        ${phones.map(p => `<tr><td class="num nw">${esc(p.number)}${p.live ? '<div class="s">живой человек</div>' : ''}${p.operator ? `<div class="s">${esc(p.operator)}</div>` : ''}</td>
          <td>${esc(p.who || '—')}<div class="s">${esc(p.type)}${p.city ? ' · ' + esc(p.city) : ''}</div></td>
          <td class="hide-m">${esc(p.company)}${p.site ? `<div class="s">${esc(p.site)}</div>` : ''}</td><td class="s">${esc(p.why.join('; '))}</td></tr>`).join('')}
        </tbody></table></div></section>` : going ? '' : emptyCard('Номеров не нашлось.' + (run.error ? ' ' + esc(run.error) : ''), 1)}
      ${plan ? moreBlock('План поиска', `сегментов ${(plan.segments || []).length}`, `<p><b>Кого ищет клиент:</b> ${esc(plan.buyer || '')}</p>
        ${(plan.segments || []).map(s => `<p><b>${esc(s.name)}</b> <span class="chip">${esc(s.type)}</span><br><span class="hint">Звонят: ${esc(s.who_calls)}. Где искать: ${esc(s.where)}</span></p>`).join('')}
        ${(plan.avoid || []).length ? `<p><b>Чего избегаем:</b> ${esc(plan.avoid.join('; '))}</p>` : ''}${plan.note ? `<p class="hint">${esc(plan.note)}</p>` : ''}`, 2) : ''}
      ${res.sites.length ? moreBlock('Компании', `${res.sites.length}`, `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Компания</th><th>Вердикт</th><th>Почему</th></tr></thead><tbody>
        ${res.sites.map(s => `<tr><td>${esc(s.name || s.site)}<div class="s">${esc(s.site)} · ${esc(s.kind || '')}</div></td><td>${esc(s.verdict || '')}</td><td class="s">${esc(s.why)}</td></tr>`).join('')}</tbody></table></div>`, 3) : ''}
      ${Object.keys(run.cost || {}).length ? moreBlock('Расход по шагам', rub(run.rub), `<div class="tbl-wrap"><table class="tbl"><tbody>${Object.entries(run.cost).sort((a, b) => b[1] - a[1]).map(([k, v]) =>
        `<tr><td>${esc(k)}</td><td class="num">${rub(v)}</td></tr>`).join('')}</tbody></table></div>`, 4) : ''}
      ${moreBlock('Ход работы', `${run.log.length}`, `<div class="s">${run.log.slice().reverse().map(l => `<div>${esc(l.t)} ${esc(l.text)}</div>`).join('')}</div>`, 5, going)}</div>`;
    $('[data-back]', box).addEventListener('click', () => go('sources', null));
    $$('[data-f]', box).forEach(b => b.addEventListener('click', () => { filter = b.dataset.f; detail(box, id); }));
    $('[data-stop]', box)?.addEventListener('click', async e => {
      busy(e.currentTarget, true);
      try { await api(`/api/donors/${id}/stop`, {method: 'POST'}); toast('Останавливаю после текущего шага'); } catch (err) { toast(err.message, true); }
    });
    $('[data-del]', box)?.addEventListener('click', async () => {
      if (!await askConfirm('Удалить прогон?', 'Удалить', true)) return;
      try { await api(`/api/donors/${id}/delete`, {method: 'POST'}); go('sources', null); } catch (err) { toast(err.message, true); }
    });
    $$('[data-copy]', box).forEach(b => b.addEventListener('click', () => {
      const k = b.dataset.copy, good = res.phones.filter(p => p.verdict === 'go');
      if (k === 'calls') copy(good.map(p => '+' + p.n).join('\n'), 'номера для звонков');
      if (k === 'sms') copy(good.filter(p => p.sms).map(p => '+' + p.n).join('\n'), 'номера для SMS');
      if (k === 'sites') copy(res.sites.filter(s => s.verdict === 'берём' && s.site).map(s => s.site).join('\n'), 'сайты');
    }));
    $('[data-csv]', box)?.addEventListener('click', () => csv(run));
    if (going) poll(box, () => detail(box, id));
  }

  window.DonorAgent = (box, arg) => {
    const m = /^run-(\d+)$/.exec(arg || '');
    return m ? detail(box, m[1]) : list(box);
  };
})();
