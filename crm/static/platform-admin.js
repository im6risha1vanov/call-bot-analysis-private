'use strict';

window.CRMPlatformAdmin = (() => {
  const base = '/api/platform/admin';
  const tabs = {
    settings: 'Настройки', accounts: 'Учётные записи', permissions: 'Права',
    pipelines: 'Воронки', automations: 'Автоматизации', integrations: 'Интеграции', audit: 'Журнал',
  };
  const roles = {director: 'Директор', manager: 'Менеджер', client: 'Клиент'};
  const sections = {
    deals: 'Сделки', leads: 'Лиды и задачи', calls: 'Журнал звонков', reports: 'Отчёты',
    staff: 'Сотрудники', callcenter: 'Колл-центр', robot: 'Робот', contracts: 'Договоры',
    sources: 'Источники', gpt: 'AveronGPT и промты', automations: 'Автоматизации',
  };
  const actions = {view: 'Просмотр', edit: 'Изменение', export: 'Экспорт', run: 'Запуск'};
  const status = {
    ready: ['Подключено', 'good'], pending: ['Ожидает подключения', 'warn'], broken: ['Ошибка', 'bad'],
    ok: ['Успешно', 'good'], failed: ['Ошибка', 'bad'], queued: ['В очереди', ''], running: ['Выполняется', 'acc'],
    delivery_unknown: ['Результат неизвестен', 'warn'], limit: ['Достигнут лимит', 'warn'],
    retry: ['Ожидает повтора', 'warn'],
  };
  const journalActions = {
    create: 'Создание', update: 'Изменение', restore: 'Восстановление', draft: 'Сохранение черновика',
    publish: 'Публикация', simulate: 'Симуляция', pause: 'Пауза', resume: 'Возобновление',
    queue: 'Запрос проверки', connect: 'Подключение', rotate: 'Замена токена', disconnect: 'Отключение',
    disable: 'Отключение учётной записи', role: 'Смена роли', password: 'Смена пароля', sessions: 'Закрытие сессий',
    ok: 'Успешная проверка', failed: 'Ошибка проверки',
  };
  const pending = new WeakMap();
  const module = {};
  const read = path => api(base + path);
  const scope = id => `/companies/${id}`;
  const time = value => value ? new Date(value).toLocaleString('ru-RU', {
    dateStyle: 'short', timeStyle: 'short',
  }) : '—';
  const json = value => esc(JSON.stringify(value, null, 2));
  const option = (value, label, selected) => `<option value="${esc(value)}"${value === selected ? ' selected' : ''}>${esc(label)}</option>`;
  const chip = (label, style = '') => `<span class="chip ${style}">${esc(label)}</span>`;
  const state = value => chip(...(status[value] || [value]));
  const button = (name, attrs = '', style = '') => `<button class="btn ${style}" type="button" ${attrs}>${esc(name)}</button>`;
  const empty = text => `<p class="pa-empty">${esc(text)}</p>`;
  const notice = text => `<p class="pa-error" role="alert">${esc(text)}</p>`;
  const row = (title, detail, controls = '') => `<div class="pa-row"><div><strong>${esc(title)}</strong>${detail ? `<div class="pa-meta">${detail}</div>` : ''}</div><div class="pa-row-actions">${controls}</div></div>`;
  const heading = (title, detail = '', control = '') => `<div class="pa-section-head"><div><h2>${esc(title)}</h2>${detail ? `<p>${esc(detail)}</p>` : ''}</div>${control}</div>`;
  const values = form => Object.fromEntries(new FormData(form));
  const random = (size = 20) => Array.from(crypto.getRandomValues(new Uint8Array(size)),
    byte => byte.toString(16).padStart(2, '0')).join('');

  async function act(btn, path, method, body, after, errorEl) {
    if (btn.disabled) return;
    const signature = JSON.stringify([path, method, body]);
    const previous = pending.get(btn);
    const key = previous?.signature === signature ? previous.key : random();
    pending.set(btn, {signature, key});
    busy(btn, true);
    if (errorEl) errorEl.hidden = true;
    try {
      const result = await api(base + path, {method, body, idempotencyKey: key});
      pending.delete(btn);
      if (btn.isConnected) await after?.(result);
    } catch (error) {
      if (!btn.isConnected) return;
      if (error.status && error.status < 500) pending.delete(btn);
      const message = error.message;
      if (errorEl) {
        errorEl.textContent = message;
        errorEl.hidden = false;
        errorEl.focus();
      } else toast(message, true);
    } finally {
      busy(btn, false);
    }
  }

  function edit(title, fields, submit, label = 'Сохранить', wide = false) {
    const box = modal(`${modalHead(title)}<form class="rvm-body pa-form">
      ${fields}<p class="pa-error" role="alert" tabindex="-1" data-pa-error hidden></p>
      <div class="pa-form-actions"><button type="submit" class="btn primary">${esc(label)}</button>
      ${button('Отмена', 'data-pa-close')}</div></form>`, wide);
    const form = $('form', box);
    $('[data-pa-close]', form).onclick = () => box.close();
    form.addEventListener('submit', e => {
      e.preventDefault();
      submit(form, $('button[type=submit]', form), $('[data-pa-error]', form), box);
    });
    return box;
  }

  function metrics(item, count) {
    const limit = item.effective_settings.ai_day_limit_rub;
    return `<div class="pa-metrics">
      <div><span>Подключённые модули</span><strong>${fmt(count)}</strong></div>
      <div><span>ИИ сегодня</span><strong>${esc(rub(item.ai_spent_today_rub))}</strong><small>${limit == null ? 'Без дневного лимита' : `Лимит ${esc(rub(limit))}`}${item.ai_limit_reached ? ' · достигнут' : ''}</small></div>
      <div><span>Телефония Mango</span><strong>${item.mango ? 'Подключена' : 'Не настроена'}</strong><small>${item.processing_enabled ? 'Обработка активна' : 'Обработка выключена'}</small></div>
    </div>`;
  }

  function settingsPanel(ctx) {
    const {detail: item, catalog} = ctx;
    const names = Object.entries(catalog.modules);
    const directors = item.directors.map(d => d.name || d.login || `Учетная запись ${d.id}`);
    const source = {company: 'Задано компанией', environment: 'Из окружения', default: 'Значение по умолчанию'};
    ctx.panel.innerHTML = heading('Профиль компании') + `<form class="pa-form" data-company-settings>
      <fieldset${ctx.write ? '' : ' disabled'}>
        <div class="pa-fields">
          <label class="field">Название<input name="name" value="${esc(item.name)}" maxlength="80" required></label>
          <label class="field">Часовой пояс<input name="timezone" list="pa-zones" value="${esc(item.timezone)}" required>
            <datalist id="pa-zones">${['Europe/Moscow','Europe/Kaliningrad','Europe/Samara','Asia/Yekaterinburg','Asia/Novosibirsk','Asia/Vladivostok','UTC'].map(z => option(z, z)).join('')}</datalist></label>
        </div>
        <div class="pa-subheading"><h3>Модули</h3></div>
        <div class="pa-check-grid">${names.map(([key, title]) => `<label><input type="checkbox" name="modules" value="${esc(key)}"${item.modules.includes(key) ? ' checked' : ''}> ${esc(title)}</label>`).join('')}</div>
        <div class="pa-subheading"><h3>Параметры и лимиты</h3></div>
        <div class="pa-fields">${Object.entries(catalog.settings).map(([key, meta]) => `<label class="field">
          ${esc(meta.label)}${meta.unit ? ', ₽' : ''}
          <input name="${esc(key)}" ${meta.type === 'number' ? 'type="number" min="0" max="100000" step="0.01"' : ''} value="${esc(item.settings[key] ?? '')}" placeholder="${esc(item.effective_settings[key] ?? '')}">
          <small>${esc(source[item.settings_sources[key]])}</small></label>`).join('')}</div>
      </fieldset>
      <p class="pa-error" role="alert" tabindex="-1" data-pa-error hidden></p>
      ${ctx.write ? '<div class="pa-form-actions"><button type="submit" class="btn primary">Сохранить настройки</button></div>' : ''}
    </form>
    <div class="pa-subheading"><h3>Директора</h3><span>${esc(directors.join(', ') || 'Учётных записей нет')}</span></div>`;
    const form = $('form', ctx.panel);
    form.addEventListener('submit', e => {
      e.preventDefault();
      const data = values(form);
      const settings = {};
      Object.keys(catalog.settings).forEach(key => {
        const value = data[key].trim();
        if (value !== String(item.settings[key] ?? '')) settings[key] = value === '' ? null
          : catalog.settings[key].type === 'number' ? Number(value) : value;
      });
      const body = {version: item.version, name: data.name, timezone: data.timezone,
        modules: new FormData(form).getAll('modules'), settings};
      act($('button[type=submit]', form), ctx.path + '/settings', 'PATCH', body,
        () => { toast('Настройки сохранены'); return ctx.repaint(); }, $('[data-pa-error]', form));
    });
  }

  function createCompany() {
    edit('Новая компания', `<div class="pa-fields">
      <label class="field">Название компании<input name="name" required maxlength="80"></label>
      <label class="field">Часовой пояс<input name="timezone" value="Europe/Moscow" required></label>
      <label class="field">Имя директора<input name="director" maxlength="80" placeholder="Директор"></label>
      <label class="field">Логин директора<input name="login" autocomplete="off" placeholder="Можно оставить пустым"></label>
      <label class="field">Пароль директора<input type="password" name="password" autocomplete="new-password" required minlength="8" maxlength="64"></label>
      </div>`, (form, btn, err, box) => {
      act(btn, '/companies', 'POST', values(form), item => {
        box.close();
        toast(`Компания создана. Логин директора: ${item.login}`);
        go('platform-admin', `${item.id}/settings`);
      }, err);
    }, 'Создать компанию', true);
  }

  function accountInput(role, item = {}) {
    return `<label class="field">Роль<select name="role">${Object.entries(roles).map(([key, label]) => option(key, label, role)).join('')}</select></label>
      <label class="field" data-role-target="manager">Добавочный номер менеджера<input name="extension" value="${esc(item.extension)}"></label>
      <label class="field" data-role-target="client">Ключ клиента<input name="client_key" value="${esc(item.client_key)}"></label>`;
  }

  function bindRole(box) {
    const select = $('[name=role]', box);
    const sync = () => $$('[data-role-target]', box).forEach(field => {
      const active = field.dataset.roleTarget === select.value;
      const input = $('input', field);
      field.hidden = !active;
      input.required = active;
      input.disabled = !active;
    });
    select.addEventListener('change', sync);
    sync();
  }

  function accountEdit(ctx, item) {
    const path = item ? `/accounts/${item.id}/role` : '/accounts';
    const fields = `${accountInput(item?.role || 'manager', item)}${item ? '' : `
      <label class="field">Имя<input name="name" maxlength="80"></label>
      <label class="field">Логин<input name="login" autocomplete="off" required></label>
      <label class="field">Пароль<input type="password" name="password" autocomplete="new-password" minlength="8" maxlength="64" required></label>`}`;
    const box = edit(item ? 'Изменить роль' : 'Новая учётная запись', fields, (form, btn, err, dialog) =>
      act(btn, ctx.path + path, 'POST', values(form), () => {
        dialog.close(); toast(item ? 'Роль изменена' : 'Учётная запись создана'); return ctx.repaint();
      }, err));
    bindRole(box);
  }

  async function accountsPanel(ctx) {
    ctx.panel.innerHTML = heading('Учётные записи', '', ctx.write ? button('Добавить учётную запись', 'data-add-account', 'primary') : '') +
      '<div data-account-list></div><div data-account-more></div>';
    $('[data-add-account]', ctx.panel)?.addEventListener('click', () => accountEdit(ctx));
    const list = $('[data-account-list]', ctx.panel);
    const more = $('[data-account-more]', ctx.panel);
    async function page(after) {
      const data = await ctx.get('/accounts' + qs({after}));
      if (!ctx.alive()) return;
      for (const item of data.items) {
        const mine = item.id === S.me.account_id;
        const details = [roles[item.role], item.login, item.extension && `доб. ${item.extension}`, item.client_key]
          .filter(Boolean).map(esc).join(' · ');
        const wrap = document.createElement('div');
        wrap.innerHTML = row(item.name || item.login || `Учётная запись ${item.id}`, details,
          (item.disabled ? chip('Отключена', 'warn') : '') + (mine ? chip('Ваш аккаунт', 'acc') : '') +
          button('Права', 'data-account-info', 'small') + (ctx.write && !item.disabled && !mine ? button('Управление', 'data-account-manage', 'small') : ''));
        list.append(wrap);
        $('[data-account-info]', wrap).onclick = () => accountRights(ctx, item);
        $('[data-account-manage]', wrap)?.addEventListener('click', () => manageAccount(ctx, item));
      }
      if (!after && !data.items.length) list.innerHTML = empty('Учётных записей пока нет.');
      more.innerHTML = data.next_after ? button('Показать ещё', 'data-more') : '';
      $('[data-more]', more)?.addEventListener('click', e => {
        const btn = e.currentTarget;
        busy(btn, true);
        page(data.next_after).catch(error => { busy(btn, false); toast(error.message, true); });
      });
    }
    await page();
  }

  async function accountRights(ctx, item) {
    try {
      const result = await ctx.get(`/accounts/${item.id}/permissions`);
      if (!ctx.alive()) return;
      modal(`${modalHead('Доступ учётной записи', esc(item.name || item.login || ''))}
        <div class="rvm-body"><p>Роль: ${esc(roles[result.role] || result.role)}.</p>
        <p>${result.scope === 'assigned_records' ? 'Доступ к записям, закреплённым за сотрудником.' : 'Действуют ограничения роли в CRM.'}</p>
        <div><strong>Отключено для роли</strong>${result.denied.length
          ? `<ul>${result.denied.map(key => `<li>${esc(permissionLabel(key))}</li>`).join('')}</ul>`
          : '<p class="muted">Дополнительных ограничений нет.</p>'}</div>
        <div><strong>Подключённые модули</strong><p>${result.modules.map(m => esc(ctx.catalog.modules[m] || m)).join(', ') || '—'}</p></div></div>`);
    } catch (error) { if (ctx.alive()) toast(error.message, true); }
  }

  function manageAccount(ctx, item) {
    const box = modal(`${modalHead(item.name || item.login || 'Учётная запись')}
      <div class="rvm-body"><div class="pa-form-actions">
      ${!item.platform ? button('Сменить роль', 'data-action="role"') : ''}
      ${item.login ? button('Сбросить пароль', 'data-action="password"') : ''}
      ${button('Завершить сессии', 'data-action="sessions"')}
      ${button('Отключить доступ', 'data-action="disable"', 'danger')}
      </div><p class="pa-error" role="alert" tabindex="-1" data-pa-error hidden></p></div>`);
    $$('[data-action]', box).forEach(btn => btn.onclick = async () => {
      const action = btn.dataset.action;
      if (action === 'role') { box.close(); accountEdit(ctx, item); return; }
      if (action === 'password') {
        box.close();
        edit('Новый пароль', '<label class="field">Пароль<input name="password" type="password" autocomplete="new-password" required minlength="8" maxlength="64"></label>',
          (form, submit, error, dialog) => act(submit, `${ctx.path}/accounts/${item.id}/password`, 'POST', values(form),
            () => { dialog.close(); toast('Пароль изменён, активные сессии закрыты'); }, error), 'Изменить пароль');
        return;
      }
      const question = action === 'disable' ? 'Отключить доступ этой учётной записи?' : 'Завершить активные сессии этой учётной записи?';
      if (!await askConfirm(question, btn.textContent, action === 'disable')) return;
      act(btn, `${ctx.path}/accounts/${item.id}/${action}`, 'POST', {},
        () => { box.close(); toast(action === 'disable' ? 'Доступ отключён' : 'Активные сессии закрыты'); return ctx.repaint(); },
        $('[data-pa-error]', box));
    });
  }

  function permissionLabel(key) {
    const [section, action] = key.split('.');
    return `${sections[section] || section} · ${actions[action] || action}`;
  }

  async function permissionsPanel(ctx) {
    ctx.panel.innerHTML = heading('Права по ролям', 'Отметьте операции, которые нужно запретить для роли в этой компании.') +
      `<label class="field pa-select">Роль<select data-role>${ctx.catalog.roles.map(key => option(key, roles[key], ctx.role)).join('')}</select></label><div data-permission-sheet></div>`;
    const holder = $('[data-permission-sheet]', ctx.panel);
    const selector = $('[data-role]', ctx.panel);
    let seq = 0;
    async function load() {
      const mine = ++seq;
      const role = selector.value;
      ctx.role = role;
      holder.innerHTML = empty('Загружаю права…');
      try {
        const data = await ctx.get(`/permissions/${role}`);
        if (!ctx.alive() || mine !== seq) return;
        holder.innerHTML = `<form class="pa-form"><fieldset${ctx.write ? '' : ' disabled'}><div class="pa-table-wrap">
          <table class="pa-table"><thead><tr><th scope="col">Раздел</th>${ctx.catalog.actions.map(a => `<th scope="col">${esc(actions[a])}</th>`).join('')}</tr></thead>
          <tbody>${ctx.catalog.sections.map(key => `<tr><th scope="row">${esc(sections[key] || key)}</th>${ctx.catalog.actions.map(a => {
            const flag = `${key}.${a}`;
            return `<td><input type="checkbox" name="denied" value="${flag}" aria-label="${esc('Запретить: ' + permissionLabel(flag))}"${data.denied.includes(flag) ? ' checked' : ''}></td>`;
          }).join('')}</tr>`).join('')}</tbody></table></div></fieldset>
          <p class="pa-error" role="alert" tabindex="-1" data-pa-error hidden></p>
          ${ctx.write ? '<button class="btn primary" type="submit">Сохранить права</button>' : ''}</form>`;
        const form = $('form', holder);
        form.addEventListener('submit', e => {
          e.preventDefault();
          busy(selector, true);
          act($('button[type=submit]', form), `${ctx.path}/permissions/${role}`, 'PATCH',
            {version: data.version, denied: new FormData(form).getAll('denied')},
            () => { toast('Права сохранены'); return load(); }, $('[data-pa-error]', form))
            .finally(() => busy(selector, false));
        });
      } catch (error) { if (ctx.alive() && mine === seq) holder.innerHTML = notice(error.message); }
    }
    selector.addEventListener('change', load);
    await load();
  }

  async function integrationsPanel(ctx) {
    const data = await ctx.get('/integrations');
    if (!ctx.alive()) return;
    const check = key => {
      const item = data.checks[key];
      return item ? `${state(item.status)} · ${esc(time(item.checked_at))}${item.last_success_at ? ` · успешно ${esc(time(item.last_success_at))}` : ''}
        ${item.error ? `<div>${esc(item.error)}</div>` : ''}` : 'Проверок пока нет';
    };
    ctx.panel.innerHTML = heading('Интеграции') +
      row('Mango Office', `${data.mango.connected ? 'Подключено' : 'Не подключено'} · ${check('mango')}`, ctx.write
        ? button('Проверить', `data-mango-check${data.mango.connected ? '' : ' disabled'}`, 'small') +
          button(data.mango.connected ? 'Заменить ключи' : 'Подключить', 'data-integrate="mango"', 'small') : '') +
      row('Telegram', data.telegram ? 'Бот CRM настроен' : 'Бот CRM не настроен', ctx.write
        ? button('Привязать чат', `data-integrate="telegram"${data.telegram ? '' : ' disabled'}`, 'small') : '') +
      row('Google Sheets', check('sheets'), ctx.write ? button('Подключить таблицу', 'data-integrate="sheets"', 'small') : '') +
      '<div data-operations></div>' +
      heading('Подключённые чаты и таблицы') + (data.accounts.map(a =>
        row(a.title, state(a.status), ctx.write ? button('Отключить', `data-disconnect="${a.id}"`, 'small') : '')).join('') || empty('Подключений пока нет.')) +
      heading('Входящие вебхуки') + (data.webhooks.map(h => row(h.name, h.enabled ? 'Сценарий включён' : 'Сценарий на паузе',
        ctx.write ? button('Заменить токен', `data-hook="${h.id}"`, 'small') : '')).join('') || empty('Входящих вебхуков нет.'));
    const operations = $('[data-operations]', ctx.panel);
    const started = async item => {
      ctx.operationIds.add(item.id);
      await operation(ctx, operations, item.id);
    };
    for (const id of ctx.operationIds) operation(ctx, operations, id);
    $('[data-mango-check]', ctx.panel)?.addEventListener('click', e =>
      act(e.currentTarget, ctx.path + '/integrations/mango/check', 'POST', {}, started));
    $$('[data-integrate]', ctx.panel).forEach(btn => btn.onclick = () => {
      const kind = btn.dataset.integrate;
      if (kind === 'telegram') { telegram(ctx); return; }
      const fields = kind === 'mango'
        ? '<label class="field">Код ВАТС<input name="key" type="password" required autocomplete="off" maxlength="200"></label><label class="field">Ключ подписи<input name="salt" type="password" required autocomplete="off" maxlength="200"></label>'
        : '<label class="field">Ссылка на таблицу<input name="url" type="url" required></label><label class="field">Имя листа<input name="sheet" maxlength="120" placeholder="Первый лист"></label>';
      edit(kind === 'mango' ? 'Подключить Mango' : 'Подключить Google Sheets', fields,
        (form, submit, error, box) => act(submit, `${ctx.path}/integrations/${kind}/check`, 'POST', values(form),
          item => { box.close(); return started(item); }, error), 'Начать проверку');
    });
    $$('[data-disconnect]', ctx.panel).forEach(btn => btn.onclick = async () => {
      if (!await askConfirm('Отключить это подключение?', 'Отключить', true)) return;
      act(btn, `${ctx.path}/integrations/accounts/${btn.dataset.disconnect}/disconnect`, 'POST', {},
        () => { toast('Подключение отключено'); return ctx.repaint(); });
    });
    $$('[data-hook]', ctx.panel).forEach(btn => btn.onclick = () => {
      const token = random(20);
      const url = `${location.origin}/hook/${token}`;
      edit('Заменить токен вебхука',
        `<p class="muted">Скопируйте новый адрес для отправителя. После сохранения прежний адрес перестанет принимать события.</p>
        <label class="field">Новый адрес<input readonly value="${esc(url)}" data-copy-hook></label>`,
        (form, submit, error, box) => act(submit, `${ctx.path}/automations/${btn.dataset.hook}/webhook`, 'POST', {token},
          () => {
            form.innerHTML = `<p class="muted">Токен заменён. Настройте отправителя на этот адрес.</p>
              <label class="field">Адрес вебхука<input readonly value="${esc(url)}"></label>
              ${button('Готово', 'data-pa-close', 'primary')}`;
            $('[data-pa-close]', form).onclick = () => box.close();
            $('[data-pa-close]', form).focus();
          }, error), 'Заменить токен');
    });
  }

  function telegram(ctx) {
    const code = random(16);
    edit('Привязать чат Telegram', `<label class="field">Имя бота CRM<input name="bot" pattern="@?[A-Za-z0-9_]{5,32}" placeholder="@имя_бота" required></label>
      <p class="muted">Откройте одну из ссылок после создания привязки. Код действует 24 часа.</p>`,
      (form, submit, error) => {
        const name = form.elements.bot.value.replace(/^@/, '');
        act(submit, `${ctx.path}/integrations/telegram`, 'POST', {code}, () => {
          form.innerHTML = `<div class="pa-form-actions">
            <a class="btn primary" target="_blank" rel="noopener" href="https://t.me/${esc(name)}?start=aut_${code}">Открыть личный чат</a>
            <a class="btn" target="_blank" rel="noopener" href="https://t.me/${esc(name)}?startgroup=aut_${code}">Выбрать группу</a></div>
            <p class="muted">После нажатия «Старт» обновите список подключений.</p>`;
          $('a', form).focus();
          ctx.repaint();
        }, error);
      }, 'Создать привязку');
  }

  async function operation(ctx, holder, id) {
    if ($(`[data-operation="${id}"]`, holder)) return;
    const tile = document.createElement('div');
    tile.dataset.operation = id;
    tile.className = 'pa-operation';
    tile.setAttribute('role', 'status');
    holder.append(tile);
    for (let check = 0; check < 45; check++) {
      if (!ctx.alive() || !tile.isConnected) return;
      try {
        const item = await ctx.get(`/operations/${id}`);
        if (!tile.isConnected) return;
        tile.innerHTML = `<strong>${item.kind === 'mango' ? 'Mango Office' : 'Google Sheets'}</strong> ${state(item.status)}
          ${item.result?.error ? `<p>${esc(item.result.error)}</p>` : ''}`;
        if (!['queued','running'].includes(item.status)) {
          if (ctx.completedOperations.has(id)) return;
          ctx.completedOperations.add(id);
          return ctx.repaint();
        }
      } catch (error) {
        if (tile.isConnected) tile.innerHTML = notice(error.message);
        return;
      }
      await new Promise(resolve => setTimeout(resolve, 1500));
    }
    if (tile.isConnected) tile.innerHTML += '<p class="muted">Проверка ещё идёт. Обновите раздел позже.</p>';
  }

  async function auditPanel(ctx) {
    ctx.panel.innerHTML = heading('Журнал изменений') +
      `<label class="field pa-select">Раздел<select data-audit-filter>${option('', 'Все разделы', ctx.audit)}${Object.entries(tabs).map(([key, label]) => option(key, label, ctx.audit)).join('')}</select></label>
      <div data-audit-list></div><div data-audit-more></div>`;
    const list = $('[data-audit-list]', ctx.panel);
    const more = $('[data-audit-more]', ctx.panel);
    const filter = $('[data-audit-filter]', ctx.panel);
    let seq = 0;
    async function page(before) {
      const current = ++seq;
      const resource = filter.value;
      ctx.audit = resource;
      if (!before) { list.innerHTML = empty('Загружаю журнал…'); more.replaceChildren(); }
      try {
        const data = await ctx.get('/audit' + qs({before, resource}));
        if (!ctx.alive() || current !== seq) return;
        if (!before) list.replaceChildren();
        for (const item of data.items) {
          list.insertAdjacentHTML('beforeend', row(journalActions[item.action] || item.action,
            `${esc(tabs[item.resource] || item.resource)} · ${esc(time(item.created_at))} · аккаунт ${esc(item.actor_id)}`,
            button('Подробнее', `data-audit-id="${item.id}"`, 'small')));
          $(`[data-audit-id="${item.id}"]`, list).onclick = () => auditDetail(ctx, item);
        }
        if (!before && !data.items.length) list.innerHTML = empty('Изменений в этом разделе нет.');
        more.innerHTML = data.next_before ? button('Показать ещё', 'data-more-audit') : '';
        $('[data-more-audit]', more)?.addEventListener('click', e => {
          busy(e.currentTarget, true);
          page(data.next_before);
        });
      } catch (error) {
        if (!ctx.alive() || current !== seq) return;
        (before ? more : list).innerHTML = notice(error.message);
      }
    }
    filter.addEventListener('change', () => page());
    await page();
  }

  function auditDetail(ctx, item) {
    const restore = item.resource === 'settings' && ctx.write;
    const box = modal(`${modalHead(journalActions[item.action] || item.action, esc(time(item.created_at)))}
      <div class="rvm-body"><div class="pa-fields">
      <div><h3>До изменения</h3><pre class="pa-code">${item.before_state ? json(item.before_state) : '—'}</pre></div>
      <div><h3>После изменения</h3><pre class="pa-code">${item.after_state ? json(item.after_state) : '—'}</pre></div>
      </div><div class="pa-form-actions">${restore && item.before_state ? button('Восстановить «до»', 'data-restore="before"') : ''}
      ${restore && item.after_state ? button('Восстановить «после»', 'data-restore="after"') : ''}</div>
      <p class="pa-error" role="alert" tabindex="-1" data-pa-error hidden></p></div>`, true);
    $$('[data-restore]', box).forEach(btn => btn.onclick = async () => {
      if (!await askConfirm('Заменить настройки компании выбранной версией?', 'Восстановить')) return;
      act(btn, `${ctx.path}/settings/restore/${item.id}`, 'POST', {version: ctx.detail.version, state: btn.dataset.restore},
        () => { box.close(); toast('Версия настроек восстановлена'); return ctx.repaint(); }, $('[data-pa-error]', box));
    });
  }

  const renderers = {settings: settingsPanel, accounts: accountsPanel, permissions: permissionsPanel,
    integrations: integrationsPanel, audit: auditPanel};
  const workspaces = new Map();
  let lastCompany = null;

  VIEWS['platform-admin'] = async (view, arg) => {
    const [param, tab] = String(arg || '').split('/');
    const activeTab = Object.hasOwn(tabs, tab) ? tab : 'settings';
    view.innerHTML = `<div class="pa"><div class="pa-layout"><aside class="pa-companies" aria-label="Компании"></aside>
      <div class="pa-main" aria-busy="true">${empty('Загружаю компании…')}</div></div></div>`;
    const aside = $('.pa-companies', view), main = $('.pa-main', view);
    const alive = () => view.isConnected;
    const result = await Promise.all([read('/catalog'), read('/companies')]).catch(error => {
      if (alive()) {
        main.removeAttribute('aria-busy');
        main.innerHTML = notice(error.message) + button('Повторить', 'data-retry-admin');
        $('[data-retry-admin]', main).onclick = reload;
      }
      return null;
    });
    if (!result || !alive()) return;
    const [catalog, listing] = result;
    const id = /^[1-9]\d{0,8}$/.test(param) ? Number(param) : lastCompany
      ?? listing.items.find(item => item.home)?.id ?? listing.items[0]?.id;
    if (id) lastCompany = id;
    if (catalog.write_enabled) {
      const top = setHead({actions: button('Новая компания', 'data-new-company', 'primary')});
      $('[data-new-company]', top).onclick = createCompany;
    }
    aside.innerHTML = `<h2>Компании</h2>
      <button class="pa-company-toggle" type="button" aria-controls="pa-company-controls" aria-expanded="false">
        <span>Выбрать компанию</span>${svg('chev')}</button>
      <div id="pa-company-controls"><label class="field"><span class="pa-sr">Найти компанию</span>
      <input type="search" placeholder="Название или ID" data-company-search></label><div data-companies></div><div data-companies-more></div></div>`;
    const search = $('[data-company-search]', aside), companies = $('[data-companies]', aside), paged = $('[data-companies-more]', aside);
    const toggle = $('.pa-company-toggle', aside);
    toggle.onclick = () => {
      const show = aside.classList.toggle('is-expanded');
      toggle.setAttribute('aria-expanded', String(show));
      if (show) search.focus();
    };
    let searchSeq = 0, typing;
    function renderCompanies(data, append = false) {
      if (!append) companies.replaceChildren();
      data.items.forEach(item => companies.insertAdjacentHTML('beforeend', `<button class="pa-company" type="button" data-go="platform-admin"
        data-arg="${item.id}/${activeTab}"${item.id === id ? ' aria-current="true"' : ''}>
        <span class="pa-company-icon">${esc(initials(item.name))}</span><span><strong>${esc(item.name)}</strong>
        <small>${item.home ? 'Основная компания' : `ID ${item.id}`}</small></span>${svg('chev')}</button>`));
      if (!append && !data.items.length) companies.innerHTML = empty('Компании не найдены.');
      paged.innerHTML = data.next_after ? button('Показать ещё', 'data-more-companies', 'small') : '';
      $('[data-more-companies]', paged)?.addEventListener('click', e => {
        busy(e.currentTarget, true);
        fetchCompanies(data.next_after);
      });
    }
    async function fetchCompanies(after) {
      const seq = ++searchSeq;
      try {
        const data = await read('/companies' + qs({q: search.value.trim(), after}));
        if (alive() && seq === searchSeq) renderCompanies(data, !!after);
      } catch (error) { if (alive() && seq === searchSeq) paged.innerHTML = notice(error.message); }
    }
    search.addEventListener('input', () => {
      searchSeq++;
      clearTimeout(typing);
      typing = setTimeout(() => { if (alive()) fetchCompanies(); }, 250);
    });
    renderCompanies(listing);
    if (!id) { main.removeAttribute('aria-busy'); main.innerHTML = empty('Компаний пока нет.'); return; }

    const sessionKey = `${S.me.account_id}/${id}`;
    if (!workspaces.has(sessionKey)) workspaces.set(sessionKey, {operationIds: new Set(), completedOperations: new Set()});
    const saved = workspaces.get(sessionKey);
    const ctx = {id, path: scope(id), catalog, write: catalog.write_enabled, alive,
      operationIds: saved.operationIds, completedOperations: saved.completedOperations,
      role: 'director', audit: '', get: path => read(scope(id) + path)};
    let seq = 0;
    ctx.repaint = async () => {
      const current = ++seq;
      try {
        const detail = await ctx.get('');
        if (!alive() || current !== seq) return;
        $('span', toggle).textContent = detail.name;
        toggle.setAttribute('aria-label', `Выбрать компанию. Сейчас выбрана ${detail.name}`);
        const picked = $('.pa-company[aria-current]', companies);
        if (picked) {
          $('strong', picked).textContent = detail.name;
          $('.pa-company-icon', picked).textContent = initials(detail.name);
        }
        main.innerHTML = `<header class="pa-company-head"><div><h2>${esc(detail.name)}</h2><p>${esc(detail.timezone)}</p></div>
          ${catalog.write_enabled ? '' : chip('Только просмотр', 'warn')}</header>
          ${metrics(detail, detail.modules.length)}
          <nav class="pa-tabs" aria-label="Управление компанией">${Object.entries(tabs).map(([key, name]) =>
            `<button type="button" data-go="platform-admin" data-arg="${id}/${key}"${activeTab === key ? ' aria-current="page"' : ''}>${esc(name)}</button>`).join('')}</nav>
          <section class="pa-panel"><p class="pa-empty">Загружаю раздел…</p></section>`;
        main.removeAttribute('aria-busy');
        const panel = $('.pa-panel', main);
        const renderer = renderers[activeTab] || module[activeTab];
        await renderer({...ctx, panel, detail});
      } catch (error) {
        if (!alive() || current !== seq) return;
        main.removeAttribute('aria-busy');
        const panel = $('.pa-panel', main) || main;
        panel.innerHTML = notice(error.message) + button('Повторить', 'data-reload');
        $('[data-reload]', panel).onclick = ctx.repaint;
      }
    };
    await ctx.repaint();
  };

  return Object.assign(module, {act, button, chip, edit, empty, heading, json, notice,
    option, random, row, state, time, values});
})();
