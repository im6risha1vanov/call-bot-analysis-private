'use strict';

(() => {
  const ui = window.CRMPlatformAdmin;
  const stagesByStatus = {active: 'В работе', lost: 'Отказ', paid: 'Оплачено'};
  const keyPattern = '[a-z][a-z0-9_]{0,39}';
  const primaryCodes = new Set(['base', 'cold', 'presentation', 'messenger', 'contract', 'paid']);
  const path = pipeline => `/pipelines/${encodeURIComponent(pipeline)}`;
  const nameFor = (config, key) => config?.stages.find(stage => stage.key === key)?.name || key;
  const basic = {name: '', stages: [{key: 'base', name: 'База номеров'}, {key: 'paid', name: 'Оплата'}],
    reasons: {other: 'Другая причина'}, transitions: {}};

  ui.pipelines = async ctx => {
    const data = await ctx.get('/pipelines');
    if (!ctx.alive()) return;
    ctx.panel.innerHTML = ui.heading('Воронки продаж', '', ctx.write ? ui.button('Новая воронка', 'data-pipeline-new', 'primary') : '') +
      data.items.map(item => {
        const config = (item.active || item.draft).config;
        const title = item.active ? (item.active.version ? `Версия ${item.active.version} опубликована` : 'Исходная воронка') : 'Публикаций пока нет';
        const detail = `${esc(title)} · ${config.stages.length} этапов` + (item.draft ? ` · ${ui.chip('Есть черновик', 'warn')}` : '');
        return ui.row(config.name, detail, ui.button('Открыть', `data-pipeline="${esc(item.pipeline)}"`, 'small'));
      }).join('');
    $('[data-pipeline-new]', ctx.panel)?.addEventListener('click', () => create(ctx));
    $$('[data-pipeline]', ctx.panel).forEach(btn => btn.onclick = () => open(ctx, data.items.find(p => p.pipeline === btn.dataset.pipeline)));
  };

  function create(ctx) {
    ui.edit('Новая воронка', `<label class="field">Название<input name="name" maxlength="100" required></label>
      <label class="field">Код воронки<input name="code" pattern="${keyPattern}" placeholder="repeat_sales" required>
      <small>От одной до 40 латинских строчных букв, цифр и подчёркиваний. Первый символ — буква.</small></label>`,
    (form, submit, error, box) => {
      const data = ui.values(form);
      const code = data.code;
      const config = {...basic, name: data.name};
      ui.act(submit, ctx.path + path(code) + '/draft', 'PUT', {version: 0, config}, draft => {
        box.close(); toast('Воронка создана как черновик'); ctx.repaint();
        editor(ctx, {pipeline: code, active: null, draft});
      }, error);
    }, 'Создать черновик');
  }

  function open(ctx, item) {
    const {active, draft, pipeline} = item;
    const shown = draft || active;
    const box = modal(`${modalHead(shown.config.name, esc(pipeline))}<div class="rvm-body pa-form">
      <div class="pa-stack">${shown.config.stages.map(stage =>
        ui.row(stage.name, `${esc(stage.key)}${stage.stall_days ? ` · без движения ${stage.stall_days} дн.` : ''}`)).join('')}</div>
      <div class="pa-form-actions">${ctx.write ? ui.button(draft ? 'Изменить черновик' : 'Редактировать', 'data-edit', 'primary') : ''}
        ${draft ? ui.button('Проверить изменения', 'data-preview') : ''}
        ${ui.button('Версии', 'data-versions')}${active ? ui.button('Отчёт', 'data-report') : ''}</div></div>`, true);
    $('[data-edit]', box)?.addEventListener('click', () => editor(ctx, item, box));
    $('[data-preview]', box)?.addEventListener('click', () => preview(ctx, item, box));
    $('[data-versions]', box).onclick = () => versions(ctx, item, box);
    $('[data-report]', box)?.addEventListener('click', () => report(ctx, pipeline, box));
  }

  function editor(ctx, item, opener) {
    const original = item.draft || item.active;
    const config = original.config;
    const code = item.pipeline;
    const box = ui.edit('Редактор воронки', `<label class="field">Название<input name="name" maxlength="100" required value="${esc(config.name)}"></label>
      <div class="pa-subheading"><h3>Этапы</h3>${ui.button('Добавить этап', 'data-add-stage', 'small')}</div>
      <div class="pa-stack" data-stage-list></div>
      <div class="pa-subheading"><h3>Причины отказа</h3>${ui.button('Добавить причину', 'data-add-reason', 'small')}</div>
      <div class="pa-stack" data-reason-list></div>
      <div class="pa-subheading"><h3>Разрешённые переходы</h3>${ui.button('Добавить переход', 'data-add-transition', 'small')}</div>
      <p class="muted">Без правила для этапа сотрудник может выбрать любой другой этап, кроме оплаты.</p>
      <div class="pa-stack" data-transition-list></div>`,
    (form, submit, error, dialog) => {
      const rows = $$('[data-stage-row]', form);
      const stages = rows.map(row => ({key: $('[data-code]', row).value, name: $('[data-name]', row).value,
        stall_days: $('[data-stall]', row).value === '' ? null : Number($('[data-stall]', row).value)}));
      const pairs = $$('[data-reason-row]', form).map(row => [$('[data-code]', row).value, $('[data-name]', row).value]);
      const transitions = Object.create(null);
      const blocking = new Set();
      for (const row of $$('[data-transition-row]', form)) {
        const from = $('[data-from]', row).value, to = $('[data-to]', row).value;
        if (to === '_none') blocking.add(from);
        else (transitions[from] ||= []).push(to);
      }
      if (new Set(pairs.map(pair => pair[0])).size !== pairs.length || [...blocking].some(from => transitions[from]?.length)) {
        error.textContent = 'Коды причин должны быть уникальны. Запрет переходов для этапа нельзя объединить с разрешением.';
        error.hidden = false;
        error.focus();
        return;
      }
      blocking.forEach(from => { transitions[from] = []; });
      const body = {version: original.version, config: {name: form.elements.name.value, stages,
        reasons: Object.fromEntries(pairs), transitions}};
      ui.act(submit, `${ctx.path}${path(code)}/draft`, 'PUT', body, draft => {
        dialog.close();
        opener?.close();
        toast('Черновик воронки сохранён');
        ctx.repaint();
        open(ctx, {...item, draft});
      }, error);
    }, 'Сохранить черновик', true);

    const stageList = $('[data-stage-list]', box);
    const reasonList = $('[data-reason-list]', box);
    const transitionList = $('[data-transition-list]', box);

    function transitionOptions(select, rows, current, to) {
      select.innerHTML = ui.option('', 'Выберите этап', current) +
        rows.map(([key, title]) => ui.option(key, title || key, current)).join('') +
        (to ? ui.option('_none', 'Запретить все переходы', current) : '');
      if (current && ![...select.options].some(opt => opt.value === current)) select.value = '';
    }
    function sync() {
      const valid = $$('[data-stage-row]', stageList).map(row => [$('[data-code]', row).value, $('[data-name]', row).value])
        .filter(([key]) => key);
      $$('[data-transition-row]', transitionList).forEach(row => $$('select', row).forEach(select =>
        transitionOptions(select, valid, select.value, select.hasAttribute('data-to'))));
      const stages = $$('[data-stage-row]', stageList);
      stages.forEach((row, i) => {
        const lock = $('[data-code]', row).value;
        $('[data-order="up"]', row).disabled = lock === 'base' || lock === 'paid' || i <= 1;
        $('[data-order="down"]', row).disabled = lock === 'base' || lock === 'paid' || i >= stages.length - 2;
      });
    }
    function stageRow(stage, before) {
      const fixed = ['base', 'paid'].includes(stage.key) || (code === 'default' && primaryCodes.has(stage.key));
      const tile = document.createElement('div');
      tile.className = 'pa-builder-item';
      tile.dataset.stageRow = '';
      tile.innerHTML = `<div class="pa-stage-line">
        <label class="field">Код<input data-code pattern="${keyPattern}" required value="${esc(stage.key)}"${fixed ? ' readonly' : ''}></label>
        <label class="field">Этап<input data-name maxlength="100" required value="${esc(stage.name)}"></label>
        <label class="field">Срок, дней<input data-stall type="number" min="1" max="365" value="${esc(stage.stall_days)}"></label></div>
        <div class="pa-form-actions">${ui.button('Выше', 'data-order="up"', 'small')}${ui.button('Ниже', 'data-order="down"', 'small')}
        ${fixed ? '' : ui.button('Удалить этап', 'data-remove', 'small')}</div>`;
      stageList.insertBefore(tile, before || null);
      $$('[data-order]', tile).forEach(btn => btn.onclick = () => {
        if (btn.dataset.order === 'up') stageList.insertBefore(tile, tile.previousElementSibling);
        else stageList.insertBefore(tile.nextElementSibling, tile);
        sync();
        btn.focus();
      });
      $('[data-remove]', tile)?.addEventListener('click', () => { tile.remove(); sync(); });
      tile.addEventListener('change', sync);
      return tile;
    }
    function addReason(key = '', title = '') {
      const tile = document.createElement('div');
      tile.className = 'pa-builder-item';
      tile.dataset.reasonRow = '';
      tile.innerHTML = `<div class="pa-key-value">
        <label class="field">Код причины<input data-code pattern="${keyPattern}" required value="${esc(key)}"></label>
        <label class="field">Причина<input data-name maxlength="100" required value="${esc(title)}"></label></div>
        <div class="pa-form-actions">${ui.button('Удалить причину', 'data-remove', 'small')}</div>`;
      reasonList.append(tile);
      $('[data-remove]', tile).onclick = () => tile.remove();
      return tile;
    }
    function addTransition(from = '', to = '') {
      const tile = document.createElement('div');
      tile.className = 'pa-builder-item';
      tile.dataset.transitionRow = '';
      tile.innerHTML = '<div class="pa-fields"><label class="field">С этапа<select data-from required></select></label>' +
        '<label class="field">На этап<select data-to required></select></label></div>' +
        `<div class="pa-form-actions">${ui.button('Удалить переход', 'data-remove', 'small')}</div>`;
      transitionList.append(tile);
      const valid = $$('[data-stage-row]', stageList).map(row => [$('[data-code]', row).value, $('[data-name]', row).value]);
      transitionOptions($('[data-from]', tile), valid, from, false);
      transitionOptions($('[data-to]', tile), valid, to, true);
      $('[data-remove]', tile).onclick = () => tile.remove();
      return tile;
    }
    config.stages.forEach(stage => stageRow(stage));
    Object.entries(config.reasons).forEach(([key, title]) => addReason(key, title));
    Object.entries(config.transitions).forEach(([from, targets]) => {
      (targets.length ? targets : ['_none']).forEach(to => addTransition(from, to));
    });
    $('[data-add-stage]', box).onclick = () => {
      const tile = stageRow({key: '', name: ''}, stageList.lastElementChild); sync(); $('[data-code]', tile).focus();
    };
    $('[data-add-reason]', box).onclick = () => $('[data-code]', addReason()).focus();
    $('[data-add-transition]', box).onclick = () => $('[data-from]', addTransition()).focus();
    sync();
  }

  async function preview(ctx, item, opener) {
    try {
      const {draft, affected} = await ctx.get(path(item.pipeline) + '/preview' + qs({version: item.draft.version}));
      if (!ctx.alive() || !opener.isConnected) return;
      const stages = draft.config.stages.filter(stage => stage.key !== 'paid');
      const affectedKeys = [...new Set(affected.map(row => row.stage))];
      const details = affected.length ? `<div class="pa-stack">${affected.map(change => ui.row(
        nameFor(item.active?.config, change.stage), `${esc(stagesByStatus[change.status] || change.status)} · ${change.count}`)).join('')}</div>`
        : '<p>Для существующих сделок перенос этапов не требуется.</p>';
      const fields = details + (ctx.write ? affectedKeys.map(key => `<label class="field">
        Куда перенести сделки с этапа «${esc(nameFor(item.active?.config, key))}»
        <select name="${esc(key)}" required>${ui.option('', 'Выберите этап')}${stages.map(s => ui.option(s.key, s.name)).join('')}</select></label>`).join('') : '');
      if (!ctx.write) { modal(`${modalHead('Проверка изменений')}<div class="rvm-body">${fields}</div>`); return; }
      ui.edit('Проверка перед публикацией', fields, (form, submit, error, dialog) =>
        ui.act(submit, `${ctx.path}${path(item.pipeline)}/publish`, 'POST', {version: draft.version, remap: ui.values(form)},
          () => { dialog.close(); opener.close(); toast('Воронка опубликована'); return ctx.repaint(); }, error), 'Опубликовать воронку');
    } catch (error) { if (opener.isConnected) toast(error.message, true); }
  }

  async function versions(ctx, item, opener) {
    try {
      const data = await ctx.get(path(item.pipeline) + '/versions');
      if (!ctx.alive() || !opener.isConnected) return;
      const current = item.draft || item.active;
      const box = modal(`${modalHead('Версии воронки')}<div class="rvm-body">${data.items.map(version => ui.row(
        version.version ? `Версия ${version.version}` : 'Исходная воронка',
        version.version === 0 || version.published_at ? 'Опубликована' : 'Черновик',
        ui.button('Состав', `data-version="${version.version}"`, 'small') +
        (ctx.write && (version.published_at || !version.version) && version.version !== current.version
          ? ui.button('Восстановить', `data-restore="${version.version}"`, 'small') : ''))).join('')}</div>`, true);
      $$('[data-version]', box).forEach(btn => btn.onclick = () => {
        const version = data.items.find(entry => entry.version === Number(btn.dataset.version));
        modal(`${modalHead(version.config.name)}<div class="rvm-body"><ol>
          ${version.config.stages.map(s => `<li>${esc(s.name)}</li>`).join('')}</ol></div>`);
      });
      $$('[data-restore]', box).forEach(btn => btn.onclick = async () => {
        if (!await askConfirm('Сохранить выбранную версию как новый черновик?', 'Восстановить')) return;
        const body = {version: current.version, restore_version: Number(btn.dataset.restore)};
        ui.act(btn, `${ctx.path}${path(item.pipeline)}/draft`, 'PUT', body, draft => {
          box.close(); opener.close(); ctx.repaint(); open(ctx, {...item, draft});
          toast('Версия восстановлена в черновик');
        });
      });
    } catch (error) { if (opener.isConnected) toast(error.message, true); }
  }

  function report(ctx, pipeline, opener) {
    const box = modal(`${modalHead('Отчёт по воронке')}<div class="rvm-body">
      <form class="pa-form"><label class="field">На дату и время<input type="datetime-local" name="as_of">
      <small>Пустое поле показывает текущий отчёт. Время указано в часовом поясе браузера.</small></label>
      <button class="btn small" type="submit">Построить отчёт</button></form>
      <div data-report-results aria-live="polite"></div></div>`, true);
    const form = $('form', box);
    const result = $('[data-report-results]', box);
    async function load() {
      const btn = $('button', form);
      busy(btn, true);
      const stamp = form.elements.as_of.value;
      try {
        const data = await ctx.get(path(pipeline) + '/report' + qs({as_of: stamp ? new Date(stamp).toISOString() : ''}));
        if (!box.isConnected || !opener.isConnected) return;
        result.innerHTML = `<p class="muted">${data.version ? `Версия ${data.version}` : 'Исходная воронка'} · ${esc(ui.time(data.as_of))}</p>
          <div class="pa-table-wrap"><table class="pa-table"><thead><tr><th>Этап</th><th>Дошли</th><th>Отказы</th></tr></thead>
          <tbody>${data.funnel.stages.map(stage => `<tr><th scope="row">${esc(stage.name)}</th><td>${fmt(stage.reached)}</td><td>${fmt(stage.lost)}</td></tr>`).join('')}</tbody></table></div>`;
      } catch (error) { if (box.isConnected) result.innerHTML = ui.notice(error.message); }
      finally { if (box.isConnected) busy(btn, false); }
    }
    form.onsubmit = e => { e.preventDefault(); load(); };
    load();
  }
})();
