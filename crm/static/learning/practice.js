/* Runs only in a sandboxed document with connect-src 'none'. Never imports app.js,
   its API helper, work forms, dial handlers, contracts generator or AveronGPT. */
(function () {
  'use strict';
  var E = window.AveronLearningEngine, lesson, context, state, nonce, initialized = false, hints = false;
  var root = document.getElementById('exercise');
  function esc(v) { return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]; }); }
  function el(selector) { return root.querySelector(selector); }
  function button(label, action, id, attrs) { return '<button type="button" class="btn" data-action="' + action + '"' + (id ? ' data-id="' + esc(id) + '"' : '') + (attrs || '') + '>' + esc(label) + '</button>'; }
  function client(id) { return E.find(state.data.clients, id); }
  function manager(id) { return E.find(state.data.managers, id); }
  function card(title, inner) { return '<section class="card"><h2>' + esc(title) + '</h2>' + inner + '</section>'; }
  function field(label, key, type, val) { return '<label class="field">' + esc(label) + '<input data-field="' + key + '" type="' + (type || 'text') + '" value="' + esc(val == null ? state.ui.draft[key] || '' : val) + '" maxlength="2000"></label>'; }
  function select(label, key, options, current) { return '<label class="field">' + esc(label) + '<select data-field="' + key + '"><option value="">Выберите</option>' + options.map(function (x) { return '<option value="' + esc(x[0]) + '"' + (String(x[0]) === String(current) ? ' selected' : '') + '>' + esc(x[1]) + '</option>'; }).join('') + '</select></label>'; }
  function textarea(label, key) { return '<label class="field wide">' + esc(label) + '<textarea data-field="' + key + '" rows="3" maxlength="2000">' + esc(state.ui.draft[key] || '') + '</textarea></label>'; }
  function read(key) { return el('[data-field="' + key + '"]') ? el('[data-field="' + key + '"]').value : ''; }
  function send(type, data) { var packet = data || {}; packet.type = type; packet.nonce = nonce; window.parent.postMessage(packet, '*'); }
  function emit() { send('learning:state', {state: state}); height(); }
  function height() { if (initialized) send('learning:height', {height: Math.ceil(root.scrollHeight + 24)}); }
  function act(action) {
    var result = E.apply(lesson, context, state, action); state = result.state; render(); emit();
    var feedback = el('[data-feedback]'); if (feedback) feedback.focus();
  }
  function filters() {
    var list = [['today', 'Сегодня'], ['7', '7 дней'], ['30', '30 дней'], ['month', 'Месяц']];
    return '<div class="row">' + list.map(function (x) { return button(x[1], 'set_period', x[0], ' aria-pressed="' + (state.ui.period === x[0]) + '"'); }).join('') + '</div>' + (context.role === 'director' ? select('Менеджер', 'managerFilter', [['', 'Весь отдел']].concat(state.data.managers.map(function (m) { return [m.id, m.name]; })), state.ui.manager) : '');
  }
  function outcomes() {
    var selected = state.ui.selectedClient, c = client(selected);
    if (!c) return card('Очередь', state.data.clients.map(function (c) { return '<div class="learn-record"><b>' + esc(c.company) + '</b> · ' + esc(c.code) + button('Открыть карточку', 'select_client', c.id) + '</div>'; }).join(''));
    var labels = [['no_answer', 'Не взял трубку'], ['callback', 'Перезвонить'], ['not_interested', 'Отказ']];
    var history = state.data.outcomes.filter(function (o) { return o.client === c.id; }).map(function (o) { return '<li>' + esc(o.outcome === 'no_answer' ? 'Не взял трубку — недозвон' : 'Перезвонить · ' + o.due_date + ' ' + o.due_time + ' · ' + o.comment) + '</li>'; }).join('');
    var isCallback = state.ui.formOutcome !== 'not_interested';
    var form = state.ui.form === 'outcome' ? '<div class="outform">' + (isCallback ? field('Дата (' + state.clock.timezone + ')', 'date', 'date') + field('Время', 'time', 'time') : '') + textarea('Комментарий', 'comment') + button('Сохранить', isCallback ? 'save_callback' : 'save_other') + '</div>' : '';
    return card(c.company + ' · ' + c.code, '<p>Учебный клиент. Звонить не требуется, телефония отключена.</p>' + button('Другой клиент', 'clients') + '<h3>Итог звонка</h3><div class="row">' + labels.map(function (x) { return button(x[1], 'outcome', x[0]); }).join('') + '</div>' + form) + card('История клиента', history ? '<ul>' + history + '</ul>' : '<p>Отметок пока нет.</p>') + (lesson.steps[state.step] && lesson.steps[state.step].action === 'verify_outcome' ? button('Проверить сохранённый итог', 'verify_outcome') : '');
  }
  function callbacks() {
    var rows = state.data.tasks.filter(function (t) { return context.role !== 'director' ? t.manager === 'anna' : !state.ui.manager || t.manager === state.ui.manager; });
    var html = context.role === 'director' ? select('Менеджер', 'managerFilter', state.data.managers.map(function (m) { return [m.id, m.name]; }), state.ui.manager) : '';
    var groups = [['Просрочено', function (t) { return t.due_date < state.clock.today; }], ['Сегодня', function (t) { return t.due_date === state.clock.today; }], ['Дальше', function (t) { return t.due_date > state.clock.today; }]];
    groups.forEach(function (g) {
      var items = rows.filter(g[1]); if (!items.length) return;
      html += '<h3>' + g[0] + '</h3>' + items.map(function (t) { return '<div class="learn-record"><b>' + esc(client(t.client).company) + '</b> · ' + esc(manager(t.manager).name) + '<p>' + esc(t.due_date + ' ' + t.due_time) + '</p><p>' + esc(t.note) + '</p>' + button('Позвонить · открыть карточку', 'select_task', t.id) + '</div>'; }).join('');
    });
    var t = E.find(rows, state.ui.selectedTask);
    if (t) {
      html += card('Выбранный перезвон', '<p>' + esc(client(t.client).company) + ' · ' + esc(t.due_date + ' ' + t.due_time) + '</p>' + button('Перенести', 'show_move'));
      if (state.ui.form === 'move') html += card('Перенести', '<div class="outform">' + field('Новая дата', 'moveDate', 'date') + field('Время', 'moveTime', 'time') + button('Сохранить', 'move_task', t.id) + '</div>');
      if (state.ui.taskContext === t.id) html += card('История клиента', '<p>Учебный звонок: клиент ждёт обсуждения предложения.</p><p>Исходный срок: ' + esc(t.original_date + ' ' + t.original_time) + '</p>');
    }
    if (lesson.steps[state.step] && lesson.steps[state.step].action === 'verify_task') html += button('Проверить задачу и отсутствие дубля', 'verify_task');
    return html;
  }
  function deals() {
    var d = E.find(state.data.deals, state.ui.selectedDeal);
    var list = state.data.deals.filter(function (x) { return (context.role !== 'manager' || x.manager === 'anna') && (state.ui.dealFilter !== 'stalled' || x.stalled); });
    var html = '<div class="row">' + button('В работе', 'filter_deals', 'active') + button('Зависшие', 'filter_deals', 'stalled') + '</div>' + list.map(function (x) { return card(x.company, '<p>Этап: ' + esc(x.stageName) + ' · ' + esc(manager(x.manager).name) + '</p>' + button('Открыть сделку', 'select_deal', x.id)); }).join('');
    if (!d) return html;
    html += card(d.company + ' — карточка сделки', '<p>Этап: ' + esc(d.stageName) + '</p>' + button('Заметка', 'show_note') + (context.modules.indexOf('contracts') >= 0 ? button('Составить договор', 'show_contract') : '') + '<h3>История</h3><ul>' + d.history.map(function (h) { return '<li>' + esc(h.at + ' · ' + h.text) + '</li>'; }).join('') + '</ul><p>Следующий шаг: ' + esc(d.next_step || 'Не зафиксирован') + '</p>');
    if (state.ui.form === 'note') html += card('Заметка', textarea('Следующий шаг', 'note') + button('Сохранить', 'save_note', d.id));
    if (state.ui.form === 'contract') html += card('Учебный договор', '<p>Сверьте: Учебный Кедр, ИНН 0000000000. Реквизиты вымышленные. Проверка во внешних сервисах отключена.</p>' + field('Наименование', 'company', 'text', state.ui.draft.company || d.company) + field('ИНН', 'inn', 'text', state.ui.draft.inn || d.inn) + '<label class="learn-check"><input type="checkbox" data-field="confirmed"' + (state.ui.draft.confirmed === 'true' ? ' checked' : '') + '> Реквизиты проверены</label>' + button('Сформировать учебный договор', 'prepare_contract', d.id) + '<p class="hint">В рабочей CRM «Скачать договор (.docx)» одновременно присваивает номер и пишет журнал. Здесь — встроенный просмотр без скачивания.</p>');
    if (lesson.steps[state.step] && lesson.steps[state.step].action === 'verify_history') html += button('Проверить запись в истории', 'verify_history');
    return html;
  }
  function contracts() {
    return card('Журнал договоров · только учебные', state.data.documents.length ? state.data.documents.map(function (d) { return '<div class="learn-record"><b>' + esc(d.number + ' · ' + d.company) + '</b> · сделка УЧ-001' + button('Открыть документ', 'select_document', d.id) + '</div>'; }).join('') : '<p>Учебный журнал пока пуст.</p>') + (state.createdDocument ? card('Учебный документ · не для подписания', '<p>Связан со сделкой «Учебный Кедр», УЧ-001.</p><p>ИНН 0000000000. Не занимает рабочего номера. Не отправлен клиенту.</p><p>Создание и скачивание не равны отметке «Договор отправлен»: её менеджер ставит отдельно после реальной отправки.</p>') : '');
  }
  function calls() {
    var c = E.find(state.data.calls, state.ui.selectedCall);
    var list = state.data.calls.filter(function (x) { return (context.role !== 'manager' || x.manager === 'anna') && (!state.ui.manager || x.manager === state.ui.manager) && (state.ui.period !== '7' || x.id !== 'call-3'); });
    return filters() + card('Журнал звонков', list.map(function (x) { return '<div class="learn-record"><b>' + esc(x.company) + '</b> · ' + esc(manager(x.manager).name + ' · ' + x.at) + button('Открыть разбор', 'select_call', x.id) + '</div>'; }).join('')) + (c ? card('Разбор разговора · ' + manager(c.manager).name, '<p>' + esc(c.conclusion) + '</p><details><summary>Учебная расшифровка</summary><p>' + c.transcript.map(esc).join('</p><p>') + '</p></details>') : '');
  }
  function reports() {
    var selected = E.find(state.data.reports, state.ui.selectedReport);
    return card('Отчёты', '<p>Сверяйте дату и период в заголовке. Фильтра дат в этой ленте нет.</p>' + state.data.reports.map(function (r) { return '<article class="learn-record"><b>' + esc(r.title) + '</b><p>' + esc(r.from + ' — ' + r.to + ' · ' + manager(r.manager).name) + '</p>' + button('Открыть отчёт', 'select_report', r.id) + '</article>'; }).join('')) + (selected ? card(selected.title, '<p>' + esc(selected.text) + '</p>' + (context.role === 'director' ? '<p>Теперь откройте «Сотрудники», выберите Бориса и пример звонка в карточке.</p>' : '')) : '');
  }
  function staff() {
    var person = E.find(state.data.managers, state.ui.selectedPerson);
    return card('Сотрудники', state.data.managers.map(function (m) { return '<div class="learn-record">' + esc(m.name) + button('Карточка сотрудника', 'select_person', m.id) + '</div>'; }).join('')) + (person ? card(person.name + ' · Обзор', '<p>Где теряем · 30 дней: не согласована дата продолжения.</p>' + button('Пример разговора · Учебный Кедр', 'select_call', person.id === E.refs(state).targetManager ? E.refs(state).targetCall : 'call-2')) : '');
  }
  function summary() {
    var period = state.data.metrics[state.ui.period] || state.data.metrics.today;
    var selected = state.ui.manager ? [state.ui.manager] : ['anna', 'boris'];
    return filters() + card('Сводка отдела', selected.filter(function (m) { return !!period[m]; }).map(function (m) { return '<div class="learn-record"><h3>' + esc(manager(m).name) + '</h3><p>Наборов: ' + period[m].dials + '</p><p>Разговоров с результатом: <strong>' + period[m].result + '</strong></p></div>'; }).join(''));
  }
  function question() {
    var step = lesson.steps[state.step]; if (!step) return '';
    switch (step.action) {
    case 'answer_review': return card('Проверка по открытому разбору', select('Что подтверждено?', 'answer', [['meeting_agreed', 'Встреча согласована'], ['callback_missing', 'Дата следующего разговора не согласована'], ['refusal', 'Клиент отказался']], state.ui.draft.answer) + button('Проверить ответ', 'answer_review'));
    case 'answer_metric': return card('Проверка показателя', field('Разговоров с результатом', 'metric', 'number') + button('Проверить ответ', 'answer_metric'));
    case 'answer_task': return card('Проверка контекста', select('Клиент', 'answerClient', state.data.clients.map(function (c) { return [c.id, c.company]; }), state.ui.draft.answerClient) + field('Исходная дата', 'dueDate', 'date') + field('Исходное время', 'dueTime', 'time') + select('Что известно из истории?', 'taskAnswer', [['proposal_waits', 'Клиент ждёт обсуждения предложения'], ['paid', 'Получена оплата'], ['none', 'Контекста нет']], state.ui.draft.taskAnswer) + button('Проверить ответ', 'answer_task'));
    case 'answer_stalled': return card('Проверка истории', select('Этап', 'stage', [['messenger', 'Telegram или почта'], ['contract', 'Договор'], ['paid', 'Оплата']], state.ui.draft.stage) + select('Последнее событие', 'lastEvent', [['materials', 'Отправлены материалы'], ['contract_sent', 'Договор отправлен'], ['paid', 'Оплата получена']], state.ui.draft.lastEvent) + select('Следующий шаг', 'nextStep', [['not_recorded', 'Не зафиксирован'], ['meeting', 'Назначить встречу'], ['pay', 'Ждать оплату']], state.ui.draft.nextStep) + button('Проверить ответ', 'answer_stalled'));
    default: return '';
    }
  }
  function render() {
    var routes = {queue: outcomes, callbacks: callbacks, deals: deals, contracts: contracts, calls: calls, reports: reports, staff: staff, summary: summary};
    var names = {queue: 'Очередь', callbacks: 'Перезвоны', deals: 'Сделки', calls: 'Звонки', reports: 'Отчёты', summary: 'Сводка', staff: 'Сотрудники', contracts: 'Договоры'};
    var nav = context.role === 'manager' ? ['queue', 'callbacks', 'deals', 'calls', 'reports'] : ['summary', 'calls', 'callbacks', 'deals', 'reports', 'staff'];
    if (context.modules.indexOf('contracts') >= 0) nav.push('contracts');
    root.innerHTML = '<div class="learn-exercise"><header class="card learn-task"><div class="row"><b>Учебные данные · изменения в упражнении разрешены</b><span class="sp"></span>' + button('Подсказать, где нажать', 'hint') + button('Выйти из урока', 'exit') + '</div><h1>' + esc(lesson.title) + '</h1><h3>Рабочая ситуация</h3><p>' + esc(E.interpolate(lesson.situation, state)) + '</p><details><summary>Объяснение · можно пропустить</summary><p>' + esc(lesson.adaptation || 'Выполните действие в нужной записи. Учебные изменения сохраняются только в этой попытке, не в рабочей CRM.') + '</p></details><h2>Что сделать</h2><p>' + esc(state.completed ? lesson.result : lesson.steps[state.step].title) + '</p><p>Шаг ' + Math.min(state.step + 1, lesson.steps.length) + ' из ' + lesson.steps.length + '</p><p class="learn-feedback" data-feedback role="status" tabindex="-1">' + esc(state.feedback || 'Выполните действие сами. Переход по разделам не заменяет проверку.') + '</p></header>' + '<nav class="row learn-local-nav" aria-label="Учебные разделы">' + nav.map(function (k) { return button(names[k], 'navigate', k, ' aria-current="' + (state.ui.section === k ? 'page' : 'false') + '"'); }).join('') + '</nav>' + (state.completed ? card('Пройдено', '<p>' + esc(lesson.result) + '</p><p>Записи остались только в этом упражнении.</p>' + button('К программе обучения', 'exit')) : (routes[state.ui.section] || outcomes)() + question()) + '</div>';
    if (hints) showHint();
    height();
  }
  function showHint() {
    var step = lesson.steps[state.step]; if (!step) return;
    var selectors = {select_client: '[data-action="select_client"]', save_outcome: state.ui.form === 'outcome' ? '[data-action="save_callback"], [data-field="date"], [data-field="time"], [data-field="comment"]' : '[data-action="outcome"]', open_section: '[data-action="navigate"]', select_task: '[data-action="select_task"]', move_task: state.ui.form === 'move' ? '[data-action="move_task"]' : '[data-action="show_move"]', select_deal: '[data-action="select_deal"]', save_note: state.ui.form === 'note' ? '[data-action="save_note"]' : '[data-action="show_note"]', prepare_contract: state.ui.form === 'contract' ? '[data-action="prepare_contract"]' : '[data-action="show_contract"]', select_document: '[data-action="select_document"]', set_period: '[data-action="set_period"]', set_manager: '[data-field="managerFilter"]', select_call: '[data-action="select_call"]', select_report: '[data-action="select_report"]', select_person: '[data-action="select_person"], [data-action="navigate"]', filter_deals: '[data-action="filter_deals"]'};
    var nodes = root.querySelectorAll(selectors[step.action] || '[data-action="' + step.action + '"]');
    for (var i = 0; i < nodes.length; i++) nodes[i].classList.add('learn-action-hint');
  }
  root.addEventListener('change', function (event) {
    var input = event.target, key = input.getAttribute('data-field'); if (!key || !state) return;
    if (key === 'managerFilter') { act({type: 'set_manager', manager: input.value}); return; }
    var res = E.apply(lesson, context, state, {type: 'draft', key: key, value: input.type === 'checkbox' ? String(input.checked) : input.value}); state = res.state; emit();
  });
  root.addEventListener('input', function (event) {
    var key = event.target.getAttribute('data-field'); if (!key || key === 'managerFilter' || !state) return;
    var res = E.apply(lesson, context, state, {type: 'draft', key: key, value: event.target.value}); state = res.state; emit();
  });
  root.addEventListener('click', function (event) {
    var b = event.target.closest('[data-action]'); if (!b || !state) return;
    var type = b.getAttribute('data-action'), id = b.getAttribute('data-id');
    if (type === 'hint') { hints = true; showHint(); return; }
    if (type === 'exit') { send('learning:exit'); return; }
    if (type === 'clients') { state.ui.selectedClient = null; state.revision++; render(); emit(); return; }
    if (type === 'outcome') {
      if (id === 'callback' || id === 'not_interested') { act({type: 'show_form', form: 'outcome', outcome: id}); return; }
      act({type: 'save_outcome', client: state.ui.selectedClient, outcome: id}); return;
    }
    if (type === 'save_other') { act({type: 'save_outcome', client: state.ui.selectedClient, outcome: 'not_interested', comment: read('comment')}); return; }
    if (type === 'save_callback') { act({type: 'save_outcome', client: state.ui.selectedClient, outcome: 'callback', due_date: read('date'), due_time: read('time'), comment: read('comment')}); return; }
    if (type.indexOf('show_') === 0) { act({type: 'show_form', form: {show_note: 'note', show_contract: 'contract', show_move: 'move'}[type]}); return; }
    var action = {type: type};
    var ids = {select_client: 'client', select_deal: 'deal', select_task: 'task', select_call: 'call', select_report: 'report', select_person: 'manager', select_document: 'document', open_task_context: 'task', set_period: 'period', filter_deals: 'filter', navigate: 'section'};
    if (ids[type]) action[ids[type]] = id;
    if (type === 'save_note') { action.deal = id; action.note = read('note'); }
    if (type === 'move_task') { action.task = id; action.due_date = read('moveDate'); action.due_time = read('moveTime'); }
    if (type === 'prepare_contract') { action.deal = id; action.company = read('company'); action.inn = read('inn'); action.confirmed = el('[data-field="confirmed"]').checked; }
    if (type === 'answer_review') action.answer = read('answer');
    if (type === 'answer_metric') action.value = read('metric');
    if (type === 'answer_task') { action.client = read('answerClient'); action.due_date = read('dueDate'); action.due_time = read('dueTime'); action.answer = read('taskAnswer'); }
    if (type === 'answer_stalled') { action.stage = read('stage'); action.last_event = read('lastEvent'); action.next_step = read('nextStep'); }
    act(action);
  });
  addEventListener('keydown', function (e) { if (e.key === 'Escape') send('learning:exit'); });
  addEventListener('message', function (event) {
    if (event.source !== window.parent || initialized || !event.data || event.data.type !== 'learning:init' || window.parent === window) return;
    var packet = event.data; lesson = packet.lesson; context = packet.context; nonce = packet.nonce;
    state = E.restore(packet.state, lesson, context);
    if (!state) { root.textContent = 'Попытка недоступна. Откройте урок из программы обучения.'; return; }
    initialized = true; hints = packet.withHints === true;
    if (packet.theme) document.documentElement.setAttribute('data-theme', packet.theme);
    render(); emit();
  });
  if (window.ResizeObserver) new ResizeObserver(height).observe(document.body);
  if (window.parent !== window) window.parent.postMessage({type: 'learning:ready'}, '*');
}());
