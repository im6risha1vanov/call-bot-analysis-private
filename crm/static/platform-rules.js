'use strict';

(() => {
  const ui = window.CRMPlatformAdmin;
  const subtype = {telegram: 'Telegram', sheets: 'Google Sheets'};
  const route = id => `/automations/${id}`;
  const labels = {text: 'Текст', subject: 'Тема', to: 'Получатели', row: 'Значения по столбцам',
    url: 'HTTPS-адрес', proxy: 'Через прокси'};
  const types = {string: 'Текст', number: 'Число', boolean: 'Да или нет'};
  const blank = catalog => ({trigger: {type: Object.keys(catalog.triggers)[0]}, if: [],
    actions: [{type: Object.keys(catalog.actions)[0]}]});
  const label = (catalog, field) => catalog.labels[field] || field;

  ui.automations = async ctx => {
    const data = await ctx.get('/automations');
    if (!ctx.alive()) return;
    const enabled = ctx.detail.modules.includes('automations');
    ctx.panel.innerHTML = ui.heading('Автоматизации',
      enabled ? '' : 'Модуль выключен. Включите «Автоматизации» в настройках компании, чтобы создать правило.',
      ctx.write && enabled ? ui.button('Новое правило', 'data-rule-create', 'primary') : '') +
      `<div class="pa-subheading"><span>Активные правила: ${fmt(data.usage.active)}${data.usage.max_active == null ? '' : ` из ${fmt(data.usage.max_active)}`}</span>
      <span>Запуски за месяц: ${fmt(data.usage.runs)}${data.usage.max_runs_month == null ? '' : ` из ${fmt(data.usage.max_runs_month)}`}</span></div>` +
      (data.automations.map(rule => ui.row(rule.name,
        `${ui.chip(rule.enabled ? 'Включено' : 'Выключено', rule.enabled ? 'good' : '')} · ${esc(data.catalog.triggers[rule.spec.trigger.type]?.title || rule.spec.trigger.type)}`,
        ui.button('Открыть', `data-rule="${rule.id}"`, 'small'))).join('') || ui.empty('Правил пока нет.'));
    $('[data-rule-create]', ctx.panel)?.addEventListener('click', () => editor(ctx, data, {version: 0, name: '', spec: blank(data.catalog)}));
    $$('[data-rule]', ctx.panel).forEach(btn => {
      btn.onclick = () => open(ctx, data, data.automations.find(rule => rule.id === Number(btn.dataset.rule)));
    });
  };

  async function open(ctx, data, rule) {
    try {
      const current = await ctx.get(route(rule.id) + '/versions');
      if (!ctx.alive()) return;
      const head = current.versions[0] || {version: 0, name: rule.name, spec: rule.spec};
      const draft = head.version && !head.published_at ? head : null;
      const enabled = ctx.detail.modules.includes('automations');
      const controls = [
        ctx.write && enabled ? ui.button(draft ? 'Изменить черновик' : 'Редактировать', 'data-rule-edit', 'primary') : '',
        draft && ctx.write ? ui.button('Симуляция', 'data-simulate') : '',
        draft && ctx.write && draft.simulated && enabled ? ui.button('Опубликовать', 'data-rule-publish', 'primary') : '',
        ctx.write && current.enabled ? ui.button('Поставить на паузу', 'data-rule-pause') : '',
        ctx.write && enabled && !current.enabled && current.active_version ? ui.button('Возобновить', 'data-rule-resume') : '',
        ui.button('Версии', 'data-rule-versions'), ui.button('Выполнения', 'data-rule-runs'),
      ].join('');
      const cat = data.catalog;
      const preview = `<div class="pa-stack">${ui.row('Событие', esc(cat.triggers[head.spec.trigger.type]?.title || head.spec.trigger.type))}
        ${ui.row('Условия', head.spec.if?.length ? head.spec.if.map(c => esc(`${label(cat, c.field)} ${cat.op_labels[c.op] || c.op}${c.value == null ? '' : ' ' + c.value}`)).join('<br>') : 'Без условий')}
        ${ui.row('Действия', head.spec.actions.map(a => esc(cat.actions[a.type]?.title || a.type)).join('<br>'))}</div>`;
      const box = modal(`${modalHead(head.name, current.active_version ? `Опубликованная версия ${current.active_version}` : 'Правило автоматизации',
        draft ? ui.chip(draft.simulated ? 'Черновик проверен' : 'Черновик', 'warn') : '')}
        <div class="rvm-body pa-form">${current.paused_reason ? ui.notice(current.paused_reason) : ''}${preview}
        <div class="pa-form-actions">${controls}</div><p class="pa-error" role="alert" tabindex="-1" data-pa-error hidden></p></div>`, true);
      const refresh = () => { box.close(); ctx.repaint(); return open(ctx, data, rule); };
      $('[data-rule-edit]', box)?.addEventListener('click', () => editor(ctx, data, head, rule, box));
      $('[data-simulate]', box)?.addEventListener('click', () => simulate(ctx, data, rule, draft, box));
      for (const [action, title, body, question] of [
        ['publish', 'Правило опубликовано', {version: draft?.version}, 'Опубликовать правило и включить реальные действия?'],
        ['pause', 'Правило на паузе', {version: current.active_version || 0}, 'Приостановить выполнение правила?'],
        ['resume', 'Правило включено', {version: current.active_version}, 'Возобновить выполнение правила?'],
      ]) {
        $(`[data-rule-${action}]`, box)?.addEventListener('click', async e => {
          const btn = e.currentTarget;
          if (!await askConfirm(question, btn.textContent)) return;
          ui.act(btn, `${ctx.path}${route(rule.id)}/${action}`, 'POST', body,
            () => { toast(title); return refresh(); }, $('[data-pa-error]', box));
        });
      }
      $('[data-rule-versions]', box).onclick = () => versions(ctx, data, rule, current, head, box);
      $('[data-rule-runs]', box).onclick = () => runs(ctx, rule, box);
    } catch (error) { if (ctx.alive()) toast(error.message, true); }
  }

  function editor(ctx, data, item, rule, opener) {
    const cat = data.catalog;
    const {spec} = item;
    const box = ui.edit(rule ? 'Редактор правила' : 'Новое правило', `
      <label class="field">Название<input name="name" maxlength="120" value="${esc(item.name)}" required></label>
      <div class="pa-fields">
        <label class="field">Событие<select name="trigger">${Object.entries(cat.triggers).map(([key, meta]) => ui.option(key, meta.title, spec.trigger.type)).join('')}</select></label>
        <label class="field" data-trigger-time>Время по часовому поясу компании<input type="time" name="time" value="${esc(spec.trigger.time || '09:00')}"></label>
      </div>
      <div class="pa-subheading"><h3>Условия</h3>${ui.button('Добавить условие', 'data-add-condition', 'small')}</div>
      <div class="pa-stack" data-conditions></div>
      <div class="pa-subheading"><h3>Действия</h3>${ui.button('Добавить действие', 'data-add-action', 'small')}</div>
      <div class="pa-stack" data-actions></div>`, (form, submit, error, dialog) => {
      try {
        const trigger = {type: form.elements.trigger.value};
        if (trigger.type === 'schedule.daily') trigger.time = form.elements.time.value;
        const conditions = $$('[data-condition]', form).map(row => {
          const entry = readCondition(row);
          if (['empty','not_empty'].includes(entry.op)) return {field: entry.field, op: entry.op};
          return entry;
        });
        const selected = $$('[data-action-editor]', form).map(readAction);
        if (!selected.length) throw new Error('Добавьте хотя бы одно действие.');
        const body = {version: item.version, name: form.elements.name.value,
          spec: {trigger, if: conditions, actions: selected}};
        ui.act(submit, ctx.path + (rule ? route(rule.id) + '/draft' : '/automations'), rule ? 'PUT' : 'POST', body,
          saved => {
            dialog.close(); opener?.close(); toast('Черновик правила сохранён');
            ctx.repaint();
            return open(ctx, data, {...rule, id: saved.id, name: saved.name, spec: saved.spec});
          }, error);
      } catch (cause) { error.textContent = cause.message; error.hidden = false; error.focus(); }
    }, 'Сохранить черновик', true);

    const trigger = $('[name=trigger]', box), time = $('[data-trigger-time]', box);
    const conditionList = $('[data-conditions]', box), actionList = $('[data-actions]', box);
    function fields(select, current) {
      const options = cat.triggers[trigger.value].fields;
      select.innerHTML = options.map(key => ui.option(key, label(cat, key), current)).join('') +
        (current && !options.includes(current) ? ui.option(current, label(cat, current), current) : '');
    }
    function syncTrigger() {
      time.hidden = trigger.value !== 'schedule.daily';
      $('input', time).required = !time.hidden;
      $$('[data-cond-field]', conditionList).forEach(select => fields(select, select.value));
    }
    function addCondition(condition = {}) {
      const selected = ['number','boolean'].includes(typeof condition.value) ? typeof condition.value : 'string';
      const row = document.createElement('div');
      row.className = 'pa-builder-item';
      row.dataset.condition = '';
      row.innerHTML = `<div class="pa-fields">
        <label class="field">Поле события<select data-cond-field required></select></label>
        <label class="field">Сравнение<select data-cond-op>${cat.ops.map(key => ui.option(key, cat.op_labels[key], condition.op)).join('')}</select></label>
        <label class="field" data-cond-typed>Тип значения<select data-cond-type>${Object.entries(types).map(([key, text]) => ui.option(key, text, selected)).join('')}</select></label>
        <label class="field" data-cond-input>Значение<span data-cond-value-wrap></span></label></div>
        <div class="pa-form-actions">${ui.button('Удалить условие', 'data-remove', 'small')}</div>`;
      conditionList.append(row);
      fields($('[data-cond-field]', row), condition.field);
      const op = $('[data-cond-op]', row), type = $('[data-cond-type]', row), wrap = $('[data-cond-value-wrap]', row);
      function sync(value) {
        const needsValue = !['empty','not_empty'].includes(op.value);
        $('[data-cond-input]', row).hidden = !needsValue;
        $('[data-cond-typed]', row).hidden = !needsValue;
        wrap.innerHTML = type.value === 'boolean'
          ? `<select data-cond-value>${ui.option('true', 'Да', String(value))}${ui.option('false', 'Нет', String(value))}</select>`
          : `<input data-cond-value ${type.value === 'number' ? 'type="number" step="any"' : ''} value="${esc(value)}"${needsValue ? ' required' : ''}>`;
      }
      op.onchange = () => sync($('[data-cond-value]', row).value);
      type.onchange = () => sync('');
      $('[data-remove]', row).onclick = () => row.remove();
      sync(condition.value ?? '');
      return row;
    }
    function addAction(action = {}, originalIndex = null) {
      const row = document.createElement('div');
      row.className = 'pa-builder-item';
      row.dataset.actionEditor = '';
      row.innerHTML = `<label class="field">Действие<select data-action-type>
        ${Object.entries(cat.actions).map(([key, meta]) => ui.option(key, meta.title, action.type)).join('')}</select></label>
        <div class="pa-stack" data-action-fields></div><div class="pa-form-actions">${ui.button('Удалить действие', 'data-remove', 'small')}</div>`;
      actionList.append(row);
      const picker = $('[data-action-type]', row);
      function update(input) {
        const meta = cat.actions[picker.value];
        const accounts = data.accounts.filter(account => account.kind === meta.account);
        const storedUrl = Boolean(input.url_configured) && !input.url && Number.isInteger(originalIndex);
        const holder = $('[data-action-fields]', row);
        holder.innerHTML = (meta.account ? `<label class="field">${esc(subtype[meta.account] || 'Подключение')}
            <select data-action-account required>${ui.option('', 'Выберите подключение')}${accounts.map(a =>
              ui.option(String(a.id), a.title + (a.status === 'ready' ? '' : ' · ожидает подключения'), String(input.account))).join('')}</select></label>` : '') +
          Object.keys(meta.fields).map(key => {
            if (key === 'proxy') return `<label><input type="checkbox" data-action-key="proxy"${input.proxy ? ' checked' : ''}> Через прокси</label>`;
            if (key === 'url') return `<label class="field">${labels.url}<input type="url" data-action-key="url" placeholder="${storedUrl ? 'Сохранённый адрес' : 'https://…'}"
              ${storedUrl ? `data-stored-url="${originalIndex}"` : 'required'} value="${esc(input.url)}"><small>${storedUrl ? 'Пустое поле сохраняет прежний адрес для этого шага.' : ''}</small></label>`;
            if (key === 'text' || key === 'row') return `<label class="field">${labels[key]}<textarea rows="2" data-action-key="${key}" required>${esc(key === 'row' ? input.row?.join('\n') : input.text)}</textarea>
              <small>${key === 'row' ? 'Одно значение на строку.' : 'Поля события можно вставить в фигурных скобках.'}</small></label>`;
            return `<label class="field">${labels[key] || esc(key)}<input data-action-key="${esc(key)}" value="${esc(key === 'to' ? input.to?.join(', ') : input[key])}"${key === 'to' ? ' required' : ''}>
              ${key === 'to' ? '<small>Адреса через запятую.</small>' : ''}</label>`;
          }).join('');
      }
      update(action);
      picker.onchange = () => update({});
      $('[data-remove]', row).onclick = () => row.remove();
      return row;
    }
    function readCondition(row) {
      const field = $('[data-cond-field]', row).value;
      const op = $('[data-cond-op]', row).value;
      const value = $('[data-cond-value]', row).value;
      const type = $('[data-cond-type]', row).value;
      return {field, op, value: type === 'number' ? Number(value) : type === 'boolean' ? value === 'true' : value};
    }
    function readAction(row) {
      const action = {type: $('[data-action-type]', row).value};
      const account = $('[data-action-account]', row)?.value;
      if (account) action.account = Number(account);
      for (const input of $$('[data-action-key]', row)) {
        const key = input.dataset.actionKey;
        if (key === 'proxy') { if (input.checked) action.proxy = true; }
        else if (key === 'to') action.to = input.value.split(/[,;]/).map(v => v.trim()).filter(Boolean);
        else if (key === 'row') action.row = input.value.split(/\r?\n/);
        else if (key === 'url' && !input.value && input.hasAttribute('data-stored-url')) action.source_action = Number(input.dataset.storedUrl);
        else action[key] = input.value;
      }
      return action;
    }
    (spec.if || []).forEach(addCondition);
    spec.actions.forEach(addAction);
    trigger.onchange = syncTrigger;
    $('[data-add-condition]', box).onclick = () => $('[data-cond-field]', addCondition()).focus();
    $('[data-add-action]', box).onclick = () => $('[data-action-type]', addAction()).focus();
    syncTrigger();
  }

  function simulate(ctx, data, rule, draft, opener) {
    ui.edit('Симуляция правила', `<p>Запуск покажет, какие условия выполнены и какие действия подготовлены. Доставка отключена.</p>
      <details><summary>Задать свои данные примера</summary><label class="field">Данные события в JSON<textarea name="payload" rows="5" placeholder='{"transition":{"to":"consultation"}}'></textarea></label></details>`,
      (form, submit, error, box) => {
        const value = form.elements.payload.value.trim();
        const body = {version: draft.version};
        if (value) {
          try { body.payload = JSON.parse(value); }
          catch (_) { error.textContent = 'Проверьте формат JSON.'; error.hidden = false; error.focus(); return; }
          if (!body.payload || Array.isArray(body.payload) || typeof body.payload !== 'object') {
            error.textContent = 'Данные события должны быть объектом JSON.'; error.hidden = false; error.focus(); return;
          }
        }
        ui.act(submit, `${ctx.path}${route(rule.id)}/simulate`, 'POST', body, result => {
          const details = result.steps.map(step => ui.row(data.catalog.actions[step.type]?.title || step.type,
            [step.subject, step.text, step.row?.join(' · ')].filter(Boolean).map(esc).join('<br>'))).join('');
          form.innerHTML = `<p>${result.conditions_pass ? 'Условия выполнены' : 'Условия не выполнены'}. Ничего не отправлено.</p>
            ${result.conditions.length ? `<ul>${result.conditions.map((pass, i) => `<li>Условие ${i + 1} ${pass ? 'выполнено' : 'не выполнено'}</li>`).join('')}</ul>` : ''}
            ${details}<div>${ui.button('Готово', 'data-done', 'primary')}</div>`;
          $('[data-done]', form).onclick = () => { box.close(); opener.close(); ctx.repaint(); return open(ctx, data, rule); };
          $('[data-done]', form).focus();
        }, error);
      }, 'Запустить симуляцию', true);
  }

  function versions(ctx, data, rule, current, head, opener) {
    const box = modal(`${modalHead('Версии правила')}<div class="rvm-body">${current.versions.map(version => ui.row(
      `Версия ${version.version} · ${version.name}`,
      version.published_at ? `Опубликована ${esc(ui.time(version.published_at))}` : (version.simulated ? 'Черновик проверен' : 'Черновик'),
      ui.button('Состав', `data-version="${version.version}"`, 'small') +
      (ctx.write && version.published_at && version.version !== head.version
        ? ui.button('Восстановить', `data-recovery="${version.version}"`, 'small') : ''))).join('') ||
      ui.empty('История появится после сохранения первого черновика.')}</div>`, true);
    $$('[data-version]', box).forEach(btn => btn.onclick = () => {
      const version = current.versions.find(v => v.version === Number(btn.dataset.version));
      const cat = data.catalog;
      modal(`${modalHead(version.name)}<div class="rvm-body"><p>${esc(cat.triggers[version.spec.trigger.type]?.title || version.spec.trigger.type)}</p>
        <ul>${version.spec.actions.map(a => `<li>${esc(cat.actions[a.type]?.title || a.type)}</li>`).join('')}</ul></div>`);
    });
    $$('[data-recovery]', box).forEach(btn => btn.onclick = async () => {
      if (!await askConfirm('Восстановить эту версию как новый черновик?', 'Восстановить')) return;
      const body = {version: head.version, restore_version: Number(btn.dataset.recovery)};
      ui.act(btn, `${ctx.path}${route(rule.id)}/draft`, 'PUT', body, () => {
        toast('Версия восстановлена в черновик'); box.close(); opener.close(); ctx.repaint(); return open(ctx, data, rule);
      });
    });
  }

  async function runs(ctx, rule, opener) {
    try {
      const data = await ctx.get(route(rule.id) + '/runs');
      if (!opener.isConnected) return;
      modal(`${modalHead('Выполнения правила')}<div class="rvm-body">${data.items.map(run => ui.row(
        `Запуск ${run.id}${run.version ? ' · версия ' + run.version : ''}`,
        `${esc(ui.time(run.at))}${run.test ? ' · пробный запуск' : ''}${run.steps.length
          ? `<ul>${run.steps.map(s => `<li>${esc(s.type)} · ${ui.state(s.status || (s.ok ? 'ok' : 'failed'))}</li>`).join('')}</ul>` : ''}`,
        ui.state(run.status))).join('') || ui.empty('Выполнений пока нет.')}</div>`, true);
    } catch (error) { if (opener.isConnected) toast(error.message, true); }
  }
})();
