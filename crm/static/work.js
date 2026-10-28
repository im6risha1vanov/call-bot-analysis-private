// Работа с номером: карточка звонка (позвонить, отметить итог, история клиента) и очередь менеджера.
// Карточка открывается и на странице «Очередь», и в выдвижной панели из любого списка.
'use strict';

const OUT_CLASS = {meeting: 'o-good', proposal: 'o-good', callback: 'o-mid', interested: 'o-mid',
  no_answer: '', not_interested: 'o-bad', bad_number: 'o-bad'};
const OUT_ORDER = ['no_answer', 'callback', 'meeting', 'proposal', 'interested', 'not_interested', 'bad_number'];
const OPTIONAL_DATE = new Set(['proposal', 'interested']);

// Чей это клиент для отметки итога. Руководитель отмечает за менеджера, у которого номер.
function outcomeExt(ext) {
  if (S.me.role !== 'director') return undefined;
  if (ext) return ext;
  if (S.me.team.some(p => p.ext === S.me.ext)) return S.me.ext;
  return S.f.ext || undefined;
}

function itemChip(item) {
  if (item.kind === 'task') return `<span class="chip ${item.late ? 'late' : item.due_date === isoToday() ? 'today' : ''}">${esc(item.label)} · ${esc(dueText(item))}${item.late ? ' · просрочен' : ''}</span>`;
  if (item.kind === 'lead') return `<span class="chip ${item.status === 'no_answer' ? 'warn' : 'acc'}">${item.status === 'no_answer' ? `Не дозвонились · попытка ${item.attempts + 1}` : 'Новый номер'}</span>`;
  return '';
}
const SOURCE_NAME = {crm: 'отметка в CRM', review: 'из разговора', bot: 'из разговора'};

