// AveronGPT: помощник внизу меню (на телефоне круглая кнопка над нижней панелью). Окно чата справа,
// не закрывает страницу. С вопросом уходит, где человек сейчас и текст с экрана, чтобы «а это что?»
// понималось без уточнений. Кнопки разделов в ответе ведут через обычный data-go. Новому клиенту окно
// открывается само и предлагает тур по кабинету с подсветкой мест на странице.
'use strict';

const GPT = {open: false, items: null, busy: false, tour: null};
const GPT_ORB = '<span class="gpt-orb" aria-hidden="true"></span>';
const GPT_SEEN = 'crm-gpt-seen';

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
  if (!on) { gptTourEnd(); return; }
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
    ${S.me.role === 'client' && S.page === 'cab' ? '<p><button class="gpt-go" type="button" data-tour>Показать кабинет по шагам</button></p>' : ''}</div>`;
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

// ---------- тур по кабинету клиента ----------
// Шаги подсвечивают место на странице; текст шага пишется в окне помощника. Без модели: быстро и бесплатно.
const GPT_TOUR = {
  cab: [
    ['.kpis', 'Сверху четыре главные цифры: сколько номеров вы передали, по скольким уже звонили, сколько взяли трубку и сколько клиентов заинтересовались.'],
    ['.split', 'Итог по номерам: полоса показывает, чем закончились звонки по всей базе. Под ней подписи с числом и долей каждого итога.'],
    ['.grid2', 'Заинтересованные и перезвоны: телефоны, дата звонка и комментарий оператора. Длинный список раскрывается кнопкой «Показать ещё».'],
    ['[data-chart]', 'Звонки по дням: первичные и повторные звонки. Наведите или нажмите на столбик, чтобы увидеть дозвон и заинтересованных за день.'],
    ['details.more', 'В «Подробнее» расчёт по вашему тарифу с итогом к оплате и цифры по каждой загруженной базе.'],
  ],
};

function gptTour() {
  const steps = (GPT_TOUR[S.page] || []).filter(([sel]) => $(sel, $('#view')));
  if (!steps.length) { gptAsk('Покажи, что где на этой странице'); return; }
  GPT.tour = {steps, i: 0};
  gptTourStep();
}

function gptTourStep() {
  const t = GPT.tour;
  $$('.gpt-spot').forEach(el => el.classList.remove('gpt-spot'));
  if (!t) return;
  const [sel, text] = t.steps[t.i];
  const el = $(sel, $('#view'));
  el.classList.add('gpt-spot');
  $('#gpt').classList.add('touring');
  el.scrollIntoView({block: 'center', behavior: reduce ? 'auto' : 'smooth'});
  const last = t.i === t.steps.length - 1;
  $('.gpt-tour')?.remove();
  $('[data-gpt-feed]').insertAdjacentHTML('beforeend', `<div class="gpt-m ai gpt-tour"><span class="hint">Шаг ${t.i + 1} из ${t.steps.length}</span><p>${esc(text)}</p>
    <div class="row">${t.i ? '<button class="btn small ghost" type="button" data-step="-1">Назад</button>' : ''}
    <button class="btn small primary" type="button" data-step="${last ? 'end' : '1'}">${last ? 'Готово' : 'Дальше'}</button></div></div>`);
  const feed = $('[data-gpt-feed]');
  feed.scrollTop = feed.scrollHeight;
  $$('.gpt-tour [data-step]').forEach(b => b.addEventListener('click', () => {
    if (b.dataset.step === 'end') { gptTourEnd(); $('.gpt-tour')?.remove(); return; }
    t.i += +b.dataset.step; gptTourStep();
  }));
}
function gptTourEnd() { GPT.tour = null; $$('.gpt-spot').forEach(el => el.classList.remove('gpt-spot')); $('#gpt')?.classList.remove('touring'); }

// После перехода: подсказки под новую страницу, тур закончился.
function gptPageChanged() {
  gptTourEnd(); $('.gpt-tour')?.remove();
  if (GPT.open && !GPT.busy) $('[data-gpt-chips]').innerHTML = gptChips().map(c => `<button type="button" class="chip-q" data-chip="${esc(c)}">${esc(c)}</button>`).join('');
}

// Первый вход клиента: помощник открывается сам один раз.
function gptFirstVisit() {
  if (S.me.role !== 'client' || S.me.test) return;
  let seen = false;
  try { seen = !!localStorage.getItem(GPT_SEEN); localStorage.setItem(GPT_SEEN, '1'); } catch (e) { seen = true; }
  if (!seen) setTimeout(() => gptToggle(true), 1200);
}
