'use strict';

window.CRMDirectorNav = (() => {
  const groups = [
    {id: 'analytics', label: 'Аналитика', links: [
      {page: 'summary', description: 'Показатели отдела и менеджеров'},
      {page: 'reports', description: 'Итоги дня и отчёты бота'},
      {page: 'economics', label: 'Расходы', description: 'Стоимость звонков, робота и анализа', keywords: 'экономика бюджет затраты'},
    ]},
    {id: 'sales', label: 'Продажи', links: [
      {page: 'deals', description: 'Воронка продаж и сделки клиентов'},
      {page: 'deals', arg: 'contracts', module: 'contracts', label: 'Договоры', description: 'Подготовка договора и журнал документов', keywords: 'создать подготовить договор документ'},
    ]},
    {id: 'calls', label: 'Звонки', links: [
      {page: 'calls', label: 'Журнал звонков', description: 'Записи и результаты разговоров'},
      {page: 'callbacks', description: 'Кому и когда перезвонить', keywords: 'просроченные перезвоны задачи'},
      {page: 'callcenter', description: 'Клиенты, прозвон и статистика'},
      {page: 'robot', description: 'Кампании, номера, операторы и голос', keywords: 'озвучка'},
    ]},
    {id: 'team', label: 'Команда', links: [
      {page: 'staff', description: 'Сотрудники и показатели по каждому', keywords: 'менеджер добавить менеджера'},
      {page: 'learning', description: 'Подсказки по работе в CRM'},
    ]},
    {id: 'ai', label: 'ИИ и автоматизация', links: [
      {page: 'auto', description: 'Правила, условия и действия', keywords: 'новый сценарий'},
      {page: 'settings', arg: 'prompts', label: 'Инструкции для ИИ', description: 'Промты для итогов и разбора звонков', keywords: 'промпты шаблоны'},
      {gpt: true, module: 'gpt', label: 'AveronGPT', description: 'Помощник по звонкам и работе в CRM', keywords: 'чат спросить ии'},
    ]},
    {id: 'settings', label: 'Настройки', links: [
      {page: 'settings', arg: 'access', label: 'Учётные записи', description: 'Управление доступом к CRM', keywords: 'выдать доступ логин пароль учетка аккаунт'},
      {page: 'companies', description: 'Компании и подключённые модули'},
      {page: 'logs', description: 'Кто и когда изменил данные', keywords: 'аудит история изменений'},
    ]},
  ];
  const shortcuts = [
    {page: 'callcenter', arg: ':analysis', label: 'Разбор звонков', description: 'Колл-центр', keywords: 'разобрать звонки анализ', when: () => !S.me.readonly},
    {page: 'callcenter', arg: ':work', label: 'Прозвон колл-центра', description: 'Колл-центр', when: () => S.me.callcenter_mode === 'db'},
    {page: 'callcenter', arg: ':setup', label: 'Клиенты и номера', description: 'Колл-центр', when: () => S.me.callcenter_mode === 'db' && !S.me.readonly},
    {page: 'staff', arg: 'accounts', label: 'Учётные записи по компаниям', description: 'Сотрудники', keywords: 'выдать доступ', when: () => S.me.manage_accounts && S.me.platform && !S.me.support && !S.me.readonly},
  ];

  const label = link => link.label || PAGES[link.page].name;
  const available = link => (!link.module || hasModule(link.module)) && (!link.when || link.when())
    && (link.gpt ? typeof window.gptToggle === 'function' : allowed(link.page));
  const routeKey = link => link.page + '/' + (link.arg || '');
  const currentArg = () => S.page === 'settings' ? (S.arg === 'access' ? 'access' : 'prompts') : S.arg;
  const matchesPage = link => link.page === S.page && (!link.arg || link.arg === currentArg());
  const currentGroup = () => groups.find(group => group.links.some(matchesPage))
    || (S.page === 'queue' ? groups.find(group => group.id === 'team') : groups[0]);
  const routeAttrs = link => `data-go="${link.page}"${link.arg ? ` data-arg="${link.arg}"` : ''}`;

  function header() {
    return `<header class="director-header"><div class="director-header-in">
      ${brand}
      <nav class="director-nav" id="nav" aria-label="Разделы директора">
        ${groups.map(group => {
          const link = group.links.find(available);
          return link ? `<button type="button" data-director-group="${group.id}" ${routeAttrs(link)}>${esc(group.label)}${group.id === 'calls' ? '<span class="count" data-count="callbacks"></span>' : ''}</button>` : '';
        }).join('')}
        <button class="director-find" type="button" data-director-directory aria-haspopup="dialog">${svg('search')}Все разделы</button>
      </nav>
      <details class="director-profile">
        <summary>${ava(S.me.name)}<span><strong>${esc(S.me.name || 'Директор')}</strong><small>Директор</small></span>${svg('chev')}</summary>
        <div class="director-profile-menu card"><button class="ghost" type="button" data-theme-btn>${themeLabel()}</button>${switchBtn()}${exitLink()}</div>
      </details>
    </div></header>`;
  }

  function render() {
    const active = currentGroup().id;
    $$('[data-director-group]').forEach(button => {
      if (button.dataset.directorGroup === active) button.setAttribute('aria-current', 'true');
      else button.removeAttribute('aria-current');
    });
    $('#tabbar').hidden = true;
    $('.director-profile').open = false;
    renderAttention();
  }

  function subnav() {
    const group = currentGroup();
    const links = group.links.filter(available);
    if (links.length < 2 || group.id === 'sales') return '';
    return `<nav class="director-subnav" data-director-subnav aria-label="${esc(group.label)}">${links.map(link => link.gpt
      ? `<button type="button" data-gpt aria-controls="gpt" aria-expanded="${GPT.open}">${label(link)}</button>`
      : `<button type="button" ${routeAttrs(link)}${matchesPage(link) ? ' aria-current="page"' : ''}>${esc(label(link))}${link.page === 'callbacks' ? `<span class="count" data-count="callbacks">${S.counts?.callbacks || ''}</span>` : ''}</button>`
    ).join('')}</nav>`;
  }

  function title() {
    const links = currentGroup().links;
    const link = links.find(link => link.arg && matchesPage(link)) || links.find(matchesPage);
    return link && label(link);
  }

  let overdue = 0;
  function attention() {
    return `<div class="director-attention" hidden><div class="director-attention-in">
      <strong data-overdue-label></strong><button class="link" type="button" data-director-overdue>Открыть просроченные ${svg('chev')}</button>
    </div></div>`;
  }
  function renderAttention() {
    const banner = $('.director-attention');
    if (!banner) return;
    banner.hidden = S.page !== 'summary' || !overdue;
    $('[data-overdue-label]', banner).textContent = plainItems(overdue, 'просроченный перезвон', 'просроченных перезвона', 'просроченных перезвонов');
  }
  function setOverdue(items, today) {
    overdue = today ? items.filter(item => item.due_date && item.due_date < today).length : 0;
    renderAttention();
  }

  let sessionVisits = {};
  const visitKey = () => S.me.account_id != null ? 'crm-director-visits:' + S.me.account_id : '';
  const visits = () => {
    const value = visitKey() ? store.get(visitKey(), {}) : sessionVisits;
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  };
  const targets = () => [...groups.flatMap(group => group.links), ...shortcuts].filter(link => !link.gpt && available(link));
  function recordVisit() {
    const links = targets();
    const link = links.find(item => item.page === S.page && item.arg === currentArg())
      || links.find(item => item.page === S.page && !item.arg);
    if (!link) return;
    const key = routeKey(link);
    const counts = {...visits()};
    counts[key] = {count: Math.min(1000000, (Number(counts[key]?.count) || 0) + 1), at: Date.now()};
    if (visitKey()) store.set(visitKey(), counts);
    else sessionVisits = counts;
  }

  function directoryItem(link, compact = false) {
    const attrs = link.gpt ? 'data-gpt aria-controls="gpt"' : routeAttrs(link);
    return `<button class="${compact ? 'btn small' : 'director-directory-item'}" type="button" ${attrs}>${esc(label(link))}${compact ? '' : `<small>${esc(link.description)}</small>`}</button>`;
  }

  const normalize = text => text.toLowerCase().replaceAll('ё', 'е').trim();
  function openDirectory() {
    if ($('.director-directory')) return;
    const box = modal(`${modalHead('Все разделы')}<div class="rvm-body">
      <label class="field">Найти раздел или действие<input type="search" data-director-search placeholder="Например, договоры или выдать доступ"></label>
      <div data-director-results aria-live="polite"></div>
    </div>`, true);
    box.classList.add('director-directory');
    const input = $('[data-director-search]', box);
    const results = $('[data-director-results]', box);
    const renderResults = () => {
      const query = normalize(input.value);
      const words = query.split(/\s+/);
      const section = (heading, links) => links.length
        ? `<section><h3>${esc(heading)}</h3>${links.map(link => directoryItem(link)).join('')}</section>` : '';
      if (query) {
        const entries = [...groups.flatMap(group => group.links.map(link => ({link, group: group.label}))),
          ...shortcuts.map(link => ({link, group: ''}))];
        const score = link => {
          const name = normalize(label(link));
          return name === query ? 3 : name.includes(query) ? 2 : normalize(link.keywords || '').includes(query) ? 1 : 0;
        };
        const found = entries.filter(({link, group}) => available(link) &&
          words.every(word => normalize([label(link), link.description, link.keywords, group].join(' ')).includes(word)))
          .map(({link}) => link).sort((a, b) => score(b) - score(a));
        results.innerHTML = section('Результаты поиска', found) || '<p class="hint">Ничего не найдено. Попробуйте другое слово.</p>';
        return;
      }
      const counts = visits();
      const ranked = targets().filter(link => counts[routeKey(link)]?.count > 0)
        .sort((a, b) => counts[routeKey(b)].count - counts[routeKey(a)].count || counts[routeKey(b)].at - counts[routeKey(a)].at)
        .slice(0, 4);
      results.innerHTML = `<section class="director-frequent"><h3>Часто используемые</h3>${ranked.length
        ? `<div class="row">${ranked.map(link => directoryItem(link, true)).join('')}</div>`
        : '<p class="hint">Здесь появятся страницы, которые вы открываете.</p>'}</section>
        <div class="director-directory-grid">${groups.map(group => section(group.label, group.links.filter(available))).join('')}</div>`;
    };
    box.addEventListener('click', e => {
      if (e.target.closest('[data-go],[data-gpt]')) box.close();
    });
    input.addEventListener('input', renderResults);
    input.addEventListener('keydown', e => {
      if (e.key !== 'Enter' || !input.value.trim()) return;
      e.preventDefault();
      $('[data-go],[data-gpt]', results)?.click();
    });
    renderResults();
    input.focus();
  }

  return {header, render, subnav, title, attention, setOverdue, recordVisit, openDirectory};
})();
