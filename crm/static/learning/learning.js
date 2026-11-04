/* Shared manager/director program. Production navigation is read-only guidance;
   every exercise lives in a separate sandbox document with no network access. */
(function () {
  'use strict';
  const E = window.AveronLearningEngine, P = window.AveronLearningProgress;
  let context = null, practice = null, guide = null, guideTimer = null;
  const saved = lesson => P.read(localStorage, context, lesson);
  const persist = (lesson, record) => P.write(localStorage, context, lesson, record);
  const selectedLesson = id => context?.lessons.find(l => l.id === id);
  const uid = () => [...crypto.getRandomValues(new Uint32Array(4))].map(n => n.toString(16)).join('-');
  function recordFor(lesson) { return saved(lesson) || {state: null, guide: null}; }
  async function freshContext(id) {
    const data = await api('/api/learning/context' + (id ? '?lesson=' + encodeURIComponent(id) : ''));
    if (context && context.scope !== data.scope) endGuide();
    context = data;
    return data;
  }
  function status(lesson) { return P.status(saved(lesson), lesson, context, E); }
  function notice(view, text) {
    const out = $('[data-learning-notice]', view);
    if (out) { out.textContent = text; out.hidden = !text; }
    else toast(text, true);
  }
  function localHint() { return '<p class="hint">Прогресс сохраняется только в этом браузере для вашего входа, компании и роли. Это не общая статистика директора. Все клиенты и звонки упражнений вымышлены.</p>'; }
  VIEWS.learning = async (view, arg) => {
    endGuide();
    practice = null;
    await freshContext(arg || null);
    if (!view.isConnected) return;
    setHead({title: 'Обучение', back: arg ? () => go('learning') : null});
    if (!arg) {
      view.innerHTML = `<div class="page">${localHint()}<p data-learning-notice role="status" hidden></p><h2>Программа ${context.role === 'director' ? 'директора' : 'менеджера'}</h2><div class="learn-catalog">${context.lessons.map(lesson => {
        const state = status(lesson);
        return `<section class="card"><span class="chip${state === 'Пройден' ? ' good' : ''}">${esc(state)}</span><h2>${esc(lesson.title)}</h2><p>${esc(lesson.result)}</p>${lesson.available ? `<button class="btn primary" type="button" data-lesson="${lesson.id}">${state === 'Не начат' ? 'Начать урок' : 'Продолжить'}</button>${state !== 'Не начат' ? ` <button class="btn" type="button" data-restart="${lesson.id}">Пройти заново</button>` : ''}` : `<p class="hint">${esc(lesson.unavailable_reason)}</p><button type="button" class="btn" disabled>Модуль недоступен</button>`}</section>`;
      }).join('')}</div></div>`;
      $$('[data-lesson]', view).forEach(b => b.onclick = () => go('learning', b.dataset.lesson));
      $$('[data-restart]', view).forEach(b => b.onclick = () => restart(selectedLesson(b.dataset.restart)).catch(e => notice(view, e.message)));
      return;
    }
    const lesson = selectedLesson(arg);
    if (!lesson?.available) throw new Error('Этот урок недоступен вашей роли или компании.');
    view.innerHTML = `<div class="page">${localHint()}<p data-learning-notice role="status" hidden></p><section class="card"><span class="chip">${esc(status(lesson))}</span><h2>${esc(lesson.title)}</h2><p>${esc(lesson.result)}</p><h3>Рабочая ситуация</h3><p>${esc(lesson.situation.includes('{{') ? 'Точные даты и время будут закреплены при начале попытки.' : lesson.situation)}</p>${lesson.adaptation ? `<p class="hint">${esc(lesson.adaptation)}</p>` : ''}<div class="row"><button type="button" class="btn" data-mode="guide">Показать, где нажать</button>${lesson.kind === 'practice' ? '<button type="button" class="btn primary" data-mode="practice">Попробовать самому</button>' : ''}<button type="button" class="btn" data-restart>Пройти заново</button><button type="button" class="ghost" data-exit>К программе</button></div></section><div data-exercise></div></div>`;
    $('[data-exit]', view).onclick = () => go('learning');
    $('[data-restart]', view).onclick = () => restart(lesson).catch(e => notice(view, e.message));
    $$('[data-mode]', view).forEach(b => b.onclick = async () => {
      try {
        await freshContext(lesson.id);
        if (b.dataset.mode === 'guide') await beginGuide(selectedLesson(lesson.id));
        else mountPractice(view, selectedLesson(lesson.id));
      } catch (e) { notice(view, e.message); }
    });
  };
  async function restart(lesson) {
    if (!lesson?.available) throw new Error('Урок недоступен.');
    if (!await askConfirm('Начать чистую учебную ситуацию? Текущая попытка этого урока будет заменена только в вашем браузере.', 'Пройти заново')) return;
    await freshContext(lesson.id);
    endGuide();
    const record = {state: lesson.kind === 'practice' ? E.start(lesson, context, context.clock, uid(), crypto.getRandomValues(new Uint32Array(1))[0]) : null, guide: null};
    persist(lesson, record);
    await go('learning', lesson.id);
    if (lesson.kind === 'practice') mountPractice($('#view'), lesson);
    else await beginGuide(lesson);
  }
  function mountPractice(view, lesson) {
    endGuide();
    let record = recordFor(lesson), state = E.restore(record.state, lesson, context);
    if (!state) { state = E.start(lesson, context, context.clock, uid(), crypto.getRandomValues(new Uint32Array(1))[0]); record.state = state; persist(lesson, record); }
    const host = $('[data-exercise]', view);
    if (!host) return;
    host.innerHTML = '<div class="row"><p class="hint">Учебный экран изолирован от телефонии, рабочих записей, уведомлений и внешних сервисов.</p><button type="button" class="btn" data-close-practice>Закрыть упражнение</button></div><iframe class="learn-frame" title="Учебное упражнение" sandbox="allow-scripts" referrerpolicy="no-referrer"></iframe>';
    const frame = $('iframe', host);
    practice = {frame, lesson, state, record, nonce: uid(), scope: context.scope};
    $('[data-close-practice]', host).onclick = () => { practice = null; host.innerHTML = ''; go('learning', lesson.id); };
    frame.src = '/learning/practice?lesson=' + encodeURIComponent(lesson.id);
  }
  addEventListener('message', event => {
    const current = practice;
    if (!current || event.source !== current.frame.contentWindow || !event.data || typeof event.data !== 'object') return;
    const packet = event.data;
    if (packet.type === 'learning:ready') {
      current.frame.contentWindow.postMessage({type: 'learning:init', nonce: current.nonce, lesson: current.lesson, context: {scope: context.scope, role: context.role, modules: context.modules}, state: current.state, theme: document.documentElement.getAttribute('data-theme')}, '*');
      return;
    }
    if (packet.nonce !== current.nonce || context.scope !== current.scope) return;
    if (packet.type === 'learning:height' && Number.isFinite(packet.height)) { current.frame.style.height = Math.max(340, Math.min(2600, packet.height)) + 'px'; return; }
    if (packet.type === 'learning:exit') { practice = null; go('learning', current.lesson.id); return; }
    if (packet.type !== 'learning:state') return;
    try {
      if (JSON.stringify(packet.state).length > 120000) throw new Error('Учебная попытка слишком велика. Начните заново.');
      const next = E.restore(packet.state, current.lesson, context);
      if (!next || next.id !== current.state.id || next.revision < current.state.revision || next.revision > current.state.revision + 1) throw new Error('Состояние попытки изменилось. Продолжите урок заново из программы.');
      const latest = saved(current.lesson);
      if (latest?.state?.id && (latest.state.id !== next.id || latest.state.revision > current.state.revision)) throw new Error('Попытка изменена в другой вкладке. Откройте «Продолжить».');
      current.state = next; current.record.state = next;
      persist(current.lesson, current.record);
    } catch (e) { notice($('#view'), e.message); practice = null; current.frame.remove(); }
  });

  function endGuide() {
    if (guide?.target) guide.target.classList.remove('learn-highlight');
    const focus = guide?.returnFocus;
    guide = null; clearTimeout(guideTimer);
    $('[data-learning-guide]')?.remove();
    if (focus?.isConnected) focus.focus({preventScroll: true});
    else ($('dialog[open] button') || $('[data-go="learning"]'))?.focus({preventScroll: true});
  }
  function addTargets() {
    const targets = {summary: {'summary-filters': '#top .bar-f'}, calls: {'call-filters': '#top .bar-f'}, callbacks: {'callback-filters': '#top .bar-f'}, deals: {'deal-filters': '#top .bar-f'}, settings: {'settings-tabs': '#view .tabs'}, robot: {'robot-tabs': '#view .tabs'}};
    Object.entries(targets[S.page] || {}).forEach(([name, selector]) => { const node = $(selector); if (node) node.dataset.learningTarget = name; });
  }
  async function beginGuide(lesson) {
    endGuide();
    const record = recordFor(lesson);
    record.guide = record.guide || {index: 0, seen: [], complete: false};
    if (record.guide.complete || record.guide.index >= lesson.guide.length) record.guide = {index: 0, seen: [], complete: false};
    persist(lesson, record);
    guide = {lesson, record, index: record.guide.index, returnFocus: document.activeElement, target: null, signature: ''};
    if (window.GPT?.tour) tourEnd();
    const step = lesson.guide[guide.index];
    await go(step.page, step.arg || null);
    showGuide(true);
  }
  function showGuide(force = false) {
    if (!guide) return;
    addTargets();
    const g = guide, step = g.lesson.guide[g.index];
    const correctPage = S.page === step.page && (!step.arg || S.arg === step.arg);
    const selector = '[data-learning-target="' + step.target + '"]';
    const modal = $('dialog[open]');
    let target = correctPage ? (window.tourFind ? tourFind(selector) : $(selector)) : null;
    if (modal && target && !modal.contains(target)) target = null; // underlying screen is inert
    const signature = [S.page, S.arg, g.index, !!target, !!modal].join(':');
    if (!force && g.signature === signature && g.target === target && g.modal === modal) return;
    g.modal = modal;
    g.signature = signature;
    if (g.target) g.target.classList.remove('learn-highlight');
    g.target = target;
    if (target) target.classList.add('learn-highlight');
    $('[data-learning-guide]')?.remove();
    const panel = document.createElement('section');
    panel.className = 'card learn-guide'; panel.dataset.learningGuide = '1'; panel.setAttribute('aria-label', 'Подсказка обучения');
    panel.innerHTML = `<div class="row"><b>${esc(g.lesson.title)} · ${g.index + 1}/${g.lesson.guide.length}</b><span class="sp"></span><button type="button" class="ghost" data-guide-close>Закрыть обучение · Esc</button></div><h2>Что сделать</h2><p>${esc(step.text)}</p><h3>Как проверить результат</h3><p>${esc(step.result)}</p>${modal && !target ? '<p role="status">Сначала закройте рабочую карточку крестиком. Обучение не закрывает её автоматически и не теряет несохранённые поля.</p>' : ''}${!target ? `<p role="status">${correctPage ? 'Нужный элемент пока не виден: список может быть пустым, карточка не открыта или данных/прав недостаточно. Откройте нужную карточку вручную.' : 'Откройте нужный раздел.'} Учебный пример доступен в режиме практики.</p>` : '<p class="hint">Нужный блок выделен рамкой. Подсказка расположена в потоке страницы и не перекрывает кнопку.</p>'}${g.lesson.omitted_guide_modules?.length ? '<p class="hint">Отключённые модули пропущены: ' + esc(g.lesson.omitted_guide_modules.join(', ')) + '.</p>' : ''}<div class="row">${target ? '<button type="button" class="btn" data-guide-focus>К выделенному блоку</button>' : ''}<button type="button" class="btn" data-guide-open>Открыть раздел «${esc(PAGES[step.page].name)}»</button>${g.lesson.kind === 'practice' ? '<button type="button" class="btn" data-guide-practice>Учебный пример</button>' : ''}<button type="button" class="btn primary" data-guide-next ${target ? '' : 'disabled'}>${g.index === g.lesson.guide.length - 1 ? 'Закончить показ' : 'Далее'}</button></div><p class="hint">Показ не засчитывает практическое упражнение. Рабочие кнопки и формы система не нажимает.</p>`;
    const view = $('#view');
    if (modal) modal.insertBefore(panel, modal.firstChild);
    else view.parentNode.insertBefore(panel, view);
    $('[data-guide-close]', panel).onclick = () => endGuide();
    $('[data-guide-focus]', panel)?.addEventListener('click', () => {
      const node = g.target; if (!node?.isConnected) return;
      node.scrollIntoView({block: 'center', behavior: reduce ? 'auto' : 'smooth'});
      const control = node.matches('button,input,select,textarea,a') ? node : $('button,input,select,textarea,a', node);
      control?.focus({preventScroll: true});
    });
    $('[data-guide-open]', panel).onclick = () => {
      if ($('dialog[open]')) { toast('Сначала закройте рабочую карточку крестиком: её поля сохраняются только вашей кнопкой.', true); return; }
      go(step.page, step.arg || null);
    };
    $('[data-guide-practice]', panel)?.addEventListener('click', async () => { if ($('dialog[open]')) { toast('Сначала закройте рабочую карточку крестиком.', true); return; } const lesson = g.lesson; endGuide(); await go('learning', lesson.id); mountPractice($('#view'), lesson); });
    $('[data-guide-next]', panel).onclick = async () => {
      if (!g.target || !g.target.isConnected) return;
      try {
        g.record = P.advanceGuide(g.record, g.index, g.lesson.guide.length, !!g.target?.isConnected);
        persist(g.lesson, g.record);
      } catch (e) { toast(e.message, true); return; }
      g.index = g.record.guide.index;
      const done = g.record.guide.complete;
      if (done) { endGuide(); toast(g.lesson.kind === 'guide' ? 'Экскурсия пройдена. Можно продолжить работу.' : 'Показ завершён. Практика — в разделе «Обучение».'); return; }
      const next = g.lesson.guide[g.index];
      if (!$('dialog[open]')) await go(next.page, next.arg || null);
      showGuide(true);
    };
    // Non-modal normal flow: no fixed tooltip over an action, keyboard never trapped.
    $('[data-guide-close]', panel).focus({preventScroll: true});
  }
  window.learningPageChanged = function () {
    addTargets();
    if (S.page !== 'learning') practice = null;
    if (guide) showGuide(true);
  };
  new MutationObserver(() => {
    if (!guide || guideTimer) return;
    guideTimer = setTimeout(() => { guideTimer = null; showGuide(); }, 80);
  }).observe(document.body, {childList: true, subtree: true});
  addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    if (guide) endGuide();
    if (practice) { const id = practice.lesson.id; practice = null; go('learning', id); }
  });
}());
