// AveronGPT: помощник внизу меню (на телефоне круглая кнопка над нижней панелью). Окно чата справа,
// не закрывает страницу. С вопросом уходит, где человек сейчас и текст с экрана, чтобы «а это что?»
// понималось без уточнений. Кнопки разделов в ответе ведут через обычный data-go. Новому клиенту окно
// открывается само и предлагает тур по кабинету с подсветкой мест на странице.
'use strict';

const GPT = {open: false, items: null, busy: false, tour: null};
const GPT_ORB = '<span class="gpt-orb" aria-hidden="true"></span>';

// Подсказки над полем ввода: под страницу и роль.
function gptChips() {
  const role = S.me.role, page = S.page;
  const byPage = {
    cab: ['Что значат цифры сверху?', 'Почему дозвон такой?', 'Как считается сумма к оплате?'],
    robot: role === 'client' ? ['Как запустить обзвон?', 'Что значит «лиды»?', 'Сколько стоил робот за месяц?'] : ['Как запустить кампанию?', 'Сколько стоит минута робота?', 'Чем закончились звонки за неделю?'],
    summary: ['Почему дозвон изменился?', 'Кто из менеджеров просел за неделю?', 'Что значит «потеряно с шансом»?'],
    queue: ['Что делать, если не взяли трубку?', 'Почему очередь пустая?', 'Как поставить перезвон?'],
    deals: ['Почему сделка зависла?', 'Где клиенты отваливаются чаще всего?', 'Как отметить договор?'],
    calls: ['Где я чаще всего теряю клиента?', 'Как открыть разбор звонка?', 'Что значит «горячий, потерян»?'],
    me: ['Где мои слабые места?', 'Что у меня получилось лучше всего на этой неделе?', 'Откуда берётся фокус на завтра?'],
    callcenter: ['Какой клиент сейчас лучше всех по дозвону?', 'Сколько всего к оплате по клиентам?', 'Как дать клиенту доступ?'],
    staff: ['Как добавить менеджера?', 'Как назначить тренировку?', 'Что такое память бота?'],
  };
  const base = role === 'client' ? ['Покажи, что где', 'Что значит «Неактуально»?']
    : role === 'manager' ? ['Покажи, что где', 'Где мои перезвоны на сегодня?'] : ['Покажи, что где', 'Где посмотреть оплаченные сделки?'];
  return [...(byPage[page] || []), ...base].slice(0, 4);
}

function gptButton() {
  return `<button type="button" class="gpt-btn" data-gpt aria-expanded="false" aria-controls="gpt">${GPT_ORB}<span>AveronGPT</span></button>`;
}

function gptShell() {
  if ($('#gpt')) return;
  root.insertAdjacentHTML('beforeend', `
    <button type="button" class="gpt-fab" data-gpt aria-label="Открыть AveronGPT" aria-expanded="false" aria-controls="gpt">${GPT_ORB}</button>
    <aside class="gpt" id="gpt" role="dialog" aria-label="AveronGPT" aria-modal="false" hidden>
      <div class="gpt-h">${GPT_ORB}<b>AveronGPT</b><span class="sp"></span>
        <button type="button" class="ghost small" data-gpt-clear title="Начать заново">Очистить</button>
        <button type="button" class="gpt-x" data-gpt-close aria-label="Закрыть">${svg('x')}</button></div>
      <div class="gpt-b" data-gpt-feed aria-live="polite"></div>
      <div class="gpt-chips" data-gpt-chips></div>
      <form class="gpt-in" data-gpt-form>
        <textarea id="gpt-q" rows="1" maxlength="2000" placeholder="Спросите про сайт или цифры" aria-label="Вопрос AveronGPT"></textarea>
        <button type="submit" class="gpt-send" aria-label="Отправить"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 19V5M6 11l6-6 6 6"/></svg></button>
      </form>
      <p class="gpt-note">Помощник может ошибаться. Цифры он берёт из CRM, проверяйте важное на странице.</p>
    </aside>`);
  const form = $('[data-gpt-form]'), ta = $('#gpt-q');
  form.addEventListener('submit', e => { e.preventDefault(); gptAsk(ta.value); });
  ta.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); gptAsk(ta.value); } });
  ta.addEventListener('input', () => { ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 140) + 'px'; });
  $('[data-gpt-close]').addEventListener('click', () => gptToggle(false));
  $('[data-gpt-clear]').addEventListener('click', async () => {
    try { await api('/api/gpt/clear', {method: 'POST', body: {}}); GPT.items = []; gptDraw(); } catch (e) { toast(e.message, true); }
  });
  $('[data-gpt-chips]').addEventListener('click', e => {
    const b = e.target.closest('[data-chip]'); if (!b) return;
    if (b.dataset.chip === 'Покажи, что где') { gptTour(); return; }
    gptAsk(b.dataset.chip);
  });
  $('[data-gpt-feed]').addEventListener('click', e => {
    if (e.target.closest('[data-tour]')) { gptTour(); return; }
    if (e.target.closest('[data-go]') && innerWidth <= 900) gptToggle(false); // на телефоне окно на весь экран: уступаем странице
  });
}

