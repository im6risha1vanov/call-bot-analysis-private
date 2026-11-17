// Разбор звонков колл-центра: оператор, период по Москве, фоновая обработка,
// воронка этапов, покрытие расшифровок и сверка с отмеченными лидами CRM.
// Счёт клиентов и суммы в «Колл-центре» остаётся прежним; это отдельный экран.
'use strict';

const CA_STATUS = {queued: 'Ждёт обработки', running: 'Идёт разбор', done: 'Готов', failed: 'Ошибка'};
const CA_SOURCE = {existing: 'Сохранённая расшифровка', deepgram: 'Новая расшифровка Deepgram', robot: 'Расшифровка робота', none: 'Нет'};
const CA_WEAK = ['audio_missing', 'transcript_failed', 'failed', 'missing_call'];
const CA_SCRIPT_STATES = {
  complete: 'Все шаги найдены', review: 'Проверить пропуски', stopped: 'Разговор остановлен',
  not_reached: 'Недозвон', answering_machine: 'Автоответчик', unknown: 'Недостаточно данных',
  other_topic: 'Тема скрипта не подтверждена', needs_script: 'Нужен скрипт проекта'
};

function caOperatorName(list, id) {
  const x = (list || []).find(o => o.id === id);
  return x ? (x.display || x.name) : id;
}

function caStageClass(stage) {
  if (stage.state === 'yes') return 'cur';
  if (stage.state === 'not_applicable') return 'lost';
  return '';
}

function caFunnel(result) {
  return `<ol class="stages" role="list" aria-label="Воронка разбора звонков">${(result.stages || []).map((st, k) => `
    <li class="stage-cell">
      <div class="stage ${caStageClass(st)}">
        <span class="nm">${k + 1}. ${esc(st.name)}</span>
        <b>${fmt(st.reached)}</b>
        <span class="ls">${st.unknown ? `неизвестно ${fmt(st.unknown)}` : st.not_applicable ? `не применимо ${fmt(st.not_applicable)}` : ''}</span>
      </div>
      ${k < result.stages.length - 1 ? `<span class="stage-join" aria-hidden="true">${svg('chev')}</span>` : ''}
    </li>`).join('')}</ol>`;
}

function caCoverage(result) {
  const c = result.coverage || {};
  return `<div class="tbl-wrap"><table class="tbl"><tbody>
    <tr><td>Всего звонков</td><td class="n">${fmt(c.calls)}</td><td class="hint">телефонов ${fmt(c.phones)}</td></tr>
    <tr><td>Сохранённые расшифровки</td><td class="n">${fmt(c.existing_transcripts)}</td><td></td></tr>
    <tr><td>Расшифровано сейчас</td><td class="n">${fmt(c.transcribed)}</td><td class="hint">Deepgram в фоне, не в запросе страницы</td></tr>
    <tr><td>Расшифровки робота</td><td class="n">${fmt(c.robot_transcripts)}</td><td></td></tr>
    <tr><td>Без расшифровки</td><td class="n">${fmt(c.no_transcript)}</td><td class="hint">короткие звонки и недозвоны</td></tr>
    <tr><td>Нет записи</td><td class="n">${fmt(c.audio_missing)}</td><td class="hint">аудио недоступно — отдельно</td></tr>
    <tr><td>Ошибка расшифровки</td><td class="n">${fmt(c.transcript_failed)}</td><td class="hint">технический обрыв</td></tr>
    <tr><td>Неуверенная расшифровка</td><td class="n">${fmt(c.low_confidence)}</td><td class="hint">этапы не додумываются</td></tr>
    <tr><td>Неизвестная атрибуция</td><td class="n">${fmt(c.unknown_attribution)}</td><td class="hint">номер не связан с CRM</td></tr>
  </tbody></table></div>`;
}

function caReasons(result) {
  if (!(result.reasons || []).length) return '<div class="empty">Причин обрыва нет.</div>';
  return `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Где оборвалось</th><th>Причина</th><th class="n">Звонков</th></tr></thead><tbody>
    ${result.reasons.map(r => `<tr><td>${esc({quality: 'Качество базы', technical: 'Технический обрыв', operator: 'Возможный пропуск оператора', client: 'Клиент', crm: 'CRM', unknown: 'Неизвестно'}[r.group] || r.group)}</td>
      <td class="wrap">${esc(r.label)}</td><td class="n">${fmt(r.count)}</td></tr>`).join('')}</tbody></table></div>`;
}

function caItems(run) {
  const weak = (run.items || []).filter(x => x.unknown_attribution || x.low_confidence || CA_WEAK.includes(x.status)
    || x.break?.group === 'operator');
  if (!weak.length) return '<div class="empty">Слабых случаев нет.</div>';
  return `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Телефон</th><th>Состояние</th><th>Почему</th><th class="hide-m">Цитата</th></tr></thead><tbody>
    ${weak.slice(0, 80).map(x => {
      const quote = Object.values(x.stages || {}).map(s => s.quote).find(Boolean);
      return `<tr><td class="num nw">${esc(phone(x.phone) || x.phone || '—')}</td><td>${esc(CA_SOURCE[x.source] || x.source || '—')}${x.low_confidence ? ' · низкая уверенность' : ''}</td>
        <td class="wrap">${esc(x.break?.label || x.error || (x.unknown_attribution ? 'Неизвестная атрибуция' : '—'))}</td><td class="hide-m wrap">${esc(quote || '')}</td></tr>`;
    }).join('')}</tbody></table></div>${weak.length > 80 ? `<p class="hint">Показаны первые 80 из ${fmt(weak.length)}.</p>` : ''}`;
}