// item: {kind, id, number, title, note, label, ...}; onDone(итог) вызывается после сохранения.
// opts: {ext — чей клиент (для руководителя), extra — кнопки в шапке, drawer — карточка в выдвижной панели}.
function workCard(el, item, onDone, opts = {}) {
  const canDial = S.me.can_dial;
  const inDrawer = !!opts.drawer;
  const ro = S.me.readonly;
  const src = item.source ? `<span class="chip">${esc(SOURCE_NAME[item.source] || item.source)}</span>` : '';
  el.innerHTML = `<div class="${inDrawer ? '' : 'card blk '}work" data-css="--i:1">
    ${itemChip(item) || src || opts.extra ? `<div class="who">${itemChip(item)}${src}<span class="sp"></span>${opts.extra || ''}</div>` : ''}
    ${inDrawer ? '' : `<div class="phone"><a href="tel:+${esc(item.number)}">${esc(phone(item.number))}</a></div>
    ${item.title ? `<div class="title">${esc(item.title)}</div>` : ''}`}
    ${item.note ? `<div class="note">${esc(item.note)}</div>` : ''}
    <div class="actions">
      <button class="btn primary big" type="button" data-act="dial" ${canDial ? '' : 'disabled'}>${svg('phone', '')}Позвонить</button>
      <button class="btn" type="button" data-act="copy">Скопировать номер</button>
    </div>
    ${canDial ? '' : `<div class="ringing">${ro ? 'Тестовый вход: звонок и итог работают во входе самого менеджера.' : 'Звонить можно только со своего добавочного в Mango.'}</div>`}
    <div class="ringing hidden" data-ring></div>
    <div class="outs${ro ? ' hidden' : ''}">
      <h3>Итог звонка</h3>
      <div class="outgrid">${OUT_ORDER.map(k => `<button type="button" data-out="${k}" class="${OUT_CLASS[k]}" aria-pressed="false">${esc(S.me.outcomes[k])}</button>`).join('')}</div>
      <div class="outform hidden">
        <div class="quick"></div>
        <label class="field">Дата<input type="date" data-f="date" min="${isoToday()}"></label>
        <label class="field">Время<input type="time" data-f="time" step="300"></label>
        <label class="field wide">Комментарий<textarea data-f="comment" maxlength="1000" placeholder="Что важно помнить к следующему звонку"></textarea></label>
        <div class="row wide"><button class="btn primary" type="button" data-act="save">Сохранить${inDrawer ? '' : ' и дальше'}</button><button class="btn" type="button" data-act="cancel">Отмена</button></div>
      </div>
    </div>
  </div>
  <section class="${inDrawer ? '' : 'card blk'}" data-css="--i:2">${inDrawer ? '<h3 data-css="margin-bottom:12px">История клиента</h3>' : '<div class="card-h"><h2>История клиента</h2></div>'}<div class="hist"><div class="sk line"></div><div class="sk line" data-css="width:70%"></div></div></section>`;

  let chosen = null;
  const form = $('.outform', el), ring = $('[data-ring]', el);
  const field = k => $(`[data-f="${k}"]`, el);
  $('[data-act="copy"]', el).addEventListener('click', async e => {
    try { await navigator.clipboard.writeText('+' + item.number); doneBtn(e.currentTarget, 'Скопировано'); } catch (err) { toast(phone(item.number)); }
  });
  $('[data-act="dial"]', el).addEventListener('click', async ev => {
    const b = ev.currentTarget;
    busy(b, true);
    ring.className = 'ringing'; ring.textContent = 'Отправляю команду в Mango…';
    try {
      const res = await api('/api/dial', {method: 'POST', body: {number: item.number}});
      S.dial = {id: res.dial_id, number: item.number};
      ring.textContent = 'Сейчас зазвонит ваш софтфон Mango. Снимите трубку, и Mango наберёт клиента. После разговора отметьте итог ниже.';
    } catch (e) {
      ring.className = 'ringing err'; ring.textContent = e.message;
    } finally {
      b.classList.remove('busy');
      setTimeout(() => { b.disabled = false; }, 8000);
    }
  });
  $$('[data-out]', el).forEach(btn => btn.addEventListener('click', () => {
    chosen = btn.dataset.out;
    $$('[data-out]', el).forEach(x => x.setAttribute('aria-pressed', String(x === btn)));
    const dated = S.me.needs_date.includes(chosen) || OPTIONAL_DATE.has(chosen);
    form.classList.remove('hidden');
    field('date').parentElement.classList.toggle('hidden', !dated);
    field('time').parentElement.classList.toggle('hidden', !dated);
    const quick = $('.quick', el);
    quick.innerHTML = dated ? [['Завтра', 1], ['Через 2 дня', 2], ['Через неделю', 7]].map(([t, n]) =>
      `<button type="button" data-days="${n}">${t}</button>`).join('') : '';
    $$('[data-days]', quick).forEach(q => q.addEventListener('click', () => { field('date').value = addDays(isoToday(), +q.dataset.days); }));
    if (!dated && chosen === 'no_answer') saveOutcome();
    else field('comment').focus();
  }));
  $('[data-act="cancel"]', el).addEventListener('click', () => {
    chosen = null; form.classList.add('hidden');
    $$('[data-out]', el).forEach(x => x.setAttribute('aria-pressed', 'false'));
  });
  $('[data-act="save"]', el).addEventListener('click', saveOutcome);

  async function saveOutcome() {
    if (!chosen || S.busy) return;
    const dueDate = field('date').value || null;
    if (S.me.needs_date.includes(chosen) && !dueDate) { toast('Выберите дату', true); return; }
    S.busy = true;
    busy($('[data-act="save"]', el), true);
    try {
      await api('/api/outcome', {method: 'POST', body: {
        number: item.number, outcome: chosen, comment: field('comment').value,
        due_date: dueDate, due_time: field('time').value || null, ext: outcomeExt(opts.ext),
        dial_id: S.dial && S.dial.number === item.number ? S.dial.id : null}});
      S.dial = null;
      toast(chosen === 'no_answer' ? 'Не дозвонились: номер вернётся в очередь через 2 часа' : 'Итог сохранён');
      onDone && onDone(chosen);
    } catch (e) {
      toast(e.message, true);
      busy($('[data-act="save"]', el), false);
    } finally {
      S.busy = false;
    }
  }
  loadHistory(item.number, $('.hist', el), opts.ext);
}

// Карточка номера в выдвижной панели: из перезвонов, журнала звонков, разбора.
function workDrawer(item, {ext = null, onDone = null} = {}) {
  const body = openDrawer({title: phone(item.number), sub: esc(item.title || item.manager || '')});
  workCard(body, item, k => { closeDrawer(); onDone && onDone(k); }, {ext, drawer: true});
}