document.addEventListener('click', e => { if (e.target.closest('[data-gpt]')) gptToggle(); });
addEventListener('keydown', e => { if (e.key === 'Escape' && GPT.open && !drawerOpen()) gptToggle(false); });

async function gptToggle(on = !GPT.open) {
  gptShell();
  GPT.open = on;
  const box = $('#gpt');
  box.hidden = !on;
  document.body.classList.toggle('gpt-on', on);
  $$('[data-gpt]').forEach(b => b.setAttribute('aria-expanded', String(on)));
  if (!on) return;
  if (GPT.items === null) {
    $('[data-gpt-feed]').innerHTML = '<div class="sk line"></div><div class="sk line" data-css="width:70%;margin-top:8px"></div>';
    try { GPT.items = (await api('/api/gpt')).items; } catch (e) { GPT.items = []; toast(e.message, true); }
  }
  gptDraw();
  if (innerWidth > 900) $('#gpt-q').focus();
}

function gptHello() {
  const first = S.me.role === 'client' || !S.me.name || S.me.name === 'Директор' ? '' : String(S.me.name).split(' ')[0];
  const who = `Здравствуйте${first ? ', ' + esc(first) : ''}!`;
  const page = PAGES[S.page]?.name;
  return `<div class="gpt-m ai"><p>${who} Я AveronGPT. Подскажу, где что на сайте, и объясню цифры${page ? ` на странице «${esc(page)}»` : ''}.</p>
    ${tourSteps(false).length ? `<p><button class="gpt-go" type="button" data-tour>Показать страницу по шагам</button></p>` : ''}</div>`;
}

function gptDraw() {
  const feed = $('[data-gpt-feed]');
  if (!feed) return;
  feed.innerHTML = gptHello() + (GPT.items || []).map(q => `<div class="gpt-m me">${esc(q.question)}</div>` +
    (q.html ? `<div class="gpt-m ai">${q.html}</div>` : '<div class="gpt-m ai wait"><span class="gpt-dots"><i></i><i></i><i></i></span></div>')).join('');
  feed.scrollTop = feed.scrollHeight;
  $('[data-gpt-chips]').innerHTML = GPT.busy ? '' : gptChips().map(c => `<button type="button" class="chip-q" data-chip="${esc(c)}">${esc(c)}</button>`).join('');
}

// Что сейчас на экране: заголовок, фильтры и текст страницы. Только то, что человек и так видит.
function gptPageText() {
  const top = $('#top')?.innerText || '', view = $('#view')?.innerText || '';
  const drawer = drawerOpen() ? $('#drawer')?.innerText || '' : '';
  return [top, view, drawer && 'Открыта панель: ' + drawer].filter(Boolean).join('\n').replace(/\n{3,}/g, '\n\n').slice(0, 6000);
}

