// Раздел «Компании»: только у наших учёток с отметкой platform. Заводим компании, включаем модули,
// подключаем Манго, смотрим расход на ИИ и заходим посмотреть компанию её глазами.
'use strict';

VIEWS.companies = async view => {
  const d = await api('/api/platform/companies');
  const byId = Object.fromEntries(d.companies.map(c => [String(c.id), c]));
  const mods = c => c.modules.map(m => d.modules[m]).filter(Boolean).join(', ') || '—';
  const ai = c => `${fmt(c.ai_today)} ₽${c.ai_limit != null ? ` / ${fmt(c.ai_limit)} ₽` : ''}`;
  const state = c => c.home ? '<span class="chip acc">наша</span>'
    : c.working ? '<span class="chip good">работает</span>' : c.mango ? '<span class="chip warn">выключена</span>' : '<span class="chip warn">нет Манго</span>';
  setHead({sub: `${d.companies.length}`, actions: '<button class="btn primary" type="button" data-new>Новая компания</button>'});
  view.innerHTML = `<div class="page"><section class="card blk"><div class="tbl-wrap"><table class="tbl anim">
    <thead><tr><th>Компания</th><th>Состояние</th><th class="hide-m">Модули</th><th class="num">Менеджеров</th><th class="num">Звонков за 7 дней</th>
      <th class="num">ИИ сегодня</th><th class="hide-m">Заходили</th><th></th></tr></thead><tbody>
    ${d.companies.map((c, r) => `<tr data-css="--r:${Math.min(r, 30)}"><td><b>${esc(c.name)}</b><div class="s">№ ${c.id} · учёток ${c.logins}</div></td>
      <td>${state(c)}</td><td class="hide-m">${esc(mods(c))}</td><td class="num">${fmt(c.managers)}</td><td class="num">${fmt(c.calls_7d)}</td>
      <td class="num nw${c.ai_limit != null && c.ai_today >= c.ai_limit ? ' bad-text' : ''}">${ai(c)}</td><td class="hide-m nw">${fmtWhen(c.last_seen)}</td>
      <td class="nw"><button class="btn small" type="button" data-edit="${c.id}">Настроить</button>
        ${c.home ? '' : ` <button class="btn small" type="button" data-enter="${c.id}">Посмотреть</button>`}</td></tr>`).join('')}
    </tbody></table></div>
    <p class="hint">ИИ — примерный расход за сегодня по курсу из настроек сервера; на лимите разборы и AveronGPT компании останавливаются до завтра.
      «Посмотреть» открывает CRM компании глазами её директора, только для просмотра; каждый вход записывается.</p></section></div>`;
  $('[data-new]', $('#top')).addEventListener('click', () => companyForm(d, null, reload));
  $$('[data-edit]', view).forEach(b => b.addEventListener('click', () => companyForm(d, byId[b.dataset.edit], reload)));
  $$('[data-enter]', view).forEach(b => b.addEventListener('click', async () => {
    busy(b, true);
    try { const r = await api(`/api/platform/companies/${b.dataset.enter}/enter`, {method: 'POST', body: {}}); location.href = r.go || '/'; }
    catch (err) { busy(b, false); toast(err.message, true); }
  }));
};

