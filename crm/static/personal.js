/* The personal workspace uses only the authenticated user's own API. */
VIEWS.personal = async function personalView(root) {
  let tab = 'day', selected = null, projects = [], tasks = [], profile = null, history = null;
  const statusLabels = {planned: 'Запланировано', in_progress: 'В работе', done: 'Готово', cancelled: 'Отменено'};
  const h = value => esc(value == null ? '' : String(value));
  // The browser interprets entered date-times in its own zone; show that zone explicitly.
  const browserTime = value => value ? new Date(value).toISOString() : null;
  const browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'часовой пояс устройства';
  const localTime = value => value ? new Date(value).toLocaleString('ru-RU') : '—';
  let saving = false;
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
      <p class="hint">Время вводится в часовом поясе устройства: ${h(browserZone)}. Напоминания подключим на этапе Telegram; сейчас дата только сохраняется.</p>
      <div class="personal-actions"><button class="btn primary">Сохранить</button><button type="button" class="btn" data-action="cancel">Отмена</button></div></form>`;
  }
  function render() {
    if (!profile) { root.innerHTML = `<section class="panel personal-panel"><h2>Личный профиль</h2><p>Заполните профиль, чтобы начать работу.</p>${formProfile()}</section>`; return; }
    const today = new Intl.DateTimeFormat('en-CA', {timeZone: profile.timezone, year:'numeric', month:'2-digit', day:'2-digit'}).format(new Date());
    const visible = tab === 'day' ? tasks.filter(t => t.planned_day === today && !['done','cancelled'].includes(t.status)) : tasks;
    let content = '';
    if (tab === 'profile') content = `<h2>Профиль</h2>${formProfile()}`;
    else if (tab === 'projects') content = `<h2>Проекты</h2><form data-form="project" class="personal-actions"><input name="title" required maxlength="300" placeholder="Название проекта"><button class="btn primary">Добавить</button></form><ul class="personal-list">${projects.map(p => `<li>${h(p.title)}</li>`).join('') || '<li>Проектов пока нет.</li>'}</ul>`;
    else content = `<div class="personal-actions"><h2>${tab === 'day' ? 'Мой день' : 'Задачи'}</h2><button class="btn primary" data-action="new">Новая задача</button></div>${selected ? taskForm(selected === 'new' ? {} : selected) : ''}<ul class="personal-list">${visible.map(t => `<li><button type="button" class="personal-task" data-task="${t.id}"><strong>${h(t.title)}</strong><span>${h(statusLabels[t.status])}${t.planned_day ? ' · ' + h(t.planned_day) : ''}</span></button></li>`).join('') || '<li>Задач пока нет.</li>'}</ul>${selected && selected !== 'new' ? `<button type="button" class="btn" data-action="history">История</button>${history ? `<ul class="personal-list">${history.map(x => { const t = x.snapshot; const project = projects.find(p => p.id === t.project_id); return `<li><strong>Версия ${x.version} · ${h(localTime(x.created_at))}</strong><div>Название: ${h(t.title)} · Статус: ${h(statusLabels[t.status])}</div><div>Проект: ${h(project?.title || 'Без проекта')}</div><div>Ожидаемый результат: ${h(t.expected_result || '—')}</div><div>Результат: ${h(t.result_note || '—')}</div><div>Что мешает: ${h(t.blocker || '—')}</div><div>План на день: ${h(t.planned_day || '—')} · Срок: ${h(localTime(t.due_at))} · Напомнить: ${h(localTime(t.reminder_at))}</div></li>`; }).join('')}</ul>` : ''}` : ''}`;
    root.innerHTML = `<section class="panel personal-panel"><div class="personal-tabs" role="navigation" aria-label="Личный агент">${[['day','Мой день'],['tasks','Задачи'],['projects','Проекты'],['profile','Профиль']].map(([key,label]) => `<button type="button" class="btn ${tab === key ? 'primary' : ''}" data-tab="${key}">${label}</button>`).join('')}</div><div class="personal-error bad-text" role="alert"></div>${content}</section>`;
  }
  root.addEventListener('click', async e => {
    const btn = e.target.closest('button'); if (!btn) return;
    try {
      if (btn.dataset.tab) { tab = btn.dataset.tab; selected = null; history = null; render(); }
      else if (btn.dataset.action === 'new') { selected = 'new'; history = null; render(); }
      else if (btn.dataset.action === 'cancel') { selected = null; history = null; render(); }
      else if (btn.dataset.task) { selected = tasks.find(t => t.id === Number(btn.dataset.task)); history = null; render(); }
      else if (btn.dataset.action === 'history') { history = (await api(`/api/personal/tasks/${selected.id}/history`)).history; render(); }
    } catch (err) { (root.querySelector('.personal-error') || root).textContent = err.message; }
  });
  root.addEventListener('submit', async e => {
    e.preventDefault(); if (saving) return;
    const form = e.target, data = Object.fromEntries(new FormData(form));
    saving = true; form.querySelectorAll('button[type=submit], button:not([type])').forEach(button => { button.disabled = true; });
    try {
      if (form.dataset.form === 'profile') await api('/api/personal/profile', {method:'PUT', body:data});
      if (form.dataset.form === 'project') await api('/api/personal/projects', {method:'POST', body:data});
      if (form.dataset.form === 'task') {
        data.project_id = data.project_id ? Number(data.project_id) : null;
        data.planned_day ||= null; data.due_at = browserTime(data.due_at); data.reminder_at = browserTime(data.reminder_at);
        const editing = selected && selected !== 'new'; if (editing) data.version = selected.version;
        await api(editing ? `/api/personal/tasks/${selected.id}` : '/api/personal/tasks', {method:editing ? 'PATCH' : 'POST', body:data});
        selected = null; history = null;
      }
      await load(); render();
    } catch (err) { const out = root.querySelector('.personal-error'); if (out) out.textContent = err.message; else alert(err.message); }
    finally { saving = false; form.querySelectorAll('button[type=submit], button:not([type])').forEach(button => { button.disabled = false; }); }
  });
  await load(); render();
};