async function gptAsk(text) {
  const q = String(text || '').trim();
  if (!q || GPT.busy) return;
  if (TOUR_ASK.test(q) && tourSteps(false).length) { $('#gpt-q').value = ''; gptTour(); return; }
  if (S.me.readonly) { toast('Это вход только для просмотра: спрашивать можно под своим логином.', true); return; }
  const ta = $('#gpt-q');
  ta.value = ''; ta.style.height = 'auto';
  GPT.busy = true;
  const pending = {question: q, html: null};
  GPT.items.push(pending);
  gptDraw();
  try {
    const page = S.page + (S.arg ? '/' + S.arg : '');
    const r = await api('/api/gpt', {method: 'POST', body: {question: q, page, page_text: gptPageText(),
      filters: {period: S.f.period, ext: S.f.ext, owner: typeof RB === 'object' ? RB.owner : ''}}});
    Object.assign(pending, r);
  } catch (e) {
    pending.html = `<p class="bad-text">${esc(e.message)}</p>`;
  }
  GPT.busy = false;
  gptDraw();
}

// ---------- экскурсия по странице ----------
// Подсветка блока, рядом карточка «что это и зачем», кнопки Назад, Дальше, Пропустить. Без модели: быстро
// и бесплатно. Новому клиенту экскурсия начинается сама при первом входе на страницу; остальным по просьбе
// («Покажи, что где» или кнопка в приветствии). Селектор «card:Заголовок» ищет карточку по её заголовку.
// Шаги, которых нет на экране (пустой список, нет данных), пропускаются.
const TOUR_SEEN = 'crm-tour-seen:';
const TOUR_ASK = /(покажи|проведи|расскажи|объясни)[^?]{0,30}(что где|что тут|где что|страниц|сайт|кабинет|раздел)|что где|где что|экскурси|по шагам|как тут вс[её] устроено/i;
const TOUR_MENU = ['#nav, #tabbar', 'Разделы сайта', 'Здесь все разделы. Нажмите на раздел, чтобы перейти в него. На телефоне разделы внизу экрана.'];
const TOUR_HELPER = ['.gpt-btn, .gpt-fab', 'AveronGPT', 'Если что-то непонятно, спросите меня обычными словами: объясню цифры на странице и подскажу, где что лежит. Экскурсию можно повторить кнопкой «Покажи, что где».'];
const TOUR = {
  cab: () => [
    ['.kpis', 'Главные цифры', 'Сколько номеров вы передали, по скольким уже звонили, сколько взяли трубку и сколько клиентов заинтересовались. Под цифрами проценты от базы и от обзвоненных.'],
    ['card:Итог по номерам', 'Итог по номерам', 'Чем закончились звонки по всей вашей базе: заинтересованы, перезвон, неактуально, не дозвонились. Так видно, сколько номеров ещё в работе.'],
    ['card:Заинтересованные', 'Заинтересованные', 'Ваши будущие клиенты: телефон, дата звонка и комментарий оператора. С этого списка стоит начинать день.'],
    ['card:Перезвоны', 'Перезвоны', 'Кто просил перезвонить и когда. Оператор позвонит сам в назначенный день.'],
    ['card:Звонки по дням', 'Звонки по дням', 'Сколько звонков было каждый день: первичные и повторные. Наведите или нажмите на столбик, чтобы увидеть дозвон и заинтересованных за день.'],
    ['details.more', 'Подробнее', 'Здесь расчёт по вашему тарифу с итогом к оплате и цифры по каждой загруженной базе. Нажмите, чтобы раскрыть.'],
  ],
  robot: role => [
    ['#view .tabs', 'Вкладки робота', role === 'client'
      ? 'Кампании: кому и когда звонить. Номера: список контактов и стоп-лист. Звонки: каждый разговор с итогом и записью. Операторы: как робот представляется и ведёт разговор.'
      : 'Кампании, Номера, Звонки, Операторы, а ещё Голос (тестовый звонок, манера речи) и Настройки (себестоимость минуты и цены для клиентов).'],
    ['card:Кампании', 'Кампании', 'Кампания говорит роботу, кого обзванивать, в какие дни и часы. Здесь её создают, запускают и ставят на паузу.'],
    ['#rb-body .kpis', 'Итоги звонков', 'Сколько звонков сделал робот, сколько дозвонов и заинтересованных, сколько минут и сколько это стоило.'],
    ['card:Номера для обзвона', 'Номера', 'Контакты, которые обзванивает робот. Загружаются из файла CSV или Excel. Кого добавить в стоп-лист, тому робот звонить не будет.'],
  ],
  summary: () => [
    ['#top', 'Период и менеджер', 'Выберите, за какой срок смотреть цифры и по кому: по всему отделу или по одному менеджеру.'],
    ['.kpis', 'Главные цифры', 'Наборы, дозвон, разговоры с результатом и встречи с КП. Стрелка показывает сравнение с прошлым таким же периодом. Нажмите на «?», чтобы увидеть, как считается цифра.'],
    ['card:По дням', 'По дням', 'Как шла работа день за днём. Наведите на точку, чтобы увидеть цифры дня.'],
    ['card:Менеджеры', 'Менеджеры', 'Каждый менеджер в одной строке: наборы, дозвон, время на линии, результат и частая ошибка. Нажмите на строку, откроется карточка менеджера.'],
    ['details.more', 'Когда лучше звонить', 'Карта дозвона по дням недели и часам: видно, в какое время чаще берут трубку.'],
  ],
  queue: () => [
    ['.qline', 'Очередь', 'Сколько номеров ждут звонка сейчас и сколько вернутся позже.'],
    ['[data-work] .work', 'Текущий номер', 'Нажмите «Позвонить»: Mango наберёт номер на ваш телефон. После разговора отметьте итог, для перезвона и встречи укажите дату.'],
    ['card:История клиента', 'История клиента', 'Прошлые звонки и договорённости с этим номером, чтобы не начинать разговор с нуля.'],
    ['card:Дальше по очереди', 'Дальше по очереди', 'Следующие номера. Можно выбрать любой. Кто не взял трубку, вернётся через 2 часа, до 3 попыток.'],
    ['#view .empty', 'Очередь пуста', 'Номера добавляют в разделе «Источники», после этого они появятся здесь.'],
  ],
  calls: () => [
    ['#view .row', 'Фильтры', 'Выберите итог звонка (результат, потерян, без шанса) или тип звонка, чтобы найти нужные разговоры.'],
    ['#view .tbl', 'Журнал звонков', 'Каждый звонок в одной строке. Нажмите на звонок: откроется разбор бота с ошибками и расшифровкой, там же запись.'],
  ],
  callbacks: () => [
    ['#view .card', 'Перезвоны', 'Все договорённости перезвонить и встречи по датам, просроченные сверху. Перезвон можно перенести, закрыть или отменить.'],
  ],
  deals: () => [
    ['#view .tabs', 'Сделки и договоры', 'Во вкладке «Договоры» журнал договоров и генератор договора по ИНН.'],
    ['.stages', 'Этапы сделки', 'Путь клиента от базы номеров до оплаты. Нажмите на этап, чтобы увидеть сделки только на нём.'],
    ['#view section.card', 'Список сделок', 'Сделки с фильтром: в работе, зависшие, отказ, оплачено. Сделка зависает, если долго без движения. Нажмите на сделку, чтобы открыть карточку.'],
    ['details.more', 'Как считаются сделки', 'Что CRM отмечает сама по звонкам и презентациям, а что менеджер отмечает руками.'],
  ],
  me: () => [
    ['#view .tabs', 'Обзор и тренировки', 'В «Обзоре» ваши цифры и разбор, в «Тренировках» назначенные и пройденные тренировки.'],
    ['[data-body] .kpis', 'Ваши цифры', 'Наборы, дозвон и разговоры с результатом за 7 и 30 дней.'],
    ['card:Слабые места', 'Слабые места', 'Где чаще всего теряется клиент, с примерами звонков. Каждый вечер в 19:00 бот обновляет фокус на завтра.'],
    ['card:Что получается', 'Что получается', 'Что у вас выходит хорошо: это стоит повторять.'],
  ],
  staff: () => [
    ['#view .tbl', 'Сотрудники', 'Все люди отдела: подключён ли Telegram-бот и вход в CRM. Нажмите на человека, откроется его карточка с цифрами, тренировками и доступами.'],
  ],
  callcenter: () => [
    ['.kpis', 'Главные цифры', 'Сколько лидов обзвонила Ольга, дозвон, заинтересованные и сумма по тарифу для всех клиентов.'],
    ['card:По клиентам', 'По клиентам', 'Каждый клиент колл-центра в строке. Нажмите, чтобы открыть кабинет так, как его видит клиент, и взять ссылку для клиента.'],
    ['card:Звонки по дням', 'Звонки по дням', 'Первичные и повторные звонки по дням.'],
    ['card:Кому Ольге перезвонить', 'Кому перезвонить', 'Перезвоны Ольги с датой напоминания и комментарием.'],
    ['details.more', 'Базы и источники', 'Цифры по каждой загруженной базе и источнику номеров.'],
  ],
  sources: () => [
    ['#view .tabs', 'Источники', '«Подбор доноров» подсказывает, где взять номера под нишу клиента. «Номера в очереди» добавляет номера в очередь менеджеров.'],
    ['[data-body]', 'Подбор', 'Укажите нишу и город: страница покажет подходящие места на 2ГИС и Яндекс Картах и закладку «Собрать номера».'],
  ],
  reports: () => [
    ['[data-feed]', 'Отчёты бота', 'Отчёт на планёрку утром, вечерний отчёт, итоги дня менеджеров, сводки за неделю и месяц.'],
  ],
  settings: () => [
    ['#view .tabs', 'Настройки', '«Промты»: как бот разбирает звонки и пишет отчёты, правка обычными словами. «Доступы»: логины и пароли директора, менеджеров и клиентов.'],
  ],
};

