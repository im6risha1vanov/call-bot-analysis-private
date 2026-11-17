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
  const back = () => go('callcenter', ':analysis');
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
    <p>${esc(r.tab || 'Проект не установлен')} · база ${esc(r.project || 'не установлена')}${r.base ? ' · дата базы ' + esc(r.base) : ''} · ${esc(CA_SOURCE[r.source] || r.source || 'нет расшифровки')} ${r.error ? ' · ' + esc(r.error) : ''}${r.semantic?.status === 'failed' ? ' · смысловая проверка недоступна' : r.semantic?.status === 'retry' ? ' · смысловая проверка ожидает повтора' : r.semantic?.status === 'done' ? ' · смысловая проверка завершена' : ' · смысловая проверка ожидается или недоступна'}</p>
    ${r.phone ? `<a href="#callcenter/:analysis:lead:${encodeURIComponent(r.phone)}" data-go="callcenter" data-arg=":analysis:lead:${encodeURIComponent(r.phone)}">История номера ${esc(phone(r.phone) || r.phone)}</a> · ` : ''}<a href="#callcenter/:analysis:${r.run_id}" data-go="callcenter" data-arg=":analysis:${r.run_id}">Открыть разбор №${r.run_id}</a>
    ${outcomes ? `<p>Решения по направлениям:</p><ul>${outcomes}</ul>` : ''}${evidence ? `<p>Подтверждения:</p><ul>${evidence}</ul>` : ''}${coaching ? `<p>Рекомендации оператору:</p><ul>${coaching}</ul>` : ''}</details>`;
}
function caBreakdown(rows) {
  return `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Группа</th><th>Попыток</th><th>Номеров</th><th>Дозвон</th><th>Квалификация</th><th>Следующий шаг</th></tr></thead><tbody>${rows.map(r => `<tr><td>${esc(r.name)}</td><td>${fmt(r.attempts)}</td><td>${fmt(r.phones)}</td><td>${caMetric(r.reached)}</td><td>${caMetric(r.qualified)}</td><td>${caMetric(r.next_step)}</td></tr>`).join('')}</tbody></table></div>`;
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
      <p>Дозвон ${caMetric(d.current.reached)} · соответствуют критериям продукта ${caMetric(d.current.qualified)} · согласован следующий шаг ${caMetric(d.current.next_step)}.</p><p class="hint">Категории карточек: квалифицированных ${fmt(d.current.category_counts.qualified)}, для повторной работы ${fmt(d.current.category_counts.reusable)}, конкурентов ${fmt(d.current.category_counts.competitor)}, ожидают проверки ${fmt(d.current.category_counts.pending)}. Один номер может относиться к нескольким направлениям.</p>
      <p class="hint">Дозвон — от уникальных номеров; квалификация — от номеров с подтверждённым живым разговором, следующий шаг — от квалифицированных номеров по тому же направлению. Данные по расшифровке, а не отметки лидов в CRM. Повторные звонки одному номеру не увеличивают число номеров.</p>
      <p>Предыдущий сопоставимый период ${esc(d.previous_start)} — ${esc(d.previous_end)}: ${fmt(d.previous.attempts)} попыток, ${fmt(d.previous.phones)} номеров; дозвон ${caMetric(d.previous.reached)}, квалификация ${caMetric(d.previous.qualified)}, следующий шаг ${caMetric(d.previous.next_step)}.</p>
      <p class="hint">Неподтверждённые разговоры и отсутствующие записи не считаются отказом. Привязка к таблице при неоднозначности не выполняется.</p></section>
      <section class="card blk"><h2>Проекты</h2>${caBreakdown(d.projects)}<h2>Базы</h2>${caBreakdown(d.bases)}<h2>Операторы</h2>${caBreakdown(d.operators)}<h2>Проект · база · оператор</h2>${caBreakdown(d.combinations.map(x => ({...x, name: `${x.project} · ${x.base} · ${x.operator}`})))}</section>
      <section class="card blk"><h2>Наблюдаемые причины</h2>${d.reasons.length ? `<ul>${d.reasons.map(r => `<li>${esc(r.reason)} · ${fmt(r.leads)} номеров; ранее ${fmt(r.previous)} ${r.evidence.map(e => `<a href="#callcenter/:analysis:lead:${encodeURIComponent(e.phone)}:call:${e.call_id}" data-go="callcenter" data-arg=":analysis:lead:${encodeURIComponent(e.phone)}:call:${e.call_id}">звонок ${e.call_id}</a> ${e.quote ? caEvidenceTime(e.quote_at) + esc(e.quote) : ''}`).join(' · ')}</li>`).join('')}</ul>` : '<p>Подтверждённых причин пока нет.</p>'}</section>
      <section class="card blk"><h2>Повторяющиеся затруднения</h2><p class="hint">Гипотезы по нескольким клиентам, а не доказанные дефекты скрипта. Изменение следует проверить на новых разговорах и сравнить показатели до и после.</p>${d.friction.length ? d.friction.map(f => `<details><summary>${esc(f.project)} · ${esc(f.step)} · ${esc(f.issue)} (${fmt(f.leads)} номеров)</summary><p>Предложение: ${esc(f.proposal)}</p>${f.measure ? `<p>Как измерить: ${esc(f.measure)}</p>` : ''}<ul>${f.evidence.map(e => `<li>${caEvidenceTime(e.at)}${esc(e.quote)} · <a href="#callcenter/:analysis:lead:${encodeURIComponent(e.phone)}:call:${e.call_id}" data-go="callcenter" data-arg=":analysis:lead:${encodeURIComponent(e.phone)}:call:${e.call_id}">звонок ${e.call_id}</a></li>`).join('')}</ul></details>`).join('') : '<p>Повторяющихся подтверждённых наблюдений пока нет.</p>'}</section>
      <section class="card blk"><h2>Отдельно: рекомендации операторам</h2>${d.coaching.length ? `<ul>${d.coaching.map(c => `<li>${esc(c.step || '')}: ${esc(c.advice || '')} · ${caEvidenceTime(c.at)}${esc(c.quote || '')} · <a href="#callcenter/:analysis:lead:${encodeURIComponent(c.phone || '')}:call:${c.call_id}" data-go="callcenter" data-arg=":analysis:lead:${encodeURIComponent(c.phone || '')}:call:${c.call_id}">звонок ${c.call_id}</a></li>`).join('')}</ul>` : '<p>Рекомендаций пока нет.</p>'}</section>
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