// Запись звонка из Mango: кнопка превращается в плеер, файл отдаёт наш сервер.
const listenBtn = c => c.review ? `<button class="btn small" type="button" data-review="${c.id}">Разбор${c.rec ? ' и запись' : ''}</button>`
  : c.rec ? `<button class="btn small" type="button" data-rec="${c.id}">▶ Запись</button>` : '';
document.addEventListener('click', e => {
  const b = e.target.closest('button[data-rec]');
  if (!b) return;
  e.stopPropagation();
  const a = document.createElement('audio');
  a.controls = true; a.preload = 'auto'; a.className = 'rec';
  a.src = '/api/recording/' + encodeURIComponent(b.dataset.rec);
  a.addEventListener('click', ev => ev.stopPropagation());
  a.addEventListener('error', async () => {
    let text = 'Запись не загрузилась.';
    try { const r = await fetch(a.src, {credentials: 'same-origin'}); text = (await r.json()).error || text; } catch (err) { /* остаётся общий текст */ }
    const m = document.createElement('div'); m.className = 'hint'; m.textContent = text; a.replaceWith(m);
  });
  b.replaceWith(a);
  a.play().catch(() => { /* браузер мог запретить автозапуск: плеер остаётся с кнопкой */ });
}, true);

async function loadHistory(number, box, ext) {
  let card;
  try { card = await api('/api/card' + qs({number, ext: S.me.role === 'director' ? ext : ''})); } catch (e) { box.innerHTML = `<div class="hint">${esc(e.message)}</div>`; return; }
  const events = [];
  for (const c of card.calls) {
    const rv = c.review;
    const cls = !c.seconds ? 'miss' : rv?.success ? 'ok' : rv?.chance === 'hot' ? 'hot' : '';
    const head = `${when(c.at)} · ${c.incoming ? 'входящий' : 'исходящий'} · ${c.seconds ? dur(c.seconds) : 'не дозвонились'} · ${esc(c.who)}`;
    const text = rv ? (rv.manager_text || rv.rop_line || '') : '';
    events.push({at: c.at || '', html: `<div class="h ${cls}"><div class="hd">${head}</div>${text ? `<div class="bd">${esc(text)}</div>` : ''}${listenBtn(c)}</div>`});
  }
  for (const o of card.outcomes) {
    const due = o.due_date ? ` · на ${ddmm(o.due_date)}${o.due_time ? ' ' + o.due_time : ''}` : '';
    events.push({at: o.at || '', html: `<div class="h mark"><div class="hd">${when(o.at)} · отметка в CRM · ${esc(o.who || '')}</div><div class="bd"><b>${esc(o.label)}</b>${esc(due)}${o.comment ? '\n' + esc(o.comment) : ''}</div></div>`});
  }
  events.sort((a, b) => b.at.localeCompare(a.at));
  const tasks = card.tasks.map(t => `<div class="h hot"><div class="hd">Открытый ${esc(t.label.toLowerCase())} · ${esc(dueText(t))} · ${esc(t.who || '')}</div>${t.note ? `<div class="bd">«${esc(t.note)}»</div>` : ''}</div>`).join('');
  box.innerHTML = tasks + (events.map(e => e.html).join('') || (tasks ? '' : '<div class="hint">С этим номером ещё не говорили.</div>'));
}

// В окне подробного разбора — кнопка «Позвонить»: открывает карточку номера.
document.addEventListener('crm-review', e => {
  const {box, review: r, close} = e.detail;
  if (!S.me || S.me.role === 'client' || !r.number) return;
  const acts = document.createElement('div');
  acts.className = 'rvm-acts';
  acts.innerHTML = `<button class="btn small primary" type="button">${svg('phone', '')}${S.me.can_dial ? 'Позвонить' : 'Карточка клиента'}</button>`;
  const head = $('.rvm-head', box);
  head.insertBefore(acts, $('.rvm-audio', head));
  $('button', acts).addEventListener('click', () => {
    close();
    workDrawer({kind: 'call', number: r.number, title: r.manager || ''}, {ext: r.ext});
  });
});