function tourFind(sel) {
  const visible = el => { const r = el?.getBoundingClientRect(); return !!r && r.width > 24 && r.height > 24; };
  if (sel.startsWith('card:')) {
    const title = sel.slice(5);
    return $$('#view .card-h h2').map(h => h.closest('.card')).find(c => visible(c) && c.querySelector('.card-h h2').textContent.trim().startsWith(title)) || null;
  }
  return $$(sel).find(visible) || null;
}

function tourSteps(full) {
  const page = (TOUR[S.page] || (() => []))(S.me.role);
  return (full ? [TOUR_MENU, ...page, TOUR_HELPER] : page).filter(([sel]) => tourFind(sel));
}

// full: со ссылкой на меню и на помощника (первый вход клиента). Шагов нет: спрашиваем модель как раньше.
function gptTour(full = false) {
  const steps = tourSteps(full);
  if (!steps.length) { gptAsk('Покажи, что где на этой странице'); return; }
  tourEnd();
  GPT.tour = {steps, i: 0, reopen: GPT.open, page: S.page};
  if (GPT.open) gptToggle(false);
  GPT.tour.reopen = GPT.tour.reopen && innerWidth > 900;
  root.insertAdjacentHTML('beforeend', `<div class="tour" data-tour-box>
    <div class="tour-hole" data-tour-hole></div>
    <div class="tour-card" data-tour-card role="dialog" aria-modal="true" aria-labelledby="tour-t" aria-describedby="tour-p"></div></div>`);
  const box = $('[data-tour-box]');
  box.addEventListener('click', e => {
    const b = e.target.closest('[data-tour-do]');
    if (!b) return;
    const act = b.dataset.tourDo;
    if (act === 'skip' || act === 'done') tourEnd(true);
    else { GPT.tour.i += act === 'next' ? 1 : -1; tourShow(); }
  });
  addEventListener('resize', tourPlace);
  addEventListener('scroll', tourPlace, true);
  tourShow();
}

