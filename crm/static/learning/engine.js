/* Pure exercise state machine. No DOM, fetch, production APIs, clocks or LLM calls.
   ES5 deliberately: also executable in the socket-free Duktape test bench. */
(function (root) {
  'use strict';
  var VERSION = 1;
  function copy(x) { return JSON.parse(JSON.stringify(x)); }
  function text(x) { return String(x == null ? '' : x).replace(/\s+/g, ' ').trim(); }
  function same(a, b) { return text(a).toLowerCase() === text(b).toLowerCase(); }
  function find(list, id) { for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i]; return null; }
  function day(iso, delta) { var d = new Date(iso + 'T12:00:00Z'); d.setUTCDate(d.getUTCDate() + delta); return d.toISOString().slice(0, 10); }
  function fixtures(seed, role, clock) {
    var own = role === 'manager' ? 'anna' : 'boris', other = own === 'anna' ? 'boris' : 'anna';
    var metric = 8 + (seed % 13), metrics = {}, periods = ['today', '7', '30', 'month'];
    for (var i = 0; i < periods.length; i++) metrics[periods[i]] = {anna: {result: metric + i * 19, dials: 50 + metric + i * 33}, boris: {result: metric + i * 19 + 7, dials: 80 + metric + i * 41}};
    var call = {id: 'call-1', manager: own, client: 'client-1', company: 'Учебный Кедр', at: day(clock.today, -1), seconds: 185,
      conclusion: 'Клиент согласился получить предложение, но дата следующего разговора не согласована.', answer: 'callback_missing',
      transcript: ['Менеджер: Пришлю описание предложения.', 'Клиент: Да, пришлите, посмотрю.', 'Менеджер: Спасибо, хорошего дня.']};
    var weekly = copy(call); weekly.id = 'call-weekly'; weekly.manager = 'boris'; weekly.at = clock.week_end;
    return {
      managers: [{id: 'anna', name: 'Анна Учебная'}, {id: 'boris', name: 'Борис Учебный'}],
      clients: [{id: 'client-1', company: 'Учебный Кедр', contact: 'Учебный контакт А', code: 'УЧ-001'}, {id: 'client-2', company: 'Учебный Кедр · другой клиент', contact: 'Учебный контакт Б', code: 'УЧ-002'}, {id: 'client-3', company: 'Учебный Лён', contact: 'Учебный контакт В', code: 'УЧ-003'}],
      calls: [call, weekly, {id: 'call-2', manager: role === 'manager' ? own : other, client: 'client-2', company: 'Учебный Кедр · другой клиент', at: day(clock.today, -1), seconds: 192, conclusion: 'Встреча согласована на конкретное время.', answer: 'meeting_agreed', transcript: ['Клиент: Подтверждаю встречу в четверг в десять.']}, {id: 'call-3', manager: own, client: 'client-3', company: 'Учебный Лён', at: day(clock.today, -12), seconds: 75, conclusion: 'Клиент отказался от предложения.', answer: 'refusal', transcript: ['Клиент: Спасибо, предложение не нужно.']}],
      deals: [{id: 'deal-1', client: 'client-1', company: 'Учебный Кедр', manager: own, stage: 'messenger', stageName: 'Telegram или почта', stalled: true, inn: '0000000000', history: [{kind: 'materials', text: 'Отправлены материалы', at: day(clock.today, -10)}], next_step: null}, {id: 'deal-2', client: 'client-2', company: 'Учебный Кедр · другая сделка', manager: role === 'manager' ? own : other, stage: 'contract', stageName: 'Договор', stalled: true, inn: '0000000001', history: [{kind: 'contract_sent', text: 'Договор отправлен', at: day(clock.today, -8)}], next_step: 'Связаться в пятницу'}],
      tasks: [{id: 'task-overdue', client: 'client-1', manager: 'boris', due_date: day(clock.today, -2), due_time: '10:00', original_date: day(clock.today, -2), original_time: '10:00', note: 'Клиент ждёт обсуждения предложения.', context: 'proposal_waits', call: 'call-1'}, {id: 'task-today', client: 'client-3', manager: 'boris', due_date: clock.today, due_time: '16:00', original_date: clock.today, original_time: '16:00', note: 'Уточнить удобное время.', context: 'today'}, {id: 'task-anna', client: 'client-2', manager: 'anna', due_date: day(clock.today, -3), due_time: '12:00', original_date: day(clock.today, -3), original_time: '12:00', note: 'Другой менеджер.', context: 'other'}],
      reports: [{id: 'report-personal', kind: 'day_manager', title: 'Итог дня менеджера', from: day(clock.today, -1), to: day(clock.today, -1), manager: own, text: 'Отправка предложения согласована. В следующем звонке согласуйте дату продолжения.', calls: ['call-1']}, {id: 'report-weekly', kind: 'weekly', title: 'Сводка за неделю', from: clock.week_start, to: clock.week_end, manager: 'boris', text: 'Борису стоит обсудить согласование следующего разговора.', calls: ['call-weekly']}, {id: 'report-other', kind: 'weekly', title: 'Сводка за неделю', from: day(clock.today, -14), to: day(clock.today, -8), manager: 'anna', text: 'Другой период и сотрудник.', calls: ['call-2']}],
      metrics: metrics, outcomes: [], documents: []
    };
  }
  function refs(s) {
    return {targetClient: 'client-1', targetDeal: 'deal-1', targetManager: s.role === 'manager' ? 'anna' : 'boris', targetCall: s.lessonId === 'd5' ? 'call-weekly' : 'call-1', personalReport: 'report-personal', weeklyReport: 'report-weekly', overdueTask: 'task-overdue', createdTask: s.createdTask, createdDocument: s.createdDocument,
      targetCompany: 'Учебный Кедр', targetInn: '0000000000', targetMetric: s.data.metrics['7'].boris.result,
      callbackDate: s.clock.callback.due_date, callbackTime: s.clock.callback.due_time, moveDate: s.clock.move.due_date, moveTime: s.clock.move.due_time,
      callbackMoscowDate: s.clock.callback.moscow_date, moveMoscowDate: s.clock.move.moscow_date,
      overdueDate: day(s.clock.today, -2), reportDate: day(s.clock.today, -1), weekStart: s.clock.week_start, weekEnd: s.clock.week_end, callDate: day(s.clock.today, -1), timezone: s.clock.timezone,
      expectedNote: 'Обсудить предложение ' + s.clock.callback.due_date + ' в ' + s.clock.callback.due_time};
  }
  function value(s, x) { var r = refs(s); return Object.prototype.hasOwnProperty.call(r, x) ? r[x] : x; }
  function interpolate(t, s) { return String(t).replace(/\{\{(\w+)\}\}/g, function (_, k) { return String(value(s, k)); }); }
  function interpolateDisplay(t,s){var r=refs(s);return String(t).replace(/\{\{(\w+)\}\}/g,function(_,k){var v=r[k];if(k==='timezone')return root.CRMText?root.CRMText.zone(v):v;return root.CRMText?root.CRMText.datesInText(v===undefined?k:v):String(v===undefined?k:v);});}
  function sameNote(a,b){var ad=String(a).match(/\b\d{4}-\d{2}-\d{2}\b/g)||[],bd=String(b).match(/\b\d{4}-\d{2}-\d{2}\b/g)||[];if(ad.length&&(ad.length!==bd.length||ad.some(function(d,i){return d!==bd[i];})))return false;return same(root.CRMText?root.CRMText.datesInText(a):a,root.CRMText?root.CRMText.datesInText(b):b);}
  function allowed(lesson, ctx) { return !!ctx && !!lesson && ['manager', 'director'].indexOf(ctx.role) >= 0 && ctx.role === lesson.role && (!lesson.module || ctx.modules.indexOf(lesson.module) >= 0); }
  function start(lesson, ctx, clock, id, seed) {
    if (!allowed(lesson, ctx) || lesson.kind !== 'practice') throw new Error('Этот практический урок недоступен.');
    if (!clock || !/^\d{4}-\d{2}-\d{2}$/.test(clock.today) || !clock.callback || !clock.move) throw new Error('Не удалось получить дату начала попытки.');
    return {version: VERSION, lessonVersion: lesson.version, lessonId: lesson.id, role: ctx.role, scope: ctx.scope, id: id,
      seed: seed, clock: copy(clock), step: 0, completed: false, revision: 0,
      ui: {section: lesson.guide[0].page, period: 'today', manager: '', selectedClient: null, selectedDeal: null, selectedCall: null, selectedTask: null, selectedReport: null, selectedPerson: null, draft: {}, form: null},
      data: fixtures(seed, ctx.role, clock), events: [], feedback: ''};
  }
  function restore(saved, lesson, ctx) {
    if (!allowed(lesson, ctx) || !saved || saved.version !== VERSION || saved.lessonVersion !== lesson.version || saved.lessonId !== lesson.id || saved.scope !== ctx.scope || saved.role !== ctx.role || !saved.id || !saved.clock || !saved.data || !saved.ui || !Array.isArray(saved.events)) return null;
    if (saved.step < 0 || saved.step > lesson.steps.length || saved.step % 1 || saved.completed !== (saved.step === lesson.steps.length) || saved.events.length !== saved.step || saved.revision < 0) return null;
    for (var i = 0; i < saved.events.length; i++) if (saved.events[i].type !== lesson.steps[i].action) return null;
    return copy(saved);
  }
  function apply(lesson, ctx, old, action) {
    var s = restore(old, lesson, ctx);
    if (!s) throw new Error('Сохранённая попытка повреждена или относится к другому входу. Начните заново.');
    if (!action || typeof action.type !== 'string') throw new Error('Некорректное действие.');
    var st = lesson.steps[s.step], e = st && st.expected || {}, r = refs(s), error = '', task, row;
    function bad(message) { if(root.CRMText){message=root.CRMText.datesInText(message);message=message.split(s.clock.timezone).join(root.CRMText.zone(s.clock.timezone));}s.feedback = message; s.revision++; return {state: s, ok: false, advanced: false, feedback: message}; }
    function expected(k) { return value(s, e[k]); }
    if (s.completed) return bad('Урок уже пройден. Для новой ситуации выберите «Пройти заново».');
    if (action.type === 'draft') { if (text(action.key).length > 30 || text(action.value).length > 2000) return bad('Слишком длинное поле.'); s.ui.draft[action.key] = text(action.value); s.revision++; return {state: s, ok: true, advanced: false, feedback: ''}; }
    if (action.type === 'show_form') { s.ui.form = ['outcome', 'note', 'contract', 'move'].indexOf(action.form) >= 0 ? action.form : null; if (action.form === 'outcome') s.ui.formOutcome = action.outcome === 'not_interested' ? 'not_interested' : 'callback'; s.revision++; return {state: s, ok: true, advanced: false, feedback: ''}; }
    if (action.type === 'navigate') {
      if (['queue', 'callbacks', 'deals', 'calls', 'reports', 'summary', 'staff', 'contracts'].indexOf(action.section) < 0 || (ctx.role === 'manager' && ['summary', 'staff'].indexOf(action.section) >= 0) || (action.section === 'contracts' && ctx.modules.indexOf('contracts') < 0)) return bad('Этот учебный раздел недоступен.');
      s.ui.section = action.section; s.ui.form = null;
      if (st.action !== 'open_section') { s.revision++; return {state: s, ok: true, advanced: false, feedback: ''}; }
      action = {type: 'open_section', section: action.section};
    }
    if (action.type === 'set_period') s.ui.period = action.period;
    if (action.type === 'set_manager') s.ui.manager = action.manager;
    if (action.type === 'filter_deals') s.ui.dealFilter = action.filter;
    if (action.type !== st.action) return bad('Сейчас: ' + st.title);
    switch (action.type) {
    case 'visit_page':
      if(action.page!==expected('page')||(e.arg&&action.arg!==expected('arg')))error='Откройте вкладку, указанную в подсказке.';
      else {s.ui.section=action.page;s.ui.arg=action.arg||null;}
      break;
    case 'close_review':if(s.ui.selectedCall!==expected('call')||action.call!==expected('call'))error='Сначала откройте указанный разбор.';break;
    case 'close_deal':if(s.ui.selectedDeal!==expected('deal')||action.deal!==expected('deal'))error='Сначала откройте нужную сделку.';break;
    case 'close_card':if(s.ui.selectedTask!==expected('task')||s.ui.taskContext!==expected('task')||action.task!==expected('task'))error='Откройте историю нужного перезвона.';break;
    case 'set_period': if (action.period !== expected('period')) error = 'Выберите период «7 дней», затем повторите действие.'; break;
    case 'set_manager': if (action.manager !== expected('manager')) error = 'Выберите Бориса Учебного: сейчас выбран другой сотрудник.'; break;
    case 'filter_deals': if (action.filter !== expected('filter')) error = 'Нужен список «Зависшие», а не все сделки.'; break;
    case 'select_client':
      if (action.client !== expected('client') || !find(s.data.clients, action.client)) error = 'Это другой клиент. Нужен «Учебный Кедр», код УЧ-001.';
      else { s.ui.selectedClient = action.client; s.ui.section = 'queue'; }
      break;
    case 'save_outcome':
      if (s.ui.selectedClient !== expected('client') || action.client !== expected('client')) error = 'Сохраните итог в карточке УЧ-001, а не другого клиента.';
      else if (action.outcome !== expected('outcome')) error = expected('outcome') === 'no_answer' ? 'Клиент не ответил: выберите «Не взял трубку». «Отказ» означает, что он отказался от предложения.' : 'Для договорённости нужен итог «Перезвонить».';
      else if (e.due_date && (action.due_date !== expected('due_date') || action.due_time !== expected('due_time'))) error = 'Дата или время не совпадают с договорённостью. В CRM: ' + r.callbackDate + ' ' + r.callbackTime + ' (' + r.timezone + ').';
      else if (e.comment && text(action.comment).length < 4) error = 'Добавьте комментарий о том, что нужно обсудить предложение.';
      if (!error) {
        s.data.outcomes.push({id: 'outcome-' + (s.data.outcomes.length+1), client: action.client, outcome: action.outcome, comment: text(action.comment), due_date: action.due_date || null, due_time: action.due_time || null});
        if (action.outcome === 'callback') { s.createdTask = 'task-created'; s.data.tasks.push({id: s.createdTask, client: action.client, manager: r.targetManager, due_date: action.due_date, due_time: action.due_time, original_date: action.due_date, original_time: action.due_time, note: text(action.comment)}); }
        s.ui.form = null; s.ui.draft = {};
      }
      break;
    case 'verify_outcome':
      row = find(s.data.outcomes, 'outcome-1'); if (!row || row.client !== r.targetClient || row.outcome !== 'no_answer' || s.ui.selectedClient !== r.targetClient) error = 'В истории нужного клиента пока нет отметки «Не взял трубку».'; break;
    case 'open_section': if (action.section !== expected('section')) error = 'Откройте раздел «' + (expected('section') === 'contracts' ? 'Договоры' : expected('section') === 'reports' ? 'Отчёты' : 'Перезвоны') + '».'; break;
    case 'select_task':
      task = find(s.data.tasks, action.task);
      if (!task || action.task !== expected('task') || task.manager !== r.targetManager || (s.role === 'director' && s.ui.manager !== r.targetManager)) error = 'Это другая задача или сотрудник. Проверьте клиента, срок и менеджера.';
      else { s.ui.selectedTask = action.task; s.ui.section = 'callbacks'; if (e.open_context) s.ui.taskContext = action.task; }
      break;
    case 'move_task':
      task = find(s.data.tasks, action.task);
      if (!task || action.task !== expected('task') || s.ui.selectedTask !== task.id) error = 'Перенесите уже созданную задачу, не создавайте новую.';
      else if (action.due_date !== expected('due_date') || action.due_time !== expected('due_time')) error = 'Новое время в CRM: ' + r.moveDate + ' ' + r.moveTime + '. Исходная дата больше не подходит.';
      else { task.due_date = action.due_date; task.due_time = action.due_time; s.ui.form = null; }
      break;
    case 'verify_task':
      task = find(s.data.tasks, s.createdTask);
      var count = s.data.tasks.filter(function (t) { return t.client === r.targetClient && t.manager === r.targetManager; }).length;
      if (!task || task.original_date !== r.callbackDate || task.original_time !== r.callbackTime || task.due_date !== r.moveDate || task.due_time !== r.moveTime || !task.note || count !== 1) error = 'Должна остаться одна задача: первоначальный срок, новый срок и комментарий.';
      break;
    case 'select_deal':
      row = find(s.data.deals, action.deal);
      if (!row || action.deal !== expected('deal') || row.manager !== r.targetManager || ((lesson.id === 'd4'||e.filter) && s.ui.dealFilter !== (e.filter||'stalled'))) error = 'Откройте нужную сделку «Учебный Кедр» в заданном списке.';
      else { s.ui.selectedDeal = row.id; s.ui.section = 'deals'; }
      break;
    case 'save_note':
      row = find(s.data.deals, action.deal);
      if (!row || s.ui.selectedDeal !== expected('deal') || action.deal !== expected('deal')) error = 'Заметка должна относиться к выбранной сделке УЧ-001.';
      else if (!sameNote(action.note, expected('note'))) error = 'Запишите следующий шаг полностью: «' + r.expectedNote + '».';
      else { row.history.push({kind: 'note', text: text(action.note), at: s.clock.today}); s.ui.form = null; }
      break;
    case 'verify_history':
      row = find(s.data.deals, r.targetDeal);
      if (!row || row.history.filter(function (h) { return h.kind === 'note' && sameNote(h.text, r.expectedNote); }).length !== 1 || s.ui.selectedDeal !== r.targetDeal) error = 'Откройте историю нужной сделки и проверьте сохранённую заметку.';
      break;
    case 'prepare_contract':
      if (ctx.modules.indexOf('contracts') < 0) error = 'Модуль «Договоры» отключён.';
      else if (s.ui.selectedDeal !== expected('deal') || action.deal !== expected('deal')) error = 'Начните создание договора из нужной сделки.';
      else if (!same(action.company, expected('company')) || action.inn !== expected('inn') || action.confirmed !== true) error = 'Сверьте учебные реквизиты: «Учебный Кедр», ИНН 0000000000, и подтвердите проверку.';
      else { s.createdDocument = 'document-created'; s.data.documents.push({id: s.createdDocument, number: 'УЧ-' + s.id.slice(-6), deal: action.deal, company: action.company, inn: action.inn, training: true, sent: false}); s.ui.form = null; }
      break;
    case 'select_document':
      row = find(s.data.documents, action.document);
      if (!row || action.document !== expected('document') || row.deal !== r.targetDeal || row.sent || !row.training || s.ui.section !== 'contracts') error = 'В учебном журнале откройте документ, связанный с нужной сделкой.';
      else s.ui.selectedDocument = row.id;
      break;
    case 'select_call':
      row = find(s.data.calls, action.call);
      if (!row || action.call !== expected('call') || row.manager !== r.targetManager || (['m5', 'd2'].indexOf(lesson.id) >= 0 && s.ui.period !== '7') || (lesson.id === 'd2' && s.ui.manager !== r.targetManager) || ((lesson.id === 'd5'||e.person) && s.ui.selectedPerson !== r.targetManager) || (e.period && s.ui.period!==expected('period')) || (e.manager && s.ui.manager!==expected('manager'))) error = 'Выбран другой разговор, менеджер или период. Нужен звонок УЧ-001 за 7 дней.';
      else { s.ui.selectedCall = row.id; s.ui.section = 'calls'; if (lesson.id === 'd5') { s.ui.period = '7'; s.ui.manager = r.targetManager; } }
      break;
    case 'answer_review':
      row = find(s.data.calls, s.ui.selectedCall);
      if (!row || row.id !== r.targetCall || action.answer !== expected('answer') || row.answer !== action.answer) error = 'Сверьтесь с открытым разбором: дата следующего разговора не согласована, встреча не подтверждена.';
      break;
    case 'select_report':
      row = find(s.data.reports, action.report);
      if (!row || action.report !== expected('report') || (lesson.id === 'm5' && (row.manager !== r.targetManager || row.to !== r.reportDate)) || (lesson.id === 'd5' && (row.from !== r.weekStart || row.to !== r.weekEnd))) error = 'Это другой отчёт или период. Проверьте заголовок, даты и сотрудника.';
      else { s.ui.selectedReport = row.id; s.ui.section = 'reports'; }
      break;
    case 'answer_metric':
      if (s.ui.period !== '7' || s.ui.manager !== r.targetManager || text(action.value) !== String(expected('value'))) error = 'Число не совпадает со сводкой Бориса за 7 дней. Проверьте оба фильтра и показатель «Разговоров с результатом».';
      break;
    case 'open_task_context':
      task = find(s.data.tasks, action.task);
      if (!task || action.task !== expected('task') || s.ui.selectedTask !== task.id) error = 'Откройте историю выбранного просроченного перезвона.';
      else s.ui.taskContext = task.id;
      break;
    case 'answer_task':
      task = find(s.data.tasks, s.ui.selectedTask);
      if (!task || s.ui.taskContext !== task.id || action.client !== expected('client') || action.due_date !== expected('due_date') || action.due_time !== expected('due_time') || task.due_date !== task.original_date || action.answer !== expected('answer')) error = 'Сверьте клиента УЧ-001, исходный срок просроченной задачи и историю: клиент ждёт обсуждения предложения.';
      break;
    case 'answer_stalled':
      row = find(s.data.deals, s.ui.selectedDeal);
      if (!row || row.id !== r.targetDeal || action.stage !== expected('stage') || action.last_event !== expected('last_event') || action.next_step !== expected('next_step') || row.next_step !== null) error = 'По истории: этап «Telegram или почта», отправлены материалы, следующий шаг не зафиксирован. Причину не угадываем.';
      break;
    case 'select_person':
      if (action.manager !== expected('manager') || s.ui.selectedReport !== r.weeklyReport) error = 'После нужного отчёта откройте карточку Бориса Учебного.';
      else { s.ui.selectedPerson = action.manager; s.ui.section = 'staff'; }
      break;
    default: return bad('Такого учебного действия нет.');
    }
    if (error) return bad(error);
    s.events.push({type: action.type, record: action.client || action.deal || action.call || action.task || action.report || action.manager || action.document || null});
    s.step++; s.revision++; s.completed = s.step === lesson.steps.length;
    s.feedback = s.completed ? 'Урок пройден. Результат проверен на учебных данных.' : 'Верно. ' + lesson.steps[s.step].title;
    return {state: s, ok: true, advanced: true, feedback: s.feedback};
  }
  var api = {VERSION: VERSION, fixtures: fixtures, copy: copy, find: find, day: day, refs: refs, interpolate: interpolate, interpolateDisplay:interpolateDisplay, allowed: allowed, start: start, restore: restore, apply: apply};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.AveronLearningEngine = api;
}(this));