// ---------- очередь менеджера ----------
VIEWS.queue = async (view, arg) => {
  const director = S.me.role === 'director';
  const ext = director ? (arg || S.f.ext || S.me.team[0]?.ext) : null;
  const person = director ? S.me.team.find(p => p.ext === ext) : null;
  setHead({title: director ? `Очередь · ${person?.name || 'доб. ' + ext}` : 'Очередь', back: director ? () => go('staff', ext) : null});
  const q = await loading(view, api('/api/queue' + qs({ext})), 'cards');
  S.today = q.today || S.today;
  if (!director) setCount('queue', q.items.length);
  if (!q.items.length) {
    view.innerHTML = `<div class="page"><div class="card empty blk"><p><b>Очередь пуста.</b></p><p>${q.later ? `Ещё ${plainItems(q.later, 'номер', 'номера', 'номеров')} вернутся позже: перезвоны на другие дни и недозвоны через 2 часа.` : 'Добавьте номера в разделе «Источники», и они появятся здесь.'}</p>
      ${director ? '' : '<button class="btn primary" type="button" data-go="sources" data-arg="base">Добавить номера</button>'}</div></div>`;
    return;
  }
  let pos = Math.max(0, q.items.findIndex(i => i.number === S.qnum));
  const tasksN = q.items.filter(i => i.kind === 'task').length;
  view.innerHTML = `<div class="page">
    <div class="qline blk"><span>В очереди <b>${fmt(q.items.length)}</b>${tasksN ? `, из них <b>${fmt(tasksN)}</b> ${plural(tasksN, 'перезвон', 'перезвона', 'перезвонов')}` : ''}</span>
      ${q.later ? `<span class="hint">ещё ${fmt(q.later)} вернутся позже ${help('Перезвоны на другие дни и номера, которые не взяли трубку: они возвращаются через 2 часа, до 3 попыток.')}</span>` : ''}</div>
    <div class="grid2"><div class="stack" data-work></div>
      <div class="card blk" data-css="--i:3"><div class="card-h"><h2>Дальше по очереди</h2><span class="hint">${fmt(q.items.length)}</span></div><div class="list" data-next></div></div></div></div>`;
  const show = () => {
    const item = q.items[pos];
    S.qnum = item.number; S.dial = null;
    workCard($('[data-work]', view), item, () => {
      q.items.splice(pos, 1);
      S.qnum = q.items[pos % Math.max(1, q.items.length)]?.number || null;
      reload();
    }, {ext, extra: q.items.length > 1 ? '<button class="btn small ghost" type="button" data-skip>Пропустить ›</button>' : ''});
    $('[data-skip]', view)?.addEventListener('click', () => { pos = (pos + 1) % q.items.length; show(); });
    const n = Math.min(q.items.length, 12); // ближайшие номера, начиная с текущего; остальные числом
    $('[data-next]', view).innerHTML = Array.from({length: n}, (_, k) => (pos + k) % q.items.length).map(i => listItem(q.items[i], i, i === pos)).join('')
      + (q.items.length > n ? `<p class="hint more-l">и ещё ${fmt(q.items.length - n)}</p>` : '');
  };
  $('[data-next]', view).addEventListener('click', e => {
    const b = e.target.closest('.item'); if (!b) return;
    pos = +b.dataset.i; show();
    if (innerWidth <= 900) scrollTo({top: 0, behavior: reduce ? 'auto' : 'smooth'});
  });
  show();
};

function listItem(it, i, current) {
  const right = it.kind === 'task'
    ? `<span class="chip ${it.late ? 'late' : 'today'}">${esc(dueText(it))}</span>`
    : `<span class="chip ${it.status === 'no_answer' ? 'warn' : ''}">${it.status === 'no_answer' ? 'повтор ' + (it.attempts + 1) : 'новый'}</span>`;
  return `<button class="item" type="button" data-i="${i}" aria-current="${current}"><div class="main-l">
    <div class="t num">${esc(phone(it.number))}</div><div class="s">${esc(it.title || it.note || (it.kind === 'task' ? it.label : it.source) || '')}</div></div>
    <div class="r">${right}</div></button>`;
}