function tourShow() {
  const t = GPT.tour;
  if (!t) return;
  const [sel, title, text] = t.steps[t.i];
  const el = tourFind(sel);
  if (!el) { tourEnd(true); return; }
  t.el = el;
  const last = t.i === t.steps.length - 1;
  $('[data-tour-card]').innerHTML = `<div class="tour-top"><span class="hint">${t.i + 1} из ${t.steps.length}</span>
      <button type="button" class="ghost small" data-tour-do="skip">Пропустить</button></div>
    <h3 id="tour-t">${esc(title)}</h3><p id="tour-p">${esc(text)}</p>
    <div class="tour-dots" aria-hidden="true">${t.steps.map((_, i) => `<i class="${i === t.i ? 'on' : ''}"></i>`).join('')}</div>
    <div class="row">${t.i ? '<button class="btn small" type="button" data-tour-do="back">Назад</button>' : ''}<span class="sp"></span>
      <button class="btn small primary" type="button" data-tour-do="${last ? 'done' : 'next'}">${last ? 'Готово' : 'Дальше'}</button></div>`;
  const card = $('[data-tour-card]');
  card.classList.remove('in'); void card.offsetWidth; card.classList.add('in');
  const r = el.getBoundingClientRect();
  if (r.top < 70 || r.bottom > innerHeight - 40) el.scrollIntoView({block: r.height > innerHeight * .6 ? 'start' : 'center', behavior: reduce ? 'auto' : 'smooth'});
  tourPlace();
  setTimeout(tourPlace, reduce ? 0 : 420);
  $('[data-tour-do="next"], [data-tour-do="done"]', card).focus({preventScroll: true});
}