function caLeadSheets(sheet) {
  if (!sheet) return '';
  const rows = sheet.projects || [];
  return `<section class="card blk"><div class="card-h"><h2>Сверка с таблицами прозвона</h2></div>
    ${sheet.status === 'ready' ? `<p>Связано ${fmt(sheet.matched)} номеров. В нескольких проектах ${fmt(sheet.ambiguous)}. Без совпадения ${fmt(sheet.absent)}.</p>
    <p class="hint">В базе ${fmt(sheet.sheet_contacts)} уникальных номеров, ${fmt(sheet.duplicate_rows)} повторов номера в строках и ${fmt(sheet.invalid_rows)} строк без распознанного телефона. Проверено ${esc(fmtWhen(sheet.checked_at))}.</p>
    <div class="tbl-wrap"><table class="tbl"><thead><tr><th>Проект</th><th>В базе</th><th>Связано со звонками</th><th>Живой разговор</th><th>Без дозвона</th></tr></thead><tbody>
    ${rows.map(project => `<tr><td>${esc(project.title)}</td><td class="n">${fmt(project.contacts)}</td><td class="n">${fmt(project.matched)}</td><td class="n">${fmt(project.live)}</td><td class="n">${fmt(project.no_contact)}</td></tr>`).join('')}
    </tbody></table></div>` : '<p class="hint">Таблица обзвона пока недоступна.</p>'}</section>`;
}

function caAutomaticResult(run) {
  const result = run.result;
  const {script, states, gaps, base} = result.quality;
  const full = states.complete || 0;
  const review = states.review || 0;
  const metrics = [
    ['Уникальных номеров прозвонено', base.contacts], ['Есть подтверждённый разговор', base.reached],
    ['Дозвона не было', base.not_reached], ['Исход неясен по записи', base.technical],
    ['Повторный недозвон', base.repeated_no_contact], ['Неверный номер', base.wrong_number],
    ['Клиент отрицает заявку', base.no_request], ['Явно неактуально', base.not_relevant]
  ];
  const examples = (run.items || []).filter(item => {
    const check = item.stages?.script_check;
    return check && (['complete', 'review'].includes(check.state) || check.lead?.length || check.warnings?.length);
  });
  return `<section class="card blk"><div class="card-h"><h2>Соблюдение скрипта</h2><span class="hint">${esc(script)}</span></div>
    <p>Все шаги найдены в ${fmt(full)} из ${fmt(full + review)} звонков, пригодных для проверки. Всего звонков ${fmt(result.total)}.</p>
    ${result.quality.topics ? `<p class="hint">${Object.entries(result.quality.topics).map(([name, n]) => `${esc(name)} — ${fmt(n)}`).join(' · ')}</p>` : ''}
    ${result.quality.packaging_pending ? `<p>Для проверки звонков по картонной упаковке нужен скрипт проекта. Распознано звонков по теме ${fmt(result.quality.packaging_pending)}.</p>` : ''}
    ${Object.entries(result.quality.directions || {}).map(([name, n]) => `<p>${esc(name)} · все шаги ${fmt(n.complete || 0)}, проверить ${fmt(n.review || 0)}, разговор остановлен ${fmt(n.stopped || 0)}.</p>`).join('')}
    <p class="hint">${Object.entries(states).map(([key, n]) => `${esc(CA_SCRIPT_STATES[key] || key)} — ${fmt(n)}`).join(' · ')}</p>
    ${Object.keys(gaps).length ? `<ul>${Object.entries(gaps).sort((a, b) => b[1] - a[1]).map(([step, n]) => `<li>${esc(step)} · не найдено в ${fmt(n)} звонках</li>`).join('')}</ul>` : ''}
    ${Object.entries(result.quality.warnings || {}).map(([name, n]) => `<p>${esc(name)} · ${fmt(n)} звонков.</p>`).join('')}
    </section>
    <section class="card blk"><div class="card-h"><h2>Качество прозвоненной базы</h2></div>
    ${base.talk_recorded == null ? '' : `<p>Mango зафиксировал время разговора по ${fmt(base.talk_recorded)} номерам.</p>`}
    ${base.reach_pct == null ? '' : `<p>Подтверждённая доля дозвона — ${esc(base.reach_pct)} % от прозвоненных номеров.</p>`}
    <div class="tbl-wrap"><table class="tbl"><tbody>${metrics.map(([name, n]) => `<tr><td>${esc(name)}</td><td class="n">${fmt(n)}</td></tr>`).join('')}</tbody></table></div>
    ${Object.entries(base.criteria || {}).map(([label, n]) => `<p>${esc(label)} · ${fmt(n)} номеров.</p>`).join('')}
    <p class="hint">Повторные попытки объединены по номеру внутри дня. Для одного номера могут быть отмечены несколько признаков.</p></section>
    ${caLeadSheets(result.quality.sheet_base)}
    ${examples.length ? `<section class="card blk"><div class="card-h"><h2>Звонки и подтверждения</h2></div>
    ${examples.map(item => {
      const check = item.stages.script_check;
      const evidence = [...(check.warnings || []), ...(check.lead || []), ...(check.found || [])].map(step => `<li>${step.at == null ? '' : `${Math.floor(step.at / 60)}:${String(step.at % 60).padStart(2, '0')} · `}${esc(step.label)} · ${esc(step.quote)}</li>`).join('');
      return `<details><summary>Звонок №${esc(item.call_id)} · ${esc(CA_SCRIPT_STATES[check.state])}</summary>
        ${check.missing?.length ? `<p>Проверить ${esc(check.missing.join(', '))}</p>` : ''}${evidence ? `<ul>${evidence}</ul>` : ''}</details>`;
    }).join('')}</section>` : ''}`;
}