function companyForm(d, c, onDone) {
  const on = c ? c.modules : d.default_modules, s = c?.settings || {};
  const box = modal(`${modalHead(c ? c.name : 'Новая компания', c ? `№ ${c.id}` : 'Компания появится выключенной: звонки пойдут, когда подключим её Манго.')}
    <div class="rvm-body"><div class="outform">
      <label class="field">Название<input data-v="name" maxlength="80" value="${esc(c?.name || '')}" placeholder="ООО Ромашка"></label>
      ${c ? '' : `<label class="field">Директор<input data-v="director" maxlength="80" placeholder="Имя директора компании"></label>
      <label class="field">Логин директора<input data-v="login" maxlength="40" autocapitalize="none" spellcheck="false" placeholder="придумаем из названия"></label>`}
      <div class="field">Модули${Object.entries(d.modules).map(([k, name]) =>
        `<label class="rb-check"><input type="checkbox" data-m="${k}"${on.includes(k) ? ' checked' : ''}> ${esc(name)}</label>`).join('')}</div>
      <label class="field">Таблица колл-центра<input data-v="callcenter_sheet" value="${esc(s.callcenter_sheet || '')}" placeholder="ссылка на Google Таблицу"></label>
      <label class="field">E-mail в договорах<input data-v="contract_email" maxlength="120" value="${esc(s.contract_email || '')}"></label>
      <label class="field">Лимит на ИИ, ₽ в день<input data-v="ai_day_limit_rub" inputmode="decimal" value="${esc(s.ai_day_limit_rub ?? (c ? '' : '300'))}" placeholder="без лимита"></label>
      <div class="row wide"><button class="btn primary" type="button" data-save>${c ? 'Сохранить' : 'Создать'}</button></div></div><div data-out></div>
      ${c && !c.home ? `<div class="outform" data-css="margin-top:18px"><h3>Манго компании</h3>
        <p class="hint">${c.mango ? 'Ключи уже сохранены, новые заменят их.' : 'Личный кабинет Манго → Интеграции → API: уникальный код ВАТС и ключ для подписи.'} Сначала проверю их запросом баланса.</p>
        <label class="field">Уникальный код ВАТС<input data-k="key" autocomplete="off" spellcheck="false"></label>
        <label class="field">Ключ для подписи<input data-k="salt" type="password" autocomplete="new-password" spellcheck="false"></label>
        <div class="row wide"><button class="btn" type="button" data-mango>Подключить Манго</button></div></div>` : ''}</div>`);
  const v = k => $(`[data-v="${k}"]`, box)?.value || '';
  $('[data-save]', box).addEventListener('click', async e => {
    const btn = e.currentTarget;
    busy(btn, true);
    const body = {name: v('name'), modules: $$('[data-m]', box).filter(i => i.checked).map(i => i.dataset.m),
      settings: {callcenter_sheet: v('callcenter_sheet'), contract_email: v('contract_email'), ai_day_limit_rub: v('ai_day_limit_rub')}};
    try {
      if (c) {
        await api(`/api/platform/companies/${c.id}`, {method: 'POST', body});
        busy(btn, false); toast('Сохранено');
      } else {
        const r = await api('/api/platform/companies', {method: 'POST', body: {...body, director: v('director'), login: v('login')}});
        accountBox($('[data-out]', box), r.login, r.password);
        doneBtn(btn, 'Создано');
      }
      onDone && onDone();
    } catch (err) { busy(btn, false); toast(err.message, true); }
  });
  $('[data-mango]', box)?.addEventListener('click', async e => {
    const btn = e.currentTarget;
    busy(btn, true);
    try {
      const r = await api(`/api/platform/companies/${c.id}/mango`, {method: 'POST', body: {key: $('[data-k="key"]', box).value, salt: $('[data-k="salt"]', box).value}});
      $$('[data-k]', box).forEach(i => { i.value = ''; });
      doneBtn(btn, 'Подключено'); toast(r.balance != null ? `Манго подключена, баланс ${fmt(r.balance)} ₽` : 'Манго подключена');
      onDone && onDone();
    } catch (err) { busy(btn, false); toast(err.message, true); }
  });
}

// «Сменить учётку»: любая учётка любой компании без пароля. Своя сессия ждёт на сервере, «Вернуться к себе» — /support-exit.
async function switchAccount() {
  closeDrawer();
  const box = modal(`${modalHead('Сменить учётку', 'Вход без пароля, с полными правами этой учётки. Вернуться к себе — кнопкой в шапке.')}
    <div class="rvm-body"><label class="field">Поиск<input data-q placeholder="имя, логин, компания" autocomplete="off"></label><div data-list><p class="hint">Загружаю…</p></div></div>`, true);
  const ROLE = {director: 'директор', manager: 'менеджер', client: 'клиент'};
  let d;
  try { d = await api('/api/platform/accounts'); } catch (e) { $('[data-list]', box).innerHTML = `<p class="bad-text">${esc(e.message)}</p>`; return; }
  const draw = () => {
    const q = $('[data-q]', box).value.trim().toLowerCase();
    const list = d.accounts.filter(a => !q || [a.name, a.login, a.company, a.client, a.ext].some(v => String(v || '').toLowerCase().includes(q)));
    const groups = [...new Set(list.map(a => a.company))];
    $('[data-list]', box).innerHTML = list.length ? groups.map(g => `<h3>${esc(g)}</h3><div class="sheet-list">${list.filter(a => a.company === g).map(a =>
      `<button type="button" data-acc="${a.id}"${a.id === d.current ? ' aria-current="page"' : ''}><span><b>${esc(a.name)}</b> · ${esc(ROLE[a.role] || a.role)}${a.ext ? ', доб. ' + esc(a.ext) : ''}
        <span class="hint">${a.login ? esc(a.login) : 'старая ссылка'}${a.id === d.current ? ' · сейчас' : ''}</span></span>${svg('chev')}</button>`).join('')}</div>`).join('')
      : '<p class="hint">Никого не нашлось.</p>';
  };
  draw();
  $('[data-q]', box).addEventListener('input', draw);
  $('[data-q]', box).focus();
  $('[data-list]', box).addEventListener('click', async e => {
    const b = e.target.closest('[data-acc]');
    if (!b) return;
    busy(b, true);
    try { const r = await api(`/api/platform/accounts/${b.dataset.acc}/switch`, {method: 'POST', body: {}}); location.href = r.go || '/'; }
    catch (err) { busy(b, false); toast(err.message, true); }
  });
}
window.switchAccount = switchAccount;