// Дырка в затемнении ложится на блок, карточка встаёт под блоком, над ним или поверх, если места нет.
function tourPlace() {
  const t = GPT.tour, hole = $('[data-tour-hole]'), card = $('[data-tour-card]');
  if (!t || !t.el || !hole || !card) return;
  const r = t.el.getBoundingClientRect(), pad = 6;
  const top = Math.max(4, r.top - pad), bottom = Math.min(innerHeight - 4, r.bottom + pad);
  Object.assign(hole.style, {top: top + 'px', left: Math.max(4, r.left - pad) + 'px',
    width: Math.min(innerWidth - 8, r.width + pad * 2) + 'px', height: Math.max(0, bottom - top) + 'px'});
  const cw = Math.min(340, innerWidth - 32), ch = card.offsetHeight, gap = 14;
  let y = bottom + gap;
  if (y + ch > innerHeight - 12) y = top - gap - ch;
  if (y < 12) y = Math.max(12, innerHeight - ch - 16);
  const x = Math.min(Math.max(16, r.left), innerWidth - cw - 16);
  Object.assign(card.style, {top: y + 'px', left: x + 'px', width: cw + 'px'});
}

function tourEnd(byUser = false) {
  const t = GPT.tour;
  GPT.tour = null;
  $('[data-tour-box]')?.remove();
  removeEventListener('resize', tourPlace);
  removeEventListener('scroll', tourPlace, true);
  if (t && byUser) {
    try { localStorage.setItem(TOUR_SEEN + S.me.role + ':' + t.page, '1'); } catch (e) { /* без памяти браузера покажем ещё раз */ }
    if (t.reopen) gptToggle(true);
  }
}
addEventListener('keydown', e => { if (e.key === 'Escape' && GPT.tour) tourEnd(true); });

// Новый клиент: экскурсия начинается сама при первом входе на страницу (на кабинет со ссылкой на меню
// и на помощника). Сотрудникам только по просьбе.
function tourAuto() {
  if (S.me.role !== 'client' || S.me.test || GPT.tour || !TOUR[S.page]) return;
  let seen = true;
  try { seen = !!localStorage.getItem(TOUR_SEEN + S.me.role + ':' + S.page); } catch (e) { /* без памяти браузера не навязываем */ }
  if (seen) return;
  const page = S.page;
  setTimeout(() => { if (S.page === page && !GPT.tour && !drawerOpen()) gptTour(page === HOME.client); }, 1400);
}

// После перехода: подсказки под новую страницу, экскурсия закончилась, новому клиенту новая.
function gptPageChanged() {
  tourEnd();
  if (GPT.open && !GPT.busy) $('[data-gpt-chips]').innerHTML = gptChips().map(c => `<button type="button" class="chip-q" data-chip="${esc(c)}">${esc(c)}</button>`).join('');
  tourAuto();
}

// Первый вход: клиенту экскурсия по кабинету.
function gptFirstVisit() { tourAuto(); }