function caResult(run) {
  const r = run.result || {};
  if (r.quality) return caAutomaticResult(run);
  if (r.empty) return `<div class="empty">${esc(r.empty === true ? 'За выбранный период звонков нет.' : r.empty || 'За выбранный период звонков нет.')}</div>`;
  const c = r.coverage || {};
  const done = (c.existing_transcripts || 0) + (c.transcribed || 0) + (c.robot_transcripts || 0);
  let i = 0;
  return `<div class="kpis">${[
    {key: 'ca-total', label: 'Звонков за период', value: r.total || 0},
    {key: 'ca-leads', label: 'Лидов отмечено в CRM', value: r.crm_leads || 0, sub: 'только отметки людей'},
    {key: 'ca-audio', label: 'С расшифровкой', value: done, sub: `из ${fmt(c.calls || 0)}`},
    {key: 'ca-unknown', label: 'Неизвестная атрибуция', value: r.unknown_attribution || 0}].map(k => kpiHtml(k, i++)).join('')}</div>
    <section class="card funnel-card blk" data-css="--i:${i++}"><div class="card-h"><h2>Воронка по этапам разговора</h2></div>
      <p class="hint">Этапы определяются по расшифровке; лид — только фактическая отметка в CRM. «Неизвестно» не считается отказом или пропуском.</p>
      ${caFunnel(r)}</section>
    <div class="grid2 even">
      <section class="card blk" data-css="--i:${i++}"><div class="card-h"><h2>Покрытие аудио</h2></div>${caCoverage(r)}</section>
      <section class="card blk" data-css="--i:${i++}"><div class="card-h"><h2>Где и почему оборвалось</h2></div>${caReasons(r)}</section>
    </div>
    ${r.comparison_note ? `<p class="hint blk" data-css="--i:${i++}">${esc(r.comparison_note)}</p>` : ''}
    ${r.comparison ? `<p class="hint blk" data-css="--i:${i++}">Сверка с CRM: отмечено лидов ${fmt(r.comparison.crm)}, разбор дошёл до согласия ${fmt(r.comparison.analysis)}, совпало ${fmt(r.comparison.both)}; только в CRM ${fmt(r.comparison.crm_only)}, только в разборе ${fmt(r.comparison.analysis_only)}.</p>` : ''}
    ${r.model_leads_ignored ? `<p class="hint blk" data-css="--i:${i++}">Итоги робота/модели «интерес» (${fmt(r.model_leads_ignored)}) лидами CRM не считались.</p>` : ''}
    <section class="card blk" data-css="--i:${i++}"><div class="card-h"><h2>Слабые и неизвестные случаи</h2><span class="hint">проверять вручную</span></div>${caItems(run)}</section>`;
}

