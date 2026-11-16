/* The personal workspace uses only the authenticated user's own API. */
VIEWS.personal = async function personalView(root) {
  const viewToken = {}; root._personalViewToken = viewToken;
  let tab = 'day', selected = null, projects = [], tasks = [], profile = null, history = null, agentJobs = [], agentDetail = null, connections = {};
  const statusLabels = {planned: 'Запланировано', in_progress: 'В работе', done: 'Готово', cancelled: 'Отменено'};
  const h = value => esc(value == null ? '' : String(value));
  // The browser interprets entered date-times in its own zone; show that zone explicitly.
  const browserTime = value => value ? new Date(value).toISOString() : null;
  const browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'часовой пояс устройства';
  const localTime = value => value ? new Date(value).toLocaleString('ru-RU') : '—';
  let saving = false, agentBusy = false, agentDraft = '', agentPending = null, agentEpoch = 0;
  const agentStorageKey = 'personal-agent-draft:' + String(S.me.account_id || S.me.name || '');
  try { const stored = JSON.parse(sessionStorage.getItem(agentStorageKey) || 'null'); if (stored) { agentDraft = stored.text || ''; agentPending = stored.id || null; } } catch (_) {}
  function saveAgentDraft() { try { sessionStorage.setItem(agentStorageKey, JSON.stringify({text:agentDraft, id:agentPending})); } catch (_) {} }
  async function load() {
    profile = (await api('/api/personal/profile')).profile;
    if (profile) {
      [projects, tasks] = await Promise.all([api('/api/personal/projects'), api('/api/personal/tasks')]);
      projects = projects.projects; tasks = tasks.tasks;
    }
  }
  function formProfile() {
    return `<form data-form="profile" class="personal-form"><label class="field">Имя<input name="display_name" required maxlength="200" value="${h(profile?.display_name || S.me.name || '')}"></label><label class="field">Часовой пояс<input name="timezone" required value="${h(profile?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone || 'Europe/Moscow')}"></label><button class="btn primary">Сохранить профиль</button></form>`;
  }
  function taskForm(task = {}) {
    const deliveryLabels = {pending:'ожидает отправки', sending:'отправляется', sent:'отправлено', uncertain:'доставка не подтверждена', blocked:'отправка недоступна', cancelled:'отменено'};
    return `<form data-form="task" class="personal-form"><h3>${task.id ? 'Редактирование задачи' : 'Новая задача'}</h3>
      <label class="field">Название<input name="title" required maxlength="300" value="${h(task.title)}"></label>
      <label class="field">Проект<select name="project_id"><option value="">Без проекта</option>${projects.map(p => `<option value="${p.id}" ${p.id === task.project_id ? 'selected' : ''}>${h(p.title)}</option>`).join('')}</select></label>
      <label class="field">Статус<select name="status">${Object.entries(statusLabels).map(([k,v]) => `<option value="${k}" ${k === (task.status || 'planned') ? 'selected' : ''}>${v}</option>`).join('')}</select></label>
      <label class="field">Ожидаемый результат<textarea name="expected_result">${h(task.expected_result)}</textarea></label>
      <label class="field">Результат<textarea name="result_note">${h(task.result_note)}</textarea></label>
      <label class="field">Что мешает<textarea name="blocker">${h(task.blocker)}</textarea></label>
      <label class="field">План на день<input type="date" name="planned_day" value="${h(task.planned_day)}"></label>
      <label class="field">Срок<input type="datetime-local" name="due_at" value="${h(task.due_at ? new Date(task.due_at).toLocaleString('sv-SE').replace(' ', 'T').slice(0,16) : '')}"></label>
      <label class="field">Напомнить<input type="datetime-local" name="reminder_at" value="${h(task.reminder_at ? new Date(task.reminder_at).toLocaleString('sv-SE').replace(' ', 'T').slice(0,16) : '')}"></label>
      ${task.reminder_delivery_status ? `<p>Напоминание: ${h(deliveryLabels[task.reminder_delivery_status] || '—')}</p>` : ''}
      <label><input type="checkbox" name="reminders_enabled" ${task.reminders_enabled !== false ? 'checked' : ''}> Напоминания включены</label>
      <p class="hint">Время вводится в часовом поясе устройства: ${h(browserZone)}. По умолчанию напоминание приходит в 09:00 в часовом поясе профиля в день срока, а если срок раньше — в момент срока. Указанное время напоминания заменяет это правило.</p>
      <div class="personal-actions"><button class="btn primary">Сохранить</button><button type="button" class="btn" data-action="cancel">Отмена</button></div></form>`;
  }
  function render() {
    if (!profile) { root.innerHTML = `<section class="panel personal-panel"><h2>Личный профиль</h2><p>Заполните профиль, чтобы начать работу.</p>${formProfile()}</section>`; return; }
    const today = new Intl.DateTimeFormat('en-CA', {timeZone: profile.timezone, year:'numeric', month:'2-digit', day:'2-digit'}).format(new Date());
    const visible = tab === 'day' ? tasks.filter(t => t.planned_day === today && !['done','cancelled'].includes(t.status)) : tasks;
    let content = '';
    if (tab === 'agent') {
      content = `<h2>Поручения агенту</h2><p>Поручите работу с задачами, поиск материалов или подготовку сообщения. Внешняя отправка требует подтверждения.</p><form data-form="agent" class="personal-form"><label class="field">Поручение<textarea name="request" maxlength="4000" required>${h(agentDraft)}</textarea></label><button class="btn primary">Отправить поручение</button></form><button type="button" class="btn" data-action="agent-refresh">Обновить</button><div data-agent-results></div>`;
    }
    else if (tab === 'connections') content = connectionsView();
    else if (tab === 'profile') content = `<h2>Профиль</h2>${formProfile()}`;
    else if (tab === 'projects') content = `<h2>Проекты</h2><form data-form="project" class="personal-actions"><input name="title" required maxlength="300" placeholder="Название проекта"><button class="btn primary">Добавить</button></form><ul class="personal-list">${projects.map(p => `<li>${h(p.title)}</li>`).join('') || '<li>Проектов пока нет.</li>'}</ul>`;
    else content = `<div class="personal-actions"><h2>${tab === 'day' ? 'Мой день' : 'Задачи'}</h2><button class="btn primary" data-action="new">Новая задача</button></div>${selected ? taskForm(selected === 'new' ? {} : selected) : ''}<ul class="personal-list">${visible.map(t => `<li><button type="button" class="personal-task" data-task="${t.id}"><strong>${h(t.title)}</strong><span>${h(statusLabels[t.status])}${t.planned_day ? ' · ' + h(t.planned_day) : ''}</span></button></li>`).join('') || '<li>Задач пока нет.</li>'}</ul>${selected && selected !== 'new' ? `<button type="button" class="btn" data-action="history">История</button>${history ? `<ul class="personal-list">${history.map(x => { const t = x.snapshot; const project = projects.find(p => p.id === t.project_id); return `<li><strong>Версия ${x.version} · ${h(localTime(x.created_at))}</strong><div>Название: ${h(t.title)} · Статус: ${h(statusLabels[t.status])}</div><div>Проект: ${h(project?.title || 'Без проекта')}</div><div>Ожидаемый результат: ${h(t.expected_result || '—')}</div><div>Результат: ${h(t.result_note || '—')}</div><div>Что мешает: ${h(t.blocker || '—')}</div><div>План на день: ${h(t.planned_day || '—')} · Срок: ${h(localTime(t.due_at))} · Напомнить: ${h(localTime(t.reminder_at))}</div></li>`; }).join('')}</ul>` : ''}` : ''}`;
    root.innerHTML = `<section class="panel personal-panel"><div class="personal-tabs" role="navigation" aria-label="Личный агент">${[['agent','Поручения'],['connections','Подключения'],['day','Мой день'],['tasks','Задачи'],['projects','Проекты'],['profile','Профиль']].map(([key,label]) => `<button type="button" class="btn ${tab === key ? 'primary' : ''}" data-tab="${key}">${label}</button>`).join('')}</div><div class="personal-error bad-text" role="alert"></div>${content}</section>`;
  }
  function connectionsView() {
    const form = (kind, title) => {
      const data = connections[kind] || {}, name = kind === 'mail' ? 'display_name' : 'calendar_name';
      const label = kind === 'mail' ? 'Имя отправителя (необязательно)' : 'Название календаря (необязательно)';
      return `<form data-form="connection" data-kind="${kind}" data-version="${h(data.version || 0)}" class="personal-form"><h3>${title}</h3><p data-connection-status>${data.version ? 'Настройки сохранены — требуется авторизация' : 'Не подключено'}</p>
        <label class="field">Сервис<select name="provider" required><option value="">Выберите сервис</option>${[['yandex','Яндекс'],['google','Google'],['microsoft','Microsoft'],['other','Другой']].map(([v,t]) => `<option value="${v}" ${data.provider === v ? 'selected' : ''}>${t}</option>`).join('')}</select></label>
        <label class="field">Адрес почты<input name="email" type="email" maxlength="254" required value="${h(data.email || '')}"></label>
        <label class="field">${label}<input name="${name}" maxlength="200" value="${h(data[name] || '')}"></label>
        <label class="field">Часовой пояс (необязательно)<input name="timezone" maxlength="100" value="${h(data.timezone || '')}" placeholder="${h(profile.timezone || 'Europe/Moscow')}"></label>
        <div class="personal-actions"><button class="btn primary" type="submit">Сохранить настройки</button><button class="btn" type="button" disabled>Авторизация ещё не настроена</button></div></form>`;
    };
    return `<h2>Подключения</h2><p>Сохранение настроек не подключает сервис и не предоставляет агенту доступ. Авторизация и действия с почтой и календарём пока не настроены.</p>${form('mail','Почта')}${form('calendar','Календарь')}`;
  }
  const agentStates = {queued:'В очереди',transcribing:'Распознаётся',running:'Выполняется',prepared:'Материал подготовлен',needs_input:'Нужно уточнение',needs_connection:'Нужно подключение',needs_confirmation:'Ожидает подтверждения',completed:'Завершено',failed:'Ошибка',cancelled:'Отменено'};
  const actionStates = {proposed:'Ожидает вашего решения',approved:'Подтверждено, ожидает отправки',sending:'Отправляется',sent:'Отправлено',uncertain:'Доставка не подтверждена',failed:'Отправить не удалось',cancelled:'Отменено'};
  const toolNames = {list_tasks:'Список задач',get_task:'Просмотр задачи',create_task:'Создание задачи',update_task:'Изменение задачи',analyze_calls:'Анализ звонков',search_web:'Поиск материалов',needs_connection:'Подключение сервиса',ask_owner:'Уточнение',contacts:'Проверка получателей',prepare_telegram_message:'Подготовка сообщения'};
  function evidenceText(step) {
    let e = step.evidence;
    if (typeof e === 'string') { try { e=JSON.parse(e); } catch (_) { return e; } }
    if (!e || typeof e !== 'object') return '';
    if (step.status === 'failed') return e.error || 'Действие не выполнено.';
    if (step.status !== 'ok') return '';
    if (['create_task','update_task','get_task'].includes(step.tool) && (!Number.isInteger(e.id) || !Number.isInteger(e.version))) return 'Нет подтверждённого номера и версии задачи.';
    if (step.tool === 'create_task' || step.tool === 'update_task') return `Задача №${e.id}, версия ${e.version}${e.title ? ': '+e.title : ''}`;
    if (step.tool === 'get_task') return `Задача №${e.id}, версия ${e.version}${e.title ? ': '+e.title : ''}`;
    if (step.tool === 'list_tasks') return `Найдено задач: ${(e.tasks || []).length}${e.more ? '; есть ещё' : ''}.`;
    if (step.tool === 'search_web' && Array.isArray(e)) return e.map(x => [x.title, x.url, x.snippet].filter(Boolean).join(' — ')).join('\n');
    if (step.tool === 'needs_connection') return [e.service ? 'Требуется подключение: '+e.service : '', e.prepared_text].filter(Boolean).join('\n');
    if (step.tool === 'ask_owner') return e.question || '';
    if (step.tool === 'analyze_calls') return e.answer || '';
    if (step.tool === 'prepare_telegram_message') return 'Сообщение подготовлено; для отправки требуется ваше подтверждение.';
    if (step.tool === 'contacts') return 'Доступные получатели проверены.';
    return '';
  }
  function renderAgentResults() {
    const out = root.querySelector('[data-agent-results]'); if (!out) return;
    const list = agentJobs.map(j => `<li><button class="personal-task" type="button" data-agent="${h(j.id)}"><strong>${h(j.request)}</strong><span>${h(agentStates[j.status] || 'Неизвестное состояние')} · ${h(localTime(j.created_at))}</span></button></li>`).join('') || '<li>Поручений пока нет.</li>';
    let detail = '';
    if (agentDetail) {
      const job=agentDetail.job, action=agentDetail.action;
      const steps=(agentDetail.steps || []).map(step => `<li><strong>${h(toolNames[step.tool] || 'Шаг поручения')}</strong> · ${h(step.status === 'ok' ? 'выполнен' : step.status === 'failed' ? 'не выполнен' : step.status === 'uncertain' ? 'Результат не подтверждён, проверьте задачу перед повтором' : 'выполняется')} · ${h(localTime(step.created_at))}<div style="white-space:pre-wrap">${h(evidenceText(step))}</div></li>`).join('') || '<li>Шагов пока нет.</li>';
      let actionHtml = '';
      if (action) {
        const actual = `Поручение от ${action.sender_name || ''}:\n\n${action.content || ''}`;
        const ready = action.status === 'proposed' && action.sender_name && action.recipient_name && action.token;
        const state = action.status === 'sent' && !action.message_id ? 'Доставка не подтверждена' : (actionStates[action.status] || 'Неизвестное состояние');
        actionHtml = `<h4>Сообщение в Telegram</h4><p>Получатель: ${h(action.recipient_name || 'Имя не подтверждено')} · ID ${h(action.recipient)}</p><p>Точный текст сообщения:</p><p style="white-space:pre-wrap">${h(actual)}</p><p>${h(state)}${action.status === 'sent' && action.message_id ? ' · ID сообщения '+h(action.message_id) : ''}</p>${ready ? '<button type="button" class="btn primary" data-action="agent-confirm">Подтвердить отправку</button> <button type="button" class="btn" data-action="agent-decline">Отменить отправку</button>' : ''}`;
      }
      detail = `<h3>Поручение №${h(job.id)}</h3><p style="white-space:pre-wrap">${h(job.request)}</p><p>Состояние: ${h(agentStates[job.status] || 'Неизвестное состояние')} · обновлено ${h(localTime(job.updated_at))}</p>${job.result ? `<h4>Материал / пояснение</h4><p style="white-space:pre-wrap">${h(job.result)}</p>` : ''}${job.error ? `<h4>Ошибка</h4><p style="white-space:pre-wrap">${h(job.error)}</p>` : ''}<h4>Ход поручения</h4><ul>${steps}</ul>${actionHtml}`;
    }
    out.innerHTML = `<ul class="personal-list">${list}</ul>${detail}`;
  }
  async function refreshAgent() {
    if (agentBusy || tab !== 'agent') return;
    const epoch=agentEpoch, selectedId=agentDetail?.job.id;
    const jobs=(await api('/api/personal/agent/jobs')).jobs;
    const detail=selectedId ? await api(`/api/personal/agent/jobs/${selectedId}`) : null;
    if (!root.isConnected || tab !== 'agent' || epoch !== agentEpoch) return;
    agentJobs=jobs; if (selectedId && agentDetail?.job.id === selectedId) agentDetail=detail;
    renderAgentResults();
  }
  root.addEventListener('input', e => {
    if (e.target.matches('form[data-form="agent"] textarea[name="request"]')) {
      agentDraft=e.target.value; agentPending=null; saveAgentDraft();
    }
  });
  root.addEventListener('click', async e => {
    const btn = e.target.closest('button'); if (!btn) return;
    try {
      if (btn.dataset.tab) { tab = btn.dataset.tab; selected = null; history = null; agentEpoch++; const epoch=agentEpoch; if (tab==='connections') { const response=await api('/api/personal/connections'); if (!root.isConnected || root._personalViewToken !== viewToken || tab !== 'connections' || epoch !== agentEpoch) return; connections=response.connections; } render(); if (tab==='agent') await refreshAgent(); }
      else if (btn.dataset.agent) { const id=Number(btn.dataset.agent); if (!Number.isSafeInteger(id) || id <= 0) return; agentEpoch++; const epoch=agentEpoch, detail=await api(`/api/personal/agent/jobs/${id}`); if (tab === 'agent' && epoch === agentEpoch) { agentDetail=detail; renderAgentResults(); } }
      else if (btn.dataset.action === 'agent-refresh') { await refreshAgent(); }
      else if (['agent-confirm','agent-decline'].includes(btn.dataset.action)) { if (agentBusy || !agentDetail?.action || agentDetail.action.status !== 'proposed' || !agentDetail.action.recipient_name || !agentDetail.action.sender_name) return; agentBusy=true; btn.disabled=true; const id=agentDetail.job.id; try { await api(`/api/personal/agent/jobs/${id}/confirm`,{method:'POST',body:{token:agentDetail.action.token,approve:btn.dataset.action==='agent-confirm'}}); agentDetail=await api(`/api/personal/agent/jobs/${id}`); } finally { agentBusy=false; } await refreshAgent(); }
      else if (btn.dataset.action === 'new') { selected = 'new'; history = null; render(); }
      else if (btn.dataset.action === 'cancel') { selected = null; history = null; render(); }
      else if (btn.dataset.task) { selected = tasks.find(t => t.id === Number(btn.dataset.task)); history = null; render(); }
      else if (btn.dataset.action === 'reload-task') { selected = tasks.find(t => t.id === selected.id) || null; history = null; render(); }
      else if (btn.dataset.action === 'history') { history = (await api(`/api/personal/tasks/${selected.id}/history`)).history; render(); }
    } catch (err) { (root.querySelector('.personal-error') || root).textContent = err.message; }
  });
  root.addEventListener('submit', async e => {
    e.preventDefault(); if (saving) return;
    const form = e.target, data = Object.fromEntries(new FormData(form));
    saving = true; form.querySelectorAll('button[type=submit], button:not([type])').forEach(button => { button.disabled = true; });
    const agentInput = form.dataset.form === 'agent' ? form.querySelector('textarea[name=request]') : null; if (agentInput) agentInput.readOnly = true;
    const connectionFields = form.dataset.form === 'connection' ? [...form.querySelectorAll('input, select')] : []; connectionFields.forEach(field => { field.disabled = true; });
    try {
      if (form.dataset.form === 'agent') { agentDraft=data.request; agentPending ||= crypto.randomUUID(); saveAgentDraft(); const response=await api('/api/personal/agent/jobs',{method:'POST',body:{request:agentDraft,request_id:agentPending}}); const jobs=(await api('/api/personal/agent/jobs')).jobs; const job=response.job || jobs.find(j => String(j.event_key || '').endsWith(':'+agentPending)); if (!job) throw new Error('Поручение принято ранее, но не найдено в последних записях.'); agentJobs=jobs; agentDetail=await api(`/api/personal/agent/jobs/${job.id}`); agentEpoch++; agentDraft=''; agentPending=null; saveAgentDraft(); render(); renderAgentResults(); return; }
      if (form.dataset.form === 'connection') { data.version=Number(form.dataset.version); const result=await api(`/api/personal/connections/${form.dataset.kind}`, {method:'PUT', body:data}); connections[form.dataset.kind]=result.connection; form.dataset.version=String(result.connection.version); form.querySelector('[data-connection-status]').textContent='Настройки сохранены — требуется авторизация'; return; }
      if (form.dataset.form === 'profile') await api('/api/personal/profile', {method:'PUT', body:data});
      if (form.dataset.form === 'project') await api('/api/personal/projects', {method:'POST', body:data});
      if (form.dataset.form === 'task') {
        data.reminders_enabled = form.elements.reminders_enabled.checked;
        data.project_id = data.project_id ? Number(data.project_id) : null;
        data.planned_day ||= null; data.due_at = browserTime(data.due_at); data.reminder_at = browserTime(data.reminder_at);
        const editing = selected && selected !== 'new'; if (editing) data.version = selected.version;
        await api(editing ? `/api/personal/tasks/${selected.id}` : '/api/personal/tasks', {method:editing ? 'PATCH' : 'POST', body:data});
        selected = null; history = null;
      }
      await load(); render();
    } catch (err) { const out = root.querySelector('.personal-error'); if (out) out.textContent = err.message; else alert(err.message); }
    finally { saving = false; connectionFields.forEach(field => { field.disabled = false; }); if (agentInput) agentInput.readOnly = false; form.querySelectorAll('button[type=submit], button:not([type])').forEach(button => { button.disabled = false; }); }
  });
  await load(); render();
  let refreshing = false, pendingRender = false;
  async function refreshPersonal() {
    if (!root.isConnected || root._personalViewToken !== viewToken || !root.querySelector('.personal-panel')) { clearInterval(timer); window.removeEventListener('focus', refreshPersonal); return; }
    if (refreshing || saving || document.hidden || !profile) return;
    refreshing = true;
    try {
      if (tab === 'agent') { await refreshAgent(); return; }
      const fresh = (await api('/api/personal/tasks')).tasks;
      if (!root.isConnected || root._personalViewToken !== viewToken || !root.querySelector('.personal-panel')) return;
      if (selected && selected !== 'new') {
        const current = fresh.find(t => t.id === selected.id);
        if (!current || current.version !== selected.version) {
          const notice = root.querySelector('.personal-error');
          if (notice && !notice.querySelector('[data-action="reload-task"]')) {
            notice.textContent = 'Задача изменилась. Перед обновлением скопируйте несохранённый текст. ';
            const reload = document.createElement('button');
            reload.type = 'button'; reload.className = 'btn small'; reload.dataset.action = 'reload-task';
            reload.textContent = 'Обновить задачу'; notice.appendChild(reload);
          }
        }
      }
      const changed = JSON.stringify(tasks) !== JSON.stringify(fresh);
      pendingRender ||= changed;
      tasks = fresh;
      // Never redraw an open editor or a form the user has begun filling in.
      const input = root.querySelector('form input:focus, form textarea:focus, form select:focus');
      const projectInput = root.querySelector('form[data-form="project"] input');
      if (pendingRender && !selected && tab !== 'profile' && tab !== 'agent' && tab !== 'connections' && !input && !(projectInput && projectInput.value)) { render(); pendingRender = false; }
    } catch (_) { /* Keep the current view if the connection is temporarily unavailable. */ }
    finally { refreshing = false; }
  }
  const timer = setInterval(refreshPersonal, 2000);
  window.addEventListener('focus', refreshPersonal);
};
