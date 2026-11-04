/* Actual engine + progress + practice event handlers on a socket-free DOM model.
   This is not a browser/layout test and does not claim to exercise CSP enforcement. */
(function () {
  'use strict';
  var E = AveronLearningEngine, P = AveronLearningProgress;
  var stats = {lessons: [], correctActions: 0, wrongAttempts: 0, restores: 0, uiLessons: 0, uiWrongAttempts: 0, uiResumes: 0, networkCalls: 0};
  function assert(ok, message) { if (!ok) throw new Error(message); }
  function ctx(lesson, scope) { return {role: lesson.role, scope: scope || 'account-company-role-a', modules: ['contracts', 'robot', 'automations']}; }
  function fresh(lesson, seed) { return E.start(lesson, ctx(lesson), TEST_CLOCK, 'attempt-' + lesson.id + '-' + seed, seed); }
  function valid(lesson, s) {
    var st = lesson.steps[s.step], out = {type: st.action}, key;
    for (key in st.expected) if (Object.prototype.hasOwnProperty.call(st.expected, key)) out[key] = E.refs(s)[st.expected[key]] === undefined ? st.expected[key] : E.refs(s)[st.expected[key]];
    if (out.comment) out.comment = 'Обсудить предложение с клиентом';
    if (st.action === 'prepare_contract') out.confirmed = true;
    if (st.action === 'open_section') { out.type = 'navigate'; out.section = st.expected.section; }
    return out;
  }
  function invalid(lesson, s, correct) {
    var bad = E.copy(correct), key;
    if (bad.type === 'navigate') { bad.section = 'deals'; if (correct.section === 'deals') bad.section = 'calls'; return bad; }
    for (key in bad) if (key !== 'type') { bad[key] = key === 'confirmed' ? false : 'wrong-record-or-value'; return bad; }
    bad.type = 'next'; return bad;
  }
  var lessons = TEST_CATALOG.lessons.filter(function (l) { return l.kind === 'practice'; });
  lessons.forEach(function (lesson) {
    var s = fresh(lesson, 43), restarted = fresh(lesson, 99), n = lesson.steps.length;
    assert(!restarted.completed && restarted.step === 0 && !restarted.data.outcomes.length && !restarted.data.documents.length, lesson.id + ': repeat must be clean');
    assert(restarted.id !== s.id, 'attempt IDs must differ');
    for (var i = 0; i < n; i++) {
      var action = valid(lesson, s), bad = E.apply(lesson, ctx(lesson), s, invalid(lesson, s, action));
      assert(!bad.advanced && bad.state.step === s.step && !bad.state.completed, lesson.id + ': wrong attempt passed step ' + i);
      stats.wrongAttempts++;
      var result = E.apply(lesson, ctx(lesson), s, action);
      assert(result.ok && result.advanced && result.state.step === i + 1, lesson.id + ': valid step ' + i + ' failed: ' + result.feedback);
      s = result.state; stats.correctActions++;
      var restored = E.restore(JSON.parse(JSON.stringify(s)), lesson, ctx(lesson));
      assert(restored && restored.step === s.step && restored.clock.today === TEST_CLOCK.today && restored.id === s.id, 'refresh/resume failed');
      assert(E.restore(s, lesson, ctx(lesson, 'other-account')) === null, 'cross-account restore');
      s = restored; stats.restores++;
    }
    assert(s.completed, lesson.id + ': not completed');
    if (lesson.id === 'm2') {
      var t = E.find(s.data.tasks, s.createdTask);
      assert(t.original_date === TEST_CLOCK.callback.due_date && t.original_time === TEST_CLOCK.callback.due_time, 'original deadline lost');
      assert(t.due_date === TEST_CLOCK.move.due_date && t.note, 'move or note lost');
      assert(s.data.tasks.filter(function (x) { return x.id === s.createdTask; }).length === 1, 'duplicate callback');
    }
    if (lesson.id === 'd5') {
      var report = E.find(s.data.reports, E.refs(s).weeklyReport), proof = E.find(s.data.calls, E.refs(s).targetCall);
      assert(proof.manager === report.manager && proof.at >= report.from && proof.at <= report.to, 'report example outside manager/period');
    }
    if (lesson.id === 'm4') assert(s.data.documents.length === 1 && s.data.documents[0].training && !s.data.documents[0].sent && s.data.documents[0].deal === 'deal-1', 'contract isolation');
    if (lesson.id === 'd3') assert(E.find(s.data.tasks, 'task-overdue').due_date === E.refs(s).overdueDate, 'director changed task');
    stats.lessons.push({id: lesson.id, result: 'passed', actions: n});
  });
  // Persist full in-progress form + frozen clock across midnight and another account.
  var lm2 = TEST_CATALOG.lessons.filter(function (l) { return l.id === 'm2'; })[0], started = fresh(lm2, 12);
  started = E.apply(lm2, ctx(lm2), started, valid(lm2, started)).state;
  started = E.apply(lm2, ctx(lm2), started, {type: 'show_form', form: 'outcome'}).state;
  started = E.apply(lm2, ctx(lm2), started, {type: 'draft', key: 'comment', value: 'Обсудить предложение'}).state;
  var memory = {}, storage = {getItem: function (k) { return memory[k] || null; }, setItem: function (k, v) { memory[k] = v; }};
  P.write(storage, ctx(lm2), lm2, {state: started});
  var record = P.read(storage, ctx(lm2), lm2), resumed = E.restore(record.state, lm2, ctx(lm2));
  assert(resumed.ui.draft.comment === 'Обсудить предложение' && resumed.ui.form === 'outcome', 'form continuation lost');
  assert(E.refs(resumed).callbackDate === TEST_CLOCK.callback.due_date, 'midnight changed deadline');
  assert(P.read(storage, ctx(lm2, 'other-company'), lm2) === null, 'cross-company progress');
  var wrongRole = ctx(lm2); wrongRole.role = 'director';
  assert(E.restore(started, lm2, wrongRole) === null, 'manager lesson accessible to other program');
  var noModule = ctx(lm2); noModule.modules = [];
  var lm4 = TEST_CATALOG.lessons.filter(function (l) { return l.id === 'm4'; })[0];
  assert(!E.allowed(lm4, noModule), 'disabled contracts accessible');
  var caught = false; try { P.write({setItem: function () { throw new Error('quota'); }}, ctx(lm2), lm2, record); } catch (e) { caught = true; }
  assert(caught, 'storage error was silently accepted');
  var tour = TEST_CATALOG.lessons.filter(function (l) { return l.id === 'd6'; })[0];
  var guideRecord = {state: null, guide: {index: 0, seen: [], complete: false}};
  var blocked = false;
  try { P.advanceGuide(guideRecord, 0, tour.guide.length, false); } catch (e) { blocked = true; }
  assert(blocked && guideRecord.guide.index === 0, 'missing target incorrectly counts');
  for (var g = 0; g < tour.guide.length; g++) {
    guideRecord = P.advanceGuide(guideRecord, g, tour.guide.length, true);
    P.write(storage, ctx(tour), tour, guideRecord);
    guideRecord = P.read(storage, ctx(tour), tour);
    assert(guideRecord.guide.index === g + 1, 'tour continuation lost');
  }
  assert(P.status(guideRecord, tour, ctx(tour), E) === 'Пройден', 'director guide not completed');
  stats.directorGuide = {steps: tour.guide.length, missingTargetRejected: true, resume: true, passed: true};

  // Actual practice.js UI controller, with an explicit small DOM adapter.
  function runUI(lesson) {
    var current = fresh(lesson, 47), c = ctx(lesson), events = {}, listeners = {}, sent = [], fields = {}, html = '', network = 0;
    var fakeRoot = {scrollHeight: 700, addEventListener: function (name, cb) { listeners[name] = cb; }, querySelector: function (selector) {
      if (selector === '[data-feedback]') return {focus: function () {}};
      var m = /data-field="([^"]+)"/.exec(selector); return m ? fields[m[1]] || null : null;
    }};
    function decode(s) { return s.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&'); }
    function parseHTML(value) {
      html = value; fields = {};
      var regex = /<(input|select|textarea)\b([^>]*\bdata-field="([^"]+)"[^>]*)>([\s\S]*?)(?:<\/\1>|(?=<)|$)/g, m;
      // Inputs have no closing tag; parse them separately.
      var input = /<input\b([^>]*\bdata-field="([^"]+)"[^>]*)>/g;
      while ((m = input.exec(value))) {
        var type = /type="([^"]+)"/.exec(m[1]), v = /value="([^"]*)"/.exec(m[1]);
        fields[m[2]] = {value: v ? decode(v[1]) : '', type: type ? type[1] : 'text', checked: /\bchecked\b/.test(m[1]), getAttribute: attr(m[2])};
      }
      regex = /<(select|textarea)\b([^>]*\bdata-field="([^"]+)"[^>]*)>([\s\S]*?)<\/\1>/g;
      while ((m = regex.exec(value))) {
        var chosen = /<option value="([^"]*)" selected/.exec(m[4]);
        fields[m[3]] = {value: m[1] === 'textarea' ? decode(m[4]) : chosen ? decode(chosen[1]) : '', type: m[1], getAttribute: attr(m[3])};
      }
    }
    function attr(key) { return function (name) { return name === 'data-field' ? key : null; }; }
    Object.defineProperty(fakeRoot, 'innerHTML', {set: parseHTML, get: function () { return html; }});
    var parent = {postMessage: function (packet) {
      var copied = JSON.parse(JSON.stringify(packet)); sent.push(copied);
      if (copied.type === 'learning:state') current = copied.state;
    }};
    var document = {getElementById: function () { return fakeRoot; }, body: {scrollHeight: 700}, documentElement: {setAttribute: function () {}}};
    var window = {AveronLearningEngine: E, parent: parent};
    function addEventListener(name, cb) { events[name] = cb; }
    function forbidden() { network++; throw new Error('A production or external network call escaped practice'); }
    var fetch = forbidden, XMLHttpRequest = forbidden, WebSocket = forbidden;
    // The checked-in UI source is evaluated, not a duplicate implementation.
    function boot(snapshot) {
      listeners = {}; events = {};
      eval(TEST_PRACTICE_SOURCE);
      events.message({source: parent, data: {type: 'learning:init', nonce: 'only-frame', lesson: lesson, context: c, state: JSON.parse(JSON.stringify(snapshot))}});
      stats.uiResumes++;
    }
    boot(current);
    assert(html.indexOf('Учебные данные') >= 0, 'practice did not initialize');
    function click(type, id) {
      var pattern = 'data-action="' + type + '"' + (id ? ' data-id="' + id + '"' : '');
      assert(html.indexOf(pattern) >= 0, lesson.id + ': missing real rendered button ' + pattern + ' at step ' + current.step);
      var b = {getAttribute: function (name) { return name === 'data-action' ? type : name === 'data-id' ? id : null; }};
      listeners.click({target: {closest: function () { return b; }}});
    }
    function fill(key, value, checkbox) {
      assert(fields[key], lesson.id + ': missing input ' + key);
      fields[key].value = value; fields[key].checked = !!checkbox;
      listeners.change({target: fields[key]});
    }
    function perform(action) {
      switch (action.type) {
      case 'save_outcome':
        if (action.outcome === 'callback') { click('outcome', 'callback'); fill('date', action.due_date); fill('time', action.due_time); fill('comment', action.comment); if (lesson.id === 'm2') boot(current); click('save_callback'); }
        else if (action.outcome === 'not_interested') { click('outcome', action.outcome); fill('comment', 'Клиент отказался'); click('save_other'); }
        else click('outcome', action.outcome);
        break;
      case 'move_task': click('show_move'); fill('moveDate', action.due_date); fill('moveTime', action.due_time); click('move_task', action.task); break;
      case 'save_note': click('show_note'); fill('note', action.note); click('save_note', action.deal); break;
      case 'prepare_contract': click('show_contract'); fill('company', action.company); fill('inn', action.inn); fill('confirmed', 'true', action.confirmed); click('prepare_contract', action.deal); break;
      case 'set_manager': fill('managerFilter', action.manager); break;
      case 'select_person': click('navigate', 'staff'); click('select_person', action.manager); break;
      case 'answer_review': fill('answer', action.answer); click('answer_review'); break;
      case 'answer_metric': fill('metric', action.value); click('answer_metric'); break;
      case 'answer_task': fill('answerClient', action.client); fill('dueDate', action.due_date); fill('dueTime', action.due_time); fill('taskAnswer', action.answer); click('answer_task'); break;
      case 'answer_stalled': fill('stage', action.stage); fill('lastEvent', action.last_event); fill('nextStep', action.next_step); click('answer_stalled'); break;
      default:
        var id = action.client || action.deal || action.call || action.task || action.report || action.manager || action.document || action.period || action.filter || action.section;
        click(action.type, id);
      }
    }
    var first = valid(lesson, current), bad = E.copy(first);
    var wrong = {select_client: 'client-2', select_deal: 'deal-2', set_period: '30', set_manager: 'anna', filter_deals: 'active', select_report: 'report-other'};
    var wrongKey = {select_client: 'client', select_deal: 'deal', set_period: 'period', set_manager: 'manager', filter_deals: 'filter', select_report: 'report'};
    bad[wrongKey[bad.type]] = wrong[bad.type];
    perform(bad); stats.uiWrongAttempts++; assert(current.step === 0 && !current.completed, lesson.id + ': UI wrong attempt passed');
    while (!current.completed) {
      var before = current.step, action = valid(lesson, current);
      // Deliberately wrong date, requisites or answer, then correct through real UI.
      if (['save_outcome', 'move_task', 'prepare_contract', 'answer_metric', 'answer_review', 'answer_task', 'answer_stalled'].indexOf(action.type) >= 0) {
        var wrongAction = E.copy(action);
        if (action.type === 'save_outcome' && action.outcome === 'callback') wrongAction.due_date = '2000-01-01';
        else if (action.type === 'save_outcome') wrongAction.outcome = 'not_interested';
        else if (action.type === 'move_task') wrongAction.due_time = '01:00';
        else if (action.type === 'prepare_contract') wrongAction.inn = '0000000001';
        else if (action.type === 'answer_metric') wrongAction.value = '0';
        else if (action.type === 'answer_stalled') wrongAction.next_step = 'pay';
        else wrongAction.answer = 'wrong';
        perform(wrongAction); stats.uiWrongAttempts++; assert(current.step === before && !current.completed, 'UI invalid answer/date passed');
      }
      perform(action);
      assert(current.step === before + 1, lesson.id + ': real controller failed step ' + before + ': ' + current.feedback);
      assert(E.restore(JSON.parse(JSON.stringify(current)), lesson, c), 'UI snapshot cannot resume');
      boot(current); assert(current.step === before + 1, 'UI reload changed step');
    }
    assert(html.indexOf('Пройдено') >= 0, 'completion not visible');
    assert(network === 0, 'network escaped'); stats.networkCalls += network; stats.uiLessons++;
    events.keydown({key: 'Escape'});
    assert(sent[sent.length - 1].type === 'learning:exit', 'Escape does not close practice');
  }
  lessons.forEach(runUI);
  return JSON.stringify(stats);
}());