async function caCounts(view) {
  const box = $('[data-ca-counts]', view);
  if (!box) return;
  const op = $('[data-ca-op]', view)?.value;
  const start = $('[data-ca-start]', view)?.value;
  const end = $('[data-ca-end]', view)?.value;
  box.innerHTML = '<p class="hint">Считаю звонки и лиды…</p>';
  try {
    const d = await api('/api/callcenter/analysis/counts' + qs({operator: op, start, end}));
    if (!d.configured) { box.innerHTML = `<div class="empty">${esc(d.empty || 'Оператор не настроен.')}</div>`; return; }
    box.innerHTML = `<div class="minis m4"><div class="mini"><span>Звонков</span><b>${fmt(d.calls)}</b></div>
      <div class="mini"><span>Лидов в CRM</span><b>${fmt(d.leads)}</b></div>
      <div class="mini"><span>Связано с CRM</span><b>${fmt(d.known_attribution)}</b></div>
      <div class="mini"><span>Неизвестная атрибуция</span><b>${fmt(d.unknown_attribution)}</b></div></div>
      ${d.empty ? `<p class="hint">${esc(d.empty)}</p>` : ''}`;
  } catch (e) {
    box.innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

async function callAnalysisView(view, arg) {
  clearTimeout(S.caTimer);
  if (arg?.startsWith(':analysis:lead:')) return caLeadHistory(view, arg.slice(15));
  const findingRoute = /^:analysis:findings(?::(\d{4}-\d{2}-\d{2}):(\d{4}-\d{2}-\d{2}))?$/.exec(arg || '');
  const operatorRoute = /^:analysis:operator:(olga|oksana)(?::(\d{4}-\d{2}-\d{2}):(\d{4}-\d{2}-\d{2}))?$/.exec(arg || '');
  if (arg === ':analysis' || findingRoute || operatorRoute)
    return caFindingsView(view, operatorRoute?.[2] || findingRoute?.[1], operatorRoute?.[3] || findingRoute?.[2], operatorRoute?.[1] || '');
  const runId = arg && arg.startsWith(':analysis:') ? Number(arg.slice(10)) : 0;
  if (runId) return caRun(view, runId);
  const d = await loading(view, api('/api/callcenter/analysis'), 'page');
  if (!view.isConnected) return;
  const initial = (d.operators || []).find(o => o.configured) || (d.operators || [])[0] || {};
  setHead({title: 'Разбор звонков', back: () => go('callcenter')});
  view.innerHTML = `<div class="page">
    <div data-ca-dashboard></div>
    <section class="card blk"><div class="card-h"><h2>Разобрать звонки</h2><span class="hint">период по Москве</span></div>
      <div class="row">
        <label class="field">Оператор<select data-ca-op>${(d.operators || []).map(o => `<option value="${esc(o.id)}"${o.id === initial.id ? ' selected' : ''}${o.configured ? '' : ' data-empty="1"'}>${esc(o.display || o.name)}${o.configured ? '' : ' — не настроен'}</option>`).join('')}</select></label>
        <label class="field">Начало<input type="date" data-ca-start value="${esc(d.default_start)}" max="${esc(d.default_end)}"></label>
        <label class="field">Конец<input type="date" data-ca-end value="${esc(d.default_end)}" max="${esc(d.default_end)}"></label>
      </div>
      <div class="row"><button class="btn primary" type="button" data-ca-run>Разобрать звонки</button>
        <span class="hint">до ${d.max_days} дней; повторный запуск за тот же период не дублируется</span></div>
      <div data-ca-counts data-css="margin-top:14px"></div>
    </section>
    <section class="card blk" data-css="--i:1"><div class="card-h"><h2>Сохранённые разборы</h2><span class="hint">${fmt((d.runs || []).length)}</span></div>
      ${(d.runs || []).length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Оператор</th><th>Период</th><th>Состояние</th><th class="n hide-m">Звонков</th><th class="hide-m">Создан</th><th></th></tr></thead><tbody>
        ${d.runs.map(r => `<tr><td>${esc(caOperatorName(d.operators, r.operator))}${r.automatic ? '<div class="s">Авторазбор</div>' : ''}</td><td class="nw">${esc(r.period)}</td>
          <td>${esc(CA_STATUS[r.status] || r.status)}${r.stage ? `<div class="s">${esc(r.stage)}</div>` : ''}</td>
          <td class="n hide-m">${fmt(r.total)}</td><td class="hide-m nw">${esc(fmtWhen(r.created_at))}</td>
          <td class="nw"><button class="btn small" type="button" data-ca-open="${r.id}">Открыть</button></td></tr>`).join('')}</tbody></table></div>`
        : '<div class="empty">Разборов пока нет.</div>'}</section>
  </div>`;
  $('[data-ca-op]', view)?.addEventListener('change', () => caCounts(view));
  $('[data-ca-start]', view)?.addEventListener('change', () => caCounts(view));
  $('[data-ca-end]', view)?.addEventListener('change', () => caCounts(view));
  $$('[data-ca-open]', view).forEach(btn => btn.addEventListener('click', () => go('callcenter', ':analysis:' + btn.dataset.caOpen)));
  $('[data-ca-run]', view)?.addEventListener('click', async e => {
    const btn = e.currentTarget;
    busy(btn, true);
    try {
      const r = await api('/api/callcenter/analysis', {method: 'POST', body: {
        operator: $('[data-ca-op]', view).value, start: $('[data-ca-start]', view).value, end: $('[data-ca-end]', view).value}});
      toast(r.reused ? 'Этот разбор уже есть: открываю' : 'Разбор запущен');
      go('callcenter', ':analysis:' + r.id);
    } catch (err) { busy(btn, false); toast(err.message, true); }
  });
  await Promise.all([caCounts(view), caDashboard(view)]);
}

async function caRun(view, id) {
  clearTimeout(S.caTimer);
  let run;
  try { run = await api('/api/callcenter/analysis/' + id); }
  catch (e) { if (view.isConnected) view.innerHTML = errCard(e.message); return; }
  if (!view.isConnected) return;
  const back = () => go('callcenter', ':analysis:stats');
  setHead({title: 'Разбор · ' + (run.operator?.display || run.operator?.name || ''), back,
    actions: run.status === 'done' ? `<button class="btn" type="button" data-ca-save>${run.saved ? 'Сохранено' : 'Сохранить в CRM'}</button>
      ${run.can_telegram ? `<button class="btn primary" type="button" data-ca-tg>${run.telegram ? 'Отправлено в Telegram' : 'Отправить в Telegram'}</button>` : ''}`
      : run.status === 'failed' ? '<button class="btn" type="button" data-ca-retry>Повторить</button>' : ''});
  const live = run.status === 'queued' || run.status === 'running';
  view.innerHTML = `<div class="page">
    <section class="card blk"><div class="card-h"><h2>${esc(run.operator?.display || run.operator?.name || 'Разбор')}</h2>
      <span class="hint">${esc(run.period)} · ${esc(CA_STATUS[run.status] || run.status)}${run.source === 'robot_calls' ? ' · журнал робота' : run.source === 'calls' ? ' · журнал Mango' : ''}</span></div>
      <div class="row"><span class="chip">${esc(run.stage || '')}</span>${live ? `<span class="hint">${fmt(run.processed)} из ${fmt(run.total)}</span>` : ''}
        <span class="hint">запуск: ${esc(run.author || '—')} · ${esc(fmtWhen(run.created_at))}</span>
        <span class="sp"></span><button class="btn small" type="button" data-ca-refresh>Обновить</button></div>
      ${live ? `<p class="hint">Обработка идёт в фоне. Страницу можно закрыть: повторный запуск не создаст второй разбор.</p>` : ''}
      ${run.error ? `<p class="hint bad-text">${esc(run.error)}</p>` : ''}
      ${run.telegram_at ? `<p class="hint">Сводка отправлена в Telegram ${esc(fmtWhen(run.telegram_at))}.</p>` : ''}
    </section>
    <div data-ca-result>${run.status === 'done' ? caResult(run) : '<div class="empty">Ждём обработку звонков…</div>'}</div>
  </div>`;
  $('[data-ca-refresh]', view)?.addEventListener('click', () => caRun(view, id));
  $('[data-ca-retry]', view)?.addEventListener('click', async e => {
    busy(e.currentTarget, true);
    try {
      const r = await api('/api/callcenter/analysis', {method: 'POST', body: {
        operator: run.operator?.id, start: run.start, end: run.end}});
      toast('Повтор запущен'); go('callcenter', ':analysis:' + r.id);
    } catch (err) { busy(e.currentTarget, false); toast(err.message, true); }
  });
  $('[data-ca-save]', view)?.addEventListener('click', async e => {
    busy(e.currentTarget, true);
    try { await api(`/api/callcenter/analysis/${id}/save`, {method: 'POST', body: {}}); toast('Разбор сохранён в CRM'); caRun(view, id); }
    catch (err) { busy(e.currentTarget, false); toast(err.message, true); }
  });
  $('[data-ca-tg]', view)?.addEventListener('click', async e => {
    busy(e.currentTarget, true);
    try { await api(`/api/callcenter/analysis/${id}/telegram`, {method: 'POST', body: {}}); toast('Сводка отправлена'); caRun(view, id); }
    catch (err) { busy(e.currentTarget, false); toast(err.message, true); }
  });
  if (live) S.caTimer = setTimeout(() => caRun(view, id), 2500);
}


function caMetric(m) {
  return `${m.percent == null ? '—' : esc(m.percent) + ' %'} (${fmt(m.count)} из ${fmt(m.denominator)})`;
}
function caEvidenceTime(n) {
  if (n == null) return '';
  if (!Number.isFinite(Number(n))) return esc(String(n)) + ' · ';
  const t = Math.floor(Number(n)); return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')} · `;
}
function caAttempt(r, focused = false) {
  const check = r.check || {};
  const evidence = (check.decisions || []).flatMap(d => (d.evidence || []).map(e => `<li>${esc(d.project || '')} · ${esc(d.reason || '')} · ${caEvidenceTime(e.at)}${esc(e.quote || '')}</li>`)).join('');
  const coaching = (check.coaching || []).map(c => `<li>${esc(c.step || '')}: ${esc(c.advice || '')} · ${caEvidenceTime(c.at)}${esc(c.quote || '')}</li>`).join('');
  const outcomes = (check.decisions || []).map(d => `<li>${esc(d.project || '')}: ${esc({qualified: 'Квалифицирован', reusable: 'Для повторного использования', competitor: 'Конкурент', pending: 'Ожидает проверки'}[d.category] || d.category || 'ожидает проверки')} · ${esc(d.reason || '')}${d.next_step ? ' · ' + esc(d.next_step_text || 'согласован следующий шаг') : ''}</li>`).join('');
  return `<details id="ca-call-${esc(r.call_id || r.item_id)}"${focused ? ' open' : ''}><summary>${esc(fmtWhen(r.at))} · ${esc({olga:'Ольга',oksana:'Оксана'}[r.operator] || r.operator)} · звонок №${esc(r.call_id || '—')} · ${esc(CA_SCRIPT_STATES[check.state] || r.status)}</summary>
    <p>${esc(r.tab || 'Проект не установлен')} · база ${esc(r.project || 'не установлена')}${r.base ? ' · дата базы ' + esc(r.base) : ''} · ${esc(CA_SOURCE[r.source] || r.source || 'нет расшифровки')} ${r.error ? ' · ' + esc(r.error) : ''}${r.semantic?.status === 'failed' ? ' · смысловая проверка недоступна' : r.semantic?.status === 'retry' ? ' · смысловая проверка ожидает повтора' : r.semantic?.status === 'done' ? ' · смысловая проверка завершена' : r.semantic?.status === 'processing' ? ' · смысловая проверка выполняется' : (r.semantic?.status === 'ineligible' || !check.live || !check.decisions?.length) ? ' · недостаточно данных для смысловой проверки' : ' · смысловая проверка ожидается'}</p>
    ${r.phone ? `<a href="#callcenter/:analysis:lead:${encodeURIComponent(r.phone)}" data-go="callcenter" data-arg=":analysis:lead:${encodeURIComponent(r.phone)}">История номера ${esc(phone(r.phone) || r.phone)}</a> · ` : ''}<a href="#callcenter/:analysis:${r.run_id}" data-go="callcenter" data-arg=":analysis:${r.run_id}">Открыть разбор №${r.run_id}</a>
    ${r.sheet_competitor ? `<p>Отметка оператора в исходной таблице: ${esc(r.sheet_competitor.marker)} · ${esc(r.sheet_competitor.tab)}.</p>` : ''}${outcomes ? `<p>Решения по направлениям:</p><ul>${outcomes}</ul>` : ''}${evidence ? `<p>Подтверждения:</p><ul>${evidence}</ul>` : ''}${coaching ? `<p>Рекомендации оператору:</p><ul>${coaching}</ul>` : ''}</details>`;
}
function caBreakdown(rows) {
  return `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Группа</th><th>Попыток</th><th>Номеров</th><th>Дозвон</th><th>Квалификация</th><th>Следующий шаг</th></tr></thead><tbody>${rows.map(r => `<tr><td>${esc(r.name)}</td><td>${fmt(r.attempts)}</td><td>${fmt(r.phones)}</td><td>${caMetric(r.reached)}</td><td>${caMetric(r.qualified)}</td><td>${caMetric(r.next_step)}</td></tr>`).join('')}</tbody></table></div>`;
}
function caFindingCall(e) {
  const arg = e.phone ? `:analysis:lead:${encodeURIComponent(e.phone)}:call:${encodeURIComponent(e.call_id || '')}` : `:analysis:${encodeURIComponent(e.run_id || '')}`;
  return `<a href="#callcenter/${esc(arg)}" data-go="callcenter" data-arg="${esc(arg)}">Звонок ${esc(e.call_id || e.run_id)}</a>`;
}
function caFindingExample(e) {
  return `<div><p><b>Пример${e.date ? ' · ' + esc(e.date) : ''}:</b> ${esc(e.issue)}<br>«${esc(e.quote)}» ${caEvidenceTime(e.at)}· ${caFindingCall(e)}</p>
    <p><b>Предложение:</b> ${esc(e.proposal)}${e.measure ? `<br><b>Как проверить:</b> ${esc(e.measure)}` : ''}</p></div>`;
}
function caAdviceFilters(d) {
  const selected = d.filters || {};
  const option = (value, label, chosen) => `<option value="${esc(value)}"${value === chosen ? ' selected' : ''}>${esc(label)}</option>`;
  const includeSelected = (values, chosen) => chosen && !values.includes(chosen) ? [chosen, ...values] : values;
  const projects = includeSelected(d.options?.projects || [], selected.project);
  const bases = includeSelected(d.options?.bases || [], selected.base);
  return `<div class="ca-advice-filters">
    <label class="field">С<input type="date" data-advice-start value="${esc(d.start)}"></label>
    <label class="field">По<input type="date" data-advice-end value="${esc(d.end)}"></label>
    <label class="field">Проект<select data-advice-project>${option('', 'Все проекты', selected.project || '')}${projects.map(v => option(v, v, selected.project)).join('')}</select></label>
    <label class="field">База<select data-advice-base>${option('', 'Все базы', selected.base || '')}${bases.map(v => option(v, v, selected.base)).join('')}</select></label>
    <button class="btn" type="button" data-advice-apply>Показать</button>
  </div>`;
}
function caAdviceRoute(operator, start, end) {
  const scope = operator ? `:analysis:operator:${operator}` : ':analysis:findings';
  return /^\d{4}-\d{2}-\d{2}$/.test(start || '') && /^\d{4}-\d{2}-\d{2}$/.test(end || '')
    ? `${scope}:${start}:${end}` : scope;
}
function caAdviceSwitch(operator, start, end) {
  return [['', 'Вся команда'], ['olga', 'Ольга'], ['oksana', 'Оксана']].map(([id, name]) => {
    const arg = caAdviceRoute(id, start, end);
    return `<a href="#callcenter/${esc(arg)}" data-advice-switch="${id}"${operator === id ? ' aria-current="page"' : ''}>${name}</a>`;
  }).join('');
}
function caAdviceIntro(operator) {
  const name = {olga: 'Ольги', oksana: 'Оксаны'}[operator];
  return `<p>Анализ разговоров колл-центра</p><h2>${name ? `Работа ${name}` : 'Что улучшить в работе'}</h2><span>${name ? 'Персональный разбор за выбранный период' : 'Рекомендации по выбранному периоду и команде'}</span>`;
}
function caPersonalSection(title, part, kind) {
  if (!part || part.status !== 'supported') return `<section class="ca-advice-panel ca-personal-panel"><h2>${esc(title)}</h2><p class="ca-advice-why">${esc(part?.observation || 'Недостаточно подтверждённых данных для вывода по этому разделу.')}</p></section>`;
  return `<section class="ca-advice-panel ca-personal-panel"><h2>${esc(title)}</h2>
    <p><b>Что произошло</b><br>${esc(part.observation || '')}</p>
    <p><b>Что исправить</b><br>${esc(part.action || '')}</p>
    <p><b>Как проверить</b><br>${esc(part.test || '')}</p>${caAdviceSources(part.evidence, kind)}</section>`;
}
function caAdviceSources(evidence, kind = '') {
  const list = Array.isArray(evidence) ? evidence : [];
  if (!list.length) return '';
  return `<details class="ca-advice-sources"><summary>Звонки и подтверждения (${fmt(list.length)})</summary><ul>${list.map(e => {
    const details = kind === 'base' ? (e.attribution === 'matched' ? [e.project, e.base].filter(Boolean) : ['Источник базы не подтверждён'])
      : kind === 'script' ? [e.project, e.step].filter(Boolean) : [];
    return `<li>${details.length ? `<span>${details.map(esc).join(' · ')}</span><br>` : ''}«${esc(e.quote || '')}» ${caEvidenceTime(e.at)}${caFindingCall(e)}</li>`;
  }).join('')}</ul></details>`;
}
function caAdviceAction(action, index) {
  return `<article class="ca-advice-action"><div class="ca-advice-number">${index + 1}</div><div>
    <h3>${esc(action.title || 'Предложение')}</h3>
    ${action.diagnosis ? `<p class="ca-advice-why">${esc(action.diagnosis)}</p>` : ''}
    ${action.action ? `<p><b>Что изменить</b><br>${esc(action.action)}</p>` : ''}
    ${action.test ? `<p><b>Как проверить</b><br>${esc(action.test)}</p>` : ''}
    ${caAdviceSources(action.evidence)}
  </div></article>`;
}
function caAdviceArchive(items) {
  if (!items?.length) return '';
  return `<details class="ca-advice-archive"><summary>Исторический пример</summary>${items.map(h => {
    const arg = `:analysis:findings:${encodeURIComponent(h.start)}:${encodeURIComponent(h.end)}`;
    return `<p><b>${esc(h.project_name)} · ${esc(h.start)} — ${esc(h.end)}</b><br>Тип затруднения отмечен у ${fmt(h.leads)} разных номеров; приведённый пример относится к конкретному звонку.</p>${h.evidence.map(caFindingExample).join('')}<p><a href="#callcenter/${arg}" data-go="callcenter" data-arg="${arg}">Выводы за тот период</a></p>`;
  }).join('')}</details>`;
}
function caAdviceBody(d, operator = '') {
  const state = d.advisor || {status: 'unavailable'};
  const brief = state.brief || {};
  const archive = operator ? '' : caAdviceArchive(d.historical_findings);
  if (operator && state.status === 'ready') {
    const review = brief.operator_review;
    if (!review) return '<section class="ca-advice-panel"><h2>Персональный разбор пока недоступен</h2><p>Дождитесь обновления анализа для этого сотрудника.</p></section>';
    return `${caPersonalSection('Работа по скрипту', review.script, 'script')}${caPersonalSection('Качество базы', review.base, 'base')}
      ${(brief.limitations || []).length ? `<p class="ca-advice-limit">${brief.limitations.map(x => esc(x)).join(' ')}</p>` : ''}`;
  }
  if (state.status === 'ready' && brief.status === 'insufficient') {
    return `<section class="ca-advice-panel"><h2>Пока недостаточно данных</h2><p>${esc(brief.summary || 'Для надёжного вывода нужно больше подтверждённых разговоров.')}</p>${(brief.limitations || []).map(x => `<p class="hint">${esc(x)}</p>`).join('')}</section>${archive}`;
  }
  if (state.status === 'ready' && brief.status === 'ready') {
    const actions = Array.isArray(brief.actions) ? brief.actions.slice(0, 3) : [];
    return `<section class="ca-advice-summary"><div class="ca-advice-eyebrow">Главный вывод</div><p>${esc(brief.summary || 'Вывод сформирован')}</p></section>
      ${actions.length ? `<section class="ca-advice-panel"><div class="ca-advice-section-head"><h2>Что можно улучшить</h2><span>${fmt(actions.length)} ${actions.length === 1 ? 'предложение' : 'предложения'}</span></div>${actions.map(caAdviceAction).join('')}</section>` : ''}
      ${(brief.limitations || []).length ? `<p class="ca-advice-limit">${brief.limitations.map(x => esc(x)).join(' ')}</p>` : ''}${archive}`;
  }
  if (state.status === 'unavailable') return `<section class="ca-advice-panel"><h2>Вывод пока недоступен</h2><p>Источник анализа не вернул состояние. Попробуйте обновить страницу позже.</p></section>${archive}`;
  if (state.status === 'error') return `<section class="ca-advice-panel"><h2>Не удалось подготовить вывод</h2><p>${esc(state.message || 'Повторите попытку позже.')}</p><button class="btn" type="button" data-advice-retry>Проверить снова</button></section>${archive}`;
  const label = state.status === 'stale' ? 'Обновляем выводы' : state.status === 'running' ? 'Анализируем разговоры' : 'Готовим выводы';
  return `<section class="ca-advice-panel" aria-live="polite"><h2>${label}</h2><p>${esc(state.message || 'Обычно это занимает несколько минут. Страницу можно оставить открытой.')}</p></section>${archive}`;
}
async function caFindingsView(view, start, end, operator = '') {
  clearTimeout(S.caTimer);
  setHead({title: 'Выводы агента', back: () => go('callcenter'),
    actions: '<button class="btn" type="button" data-go="callcenter" data-arg=":analysis:stats">Статистика и журнал</button>'});
  view.innerHTML = `<div class="page ca-advice"><div class="ca-advice-intro" data-advice-intro>${caAdviceIntro(operator)}</div>
    <nav class="ca-advice-people" aria-label="Выбрать сотрудника" data-advice-switches>${caAdviceSwitch(operator, start, end)}</nav>
    <section class="ca-advice-filter-card" aria-label="Период и фильтры"><div data-advice-filters>Загружаем фильтры…</div></section><div data-advice-content aria-live="polite"><section class="ca-advice-panel"><h2>Загружаем данные</h2></section></div></div>`;
  let serial = 0;
  let active = {start, end, operator};
  const filterBox = $('[data-advice-filters]', view), content = $('[data-advice-content]', view);
  const switches = $('[data-advice-switches]', view), intro = $('[data-advice-intro]', view);
  const selectedInputs = () => ({start: $('[data-advice-start]', filterBox)?.value || active.start,
    end: $('[data-advice-end]', filterBox)?.value || active.end,
    project: $('[data-advice-project]', filterBox)?.value ?? active.project ?? '',
    base: $('[data-advice-base]', filterBox)?.value ?? active.base ?? ''});
  switches?.addEventListener('click', e => {
    const link = e.target.closest('[data-advice-switch]');
    if (!link) return;
    e.preventDefault();
    const next = link.getAttribute('data-advice-switch');
    if (!['', 'olga', 'oksana'].includes(next)) return;
    active = {...selectedInputs(), operator: next};
    const arg = caAdviceRoute(next, active.start, active.end);
    history.replaceState(null, '', '#callcenter/' + arg);
    S.arg = arg;
    switches.innerHTML = caAdviceSwitch(next, active.start, active.end);
    intro.innerHTML = caAdviceIntro(next);
    content.innerHTML = '<section class="ca-advice-panel" aria-live="polite"><h2>Загружаем данные</h2></section>';
    refresh(true);
  });
  async function refresh(resetFilters = false) {
    clearTimeout(S.caTimer);
    const seq = ++serial;
    try {
      const d = await api('/api/callcenter/analysis/insights' + qs(active));
      if (!view.isConnected || seq !== serial) return;
      if ((d.filters?.operator || '') !== (active.operator || '')) throw new Error('Данные выбранного сотрудника пока недоступны.');
      if (resetFilters) {
        active = {start: d.start, end: d.end, ...(d.filters || {})};
        if (switches) switches.innerHTML = caAdviceSwitch(active.operator || '', active.start, active.end);
        filterBox.innerHTML = caAdviceFilters(d);
        $('[data-advice-apply]', filterBox)?.addEventListener('click', () => {
          active = {...selectedInputs(), operator: active.operator || ''};
          content.innerHTML = '<section class="ca-advice-panel" aria-live="polite"><h2>Загружаем данные</h2></section>';
          refresh(true);
        });
      }
      content.innerHTML = caAdviceBody(d, active.operator);
      $('[data-advice-retry]', content)?.addEventListener('click', () => refresh(false));
      if (['waiting', 'queued', 'running', 'stale'].includes(d.advisor?.status)) S.caTimer = setTimeout(() => { if (view.isConnected) refresh(false); }, 30000);
    } catch (e) {
      if (view.isConnected && seq === serial) {
        content.innerHTML = `<section class="ca-advice-panel"><h2>Не удалось загрузить выводы</h2><p>${esc(e.message)}</p><button class="btn" type="button" data-advice-retry>Повторить</button></section>`;
        $('[data-advice-retry]', content)?.addEventListener('click', () => refresh(resetFilters));
      }
    }
  }
  await refresh(true);
}
async function caDashboard(view, start, end, project, base, operator) {
  const box = $('[data-ca-dashboard]', view); if (!box) return;
  try {
    const d = await api('/api/callcenter/analysis/insights' + qs({start, end, project, base, operator}));
    if (!box.isConnected) return;
    box.innerHTML = `<section class="card blk"><div class="card-h"><h2>Автоматический мониторинг · Ольга и Оксана</h2></div>
      <div class="row"><label class="field">С<input type="date" data-ci-start value="${esc(d.start)}"></label><label class="field">По<input type="date" data-ci-end value="${esc(d.end)}"></label></div><div class="row">
        <label class="field">Проект<select data-ci-project><option value="">Все проекты</option>${d.options.projects.map(x => `<option value="${esc(x)}"${d.filters.project === x ? ' selected' : ''}>${esc(x)}</option>`).join('')}</select></label>
        <label class="field">База<select data-ci-base><option value="">Все базы</option>${d.options.bases.map(x => `<option value="${esc(x)}"${d.filters.base === x ? ' selected' : ''}>${esc(x)}</option>`).join('')}</select></label>
        <label class="field">Оператор<select data-ci-operator><option value="">Оба оператора</option>${d.options.operators.map(x => `<option value="${esc(x.id)}"${d.filters.operator === x.id ? ' selected' : ''}>${esc(x.name)}</option>`).join('')}</select></label><button class="btn" data-ci-apply>Показать</button></div>
      ${d.export_status ? `<p>Выгрузка карточек: ${esc(d.export_status.status)} · ${esc(d.export_status.message || "")} ${d.export_status.checked_at ? "· " + esc(fmtWhen(d.export_status.checked_at)) : ""}</p>` : ""}
      <p>За период ${fmt(d.current.attempts)} попыток, ${fmt(d.current.phones)} уникальных номеров; без номера ${fmt(d.current.no_phone)}.</p>
      <p>Дозвон ${caMetric(d.current.reached)} · соответствуют критериям продукта ${caMetric(d.current.qualified)} · согласован следующий шаг ${caMetric(d.current.next_step)}.</p><p class="hint">Категории карточек: квалифицированных по категории ${fmt(d.current.category_counts.qualified)}, для повторной работы ${fmt(d.current.category_counts.reusable)}, конкурентов ${fmt(d.current.category_counts.competitor)}, ожидают проверки ${fmt(d.current.category_counts.pending)}. Категории учитываются по направлениям: один номер может попасть в несколько категорий. Число карточек не означает подтверждённую выгрузку в таблицу. Номеров без подтверждённой категории: ${fmt(d.current.unclassified_phones)} из ${fmt(d.current.phones)}, включая недозвоны и случаи без установленной темы.</p>
      <p class="hint">Дозвон — от уникальных номеров; квалификация — от номеров с подтверждённым живым разговором, следующий шаг — от квалифицированных номеров по тому же направлению. Данные по расшифровке, а не отметки лидов в CRM. Повторные звонки одному номеру не увеличивают число номеров.</p>
      <p>Предыдущий сопоставимый период ${esc(d.previous_start)} — ${esc(d.previous_end)}: ${d.previous.attempts ? `${fmt(d.previous.attempts)} попыток, ${fmt(d.previous.phones)} номеров; дозвон ${caMetric(d.previous.reached)}, квалификация ${caMetric(d.previous.qualified)}, следующий шаг ${caMetric(d.previous.next_step)}` : 'нет данных'}.</p>
      <p class="hint">Неподтверждённые разговоры и отсутствующие записи не считаются отказом. Привязка к таблице при неоднозначности не выполняется.</p></section>
      <p><button class="btn primary" type="button" data-go="callcenter" data-arg=":analysis:findings">Выводы агента</button></p>
      <section class="card blk"><h2>По дням</h2><p class="hint">Номера уникальны внутри каждого дня; при повторном звонке в другой день номер появится снова.</p><div class="tbl-wrap"><table class="tbl"><thead><tr><th>День</th><th>Попыток</th><th>Номеров</th><th>Живой разговор</th><th>Соответствуют критериям</th><th>Согласован следующий шаг</th></tr></thead><tbody>${d.daily.map(x => `<tr><td>${esc(x.day)}</td><td>${fmt(x.attempts)}</td><td>${fmt(x.phones)}</td><td>${caMetric(x.reached)}</td><td>${caMetric(x.qualified)}</td><td>${caMetric(x.next_step)}</td></tr>`).join('')}</tbody></table></div></section>
      <section class="card blk"><h2>Полнота данных по попыткам</h2><p>Подтверждён живой диалог с распознанными ролями: ${fmt(d.current.coverage.dialogue)} из ${fmt(d.current.attempts)}; нет записи, данных недостаточно или обработка не завершена: ${fmt(d.current.coverage.missing_or_unclear)}; недозвон или автоответчик: ${fmt(d.current.coverage.no_answer_or_machine)}.</p><p class="hint">Категории попыток не пересекаются. Если доступно мало диалогов, предполагаемые пропуски критериев и причины изменения показателей — гипотезы, требующие проверки.</p></section>
      <section class="card blk"><h2>Проекты</h2>${caBreakdown(d.projects)}<h2>Базы</h2>${caBreakdown(d.bases)}<h2>Операторы</h2>${caBreakdown(d.operators)}<h2>Проект · база · оператор</h2>${caBreakdown(d.combinations.map(x => ({...x, name: `${x.project} · ${x.base} · ${x.operator}`})))}</section>
      <section class="card blk"><h2>Наблюдаемые причины</h2>${d.reasons.length ? `<ul>${d.reasons.map(r => `<li>${esc(r.reason)} · ${fmt(r.leads)} номеров; ранее ${d.previous.attempts ? fmt(r.previous) : 'нет данных'} ${r.evidence.map(e => `<a href="#callcenter/:analysis:lead:${encodeURIComponent(e.phone)}:call:${e.call_id}" data-go="callcenter" data-arg=":analysis:lead:${encodeURIComponent(e.phone)}:call:${e.call_id}">звонок ${e.call_id}</a> ${e.quote ? caEvidenceTime(e.quote_at) + esc(e.quote) : ''}`).join(' · ')}</li>`).join('')}</ul>` : '<p>Подтверждённых причин пока нет.</p>'}</section>
      <section class="card blk"><h2>Все попытки (${fmt(d.attempts.length)})</h2><div data-ci-attempts></div></section>`;
    caPagedAttempts($('[data-ci-attempts]', box), d.attempts);
    $('[data-ci-apply]', box)?.addEventListener('click', () => caDashboard(view, $('[data-ci-start]', box).value, $('[data-ci-end]', box).value, $('[data-ci-project]', box).value, $('[data-ci-base]', box).value, $('[data-ci-operator]', box).value));
  } catch (e) { if (box.isConnected) box.innerHTML = errCard(e.message); }
}
async function caLeadHistory(view, route) {
  const [number, marker, target] = route.split(':');
  clearTimeout(S.caTimer);
  setHead({title: 'История номера', back: () => go('callcenter', ':analysis')});
  try {
    const d = await loading(view, api('/api/callcenter/analysis/insights/lead/' + encodeURIComponent(number)), 'page');
    if (view.isConnected) view.innerHTML = `<div class="page"><section class="card blk"><h2>${esc(phone(d.phone) || d.phone)}</h2>${d.stop_contact ? '<p class="bad-text"><b>Не звонить: клиент запретил контакт.</b></p>' : ''}<p>Все попытки: ${fmt(d.attempts.length)}</p><div data-ci-history></div></section></div>`;
    if (view.isConnected) caPagedAttempts($('[data-ci-history]', view), d.attempts, marker === 'call' ? target : null);
  } catch (e) { if (view.isConnected) view.innerHTML = errCard(e.message); }
}

function caPagedAttempts(box, rows, focused = null) {
  if (!box) return;
  if (!rows.length) { box.innerHTML = '<p>Попыток нет.</p>'; return; }
  const index = focused ? rows.findIndex(r => String(r.call_id) === String(focused)) : -1;
  let end = 50;
  const render = () => {
    const pinned = index >= end ? caAttempt(rows[index], true) : '';
    box.innerHTML = pinned + rows.slice(0, end).map(r => caAttempt(r, focused && String(r.call_id) === String(focused))).join('')
      + (end < rows.length ? `<button class="btn" data-ci-more>Показать ещё (${fmt(rows.length - end)})</button>` : '');
    $('[data-ci-more]', box)?.addEventListener('click', () => { end += 50; render(); });
  };
  render();
  if (index >= 0) document.getElementById('ca-call-' + focused)?.scrollIntoView({block: 'center'});
}
