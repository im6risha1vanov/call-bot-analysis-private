/* Browser-local progress. No shared director statistics and no network fallback. */
(function (root) {
  'use strict';
  function key(ctx, lesson) {
    if (!ctx || !ctx.scope || ctx.role !== lesson.role || ['manager', 'director'].indexOf(ctx.role) < 0) throw new Error('Чужая программа обучения.');
    return 'averon-learning:v1:' + ctx.scope + ':' + ctx.role + ':' + lesson.id + ':' + lesson.version;
  }
  function read(storage, ctx, lesson) {
    try {
      var raw = storage.getItem(key(ctx, lesson)), data = raw ? JSON.parse(raw) : null;
      if (!data || data.scope !== ctx.scope || data.role !== ctx.role || data.lesson !== lesson.id || data.version !== lesson.version) return null;
      return data;
    } catch (e) { return null; }
  }
  function write(storage, ctx, lesson, data) {
    var item = {scope: ctx.scope, role: ctx.role, lesson: lesson.id, version: lesson.version, state: data.state || null, guide: data.guide || null};
    try { storage.setItem(key(ctx, lesson), JSON.stringify(item)); }
    catch (e) { throw new Error('Браузер не сохранил прогресс. Разрешите локальное хранилище и повторите. Рабочие записи не затронуты.'); }
    return item;
  }
  function status(record, lesson, ctx, engine) {
    if (!record) return 'Не начат';
    if (lesson.kind === 'guide') return record.guide && record.guide.complete ? 'Пройден' : 'В процессе';
    var state = engine.restore(record.state, lesson, ctx);
    return state ? (state.completed ? 'Пройден' : 'В процессе') : record.guide ? 'В процессе' : 'Не начат';
  }
  function advanceGuide(record, index, total, visible) {
    if (!visible || index < 0 || index >= total || Math.floor(index) !== index) throw new Error('Сначала откройте доступный блок CRM.');
    var next = JSON.parse(JSON.stringify(record)), guide = next.guide || {index: 0, seen: [], complete: false};
    if (guide.index !== index || guide.complete) throw new Error('Шаг экскурсии устарел. Откройте «Продолжить».');
    if (guide.seen.indexOf(index) < 0) guide.seen.push(index);
    guide.index = index + 1;
    guide.complete = guide.index === total && guide.seen.length === total;
    next.guide = guide;
    return next;
  }
  root.AveronLearningProgress = {key: key, read: read, write: write, status: status, advanceGuide: advanceGuide};
}(this));
