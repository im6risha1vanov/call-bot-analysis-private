// Сделки: клиент от холодного звонка до оплаты. Полоса этапов (нажимается), список, карточка сделки
// в окне. Вкладка «Договоры»: журнал и генератор .docx (static/contracts/).
'use strict';

const DEAL_SHOW = [['active', 'В работе'], ['stalled', 'Зависшие'], ['lost', 'Отказ'], ['paid', 'Оплачено'], ['all', 'Все']];
const DEAL_PERIODS = [['30', '30 дней'], ['90', '90 дней'], ['', 'Всё время']];
const TEMP = {hot: ['горячий', 'good'], warm: ['тёплый', 'warn'], cold: ['холодный', ''], lost: ['отказ', 'bad']};
const dealName = d => d.company || phone(d.number);
S.deal = {show: 'active', days: '90', stage: 0};

function dealStatus(d) {
  if (d.status === 'paid') return `<span class="chip good">оплачено${d.amount ? ' · ' + rub(d.amount) : ''}</span>`;
  if (d.status === 'lost') return `<span class="chip bad">отказ${d.lost.auto ? '' : ' · отмечен'}</span>`;
  if (d.stalled_days != null) return `<span class="chip late">стоит ${d.stalled_days} ${plural(d.stalled_days, 'день', 'дня', 'дней')}</span>`;
  return '<span class="chip acc">в работе</span>';
}
function dealSteps(d) {
  return `<div class="deal-steps">${(S.dealStages || []).slice(1).map((st, i) => {
    const n = i + 2;
    const cls = d.stage_no > n || (d.stage_no === n && d.status === 'paid') ? 'done' : d.stage_no === n ? (d.status === 'lost' ? 'lost' : 'cur') : '';
    return `<span class="${cls}">${esc(st.name)}</span>`;
  }).join('')}</div>`;
}
const dealTabs = cur => tabsHtml([['', 'Сделки'], ['contracts', 'Договоры']], cur);
function bindDealTabs(view) {
  $$('[data-tab]', view).forEach(b => b.addEventListener('click', () => go('deals', b.dataset.tab || null)));
}

VIEWS.deals = async (view, arg) => {
  if (arg === 'contracts') return contractsView(view);
  const f = S.deal;
  const dir = S.me.role === 'director';
  const head = managers => setHead({filters: seg('show', DEAL_SHOW, f.show, 'Какие сделки') + seg('days', DEAL_PERIODS, f.days, 'Период') +
    (dir && managers?.length > 1 ? `<select class="inp" data-ext-pick aria-label="Менеджер"><option value="">Все менеджеры</option>${managers.map(m =>
      `<option value="${esc(m.ext)}"${m.ext === S.f.ext ? ' selected' : ''}>${esc(m.name)}</option>`).join('')}</select>` : '')});
  let d = null, seq = 0;
  const bind = () => {
    bindSeg($('#top'), {show: v => { f.show = v; render(); }, days: v => { f.days = v; load(); }});
    $('[data-ext-pick]', $('#top'))?.addEventListener('change', e => { S.f.ext = e.target.value; saveFilters(); load(); });
  };
  head(null); bind();

  async function load() {
    const my = ++seq;
    let data;
    try { data = await loading(view, api('/api/deals' + qs({days: f.days, person: dir ? S.f.ext : ''})), 'page'); }
    catch (e) { if (my === seq && view.isConnected) view.innerHTML = errCard(e.message); return; }
    if (my !== seq || !view.isConnected) return;
    const first = !d;
    d = data;
    S.dealStages = d.stages; S.dealReasons = d.reasons; S.canPay = d.can_pay;
    if (first) { head(d.managers); bind(); }
    render();
  }

  function render() {
    const fn = d.funnel;
    const base = Math.max(1, fn.stages[0]?.reached || 0);
    const list = d.deals.filter(x => (f.show === 'all' || (f.show === 'stalled' ? x.stalled_days != null : x.status === f.show))
      && (!f.stage || x.stage_no === f.stage));
    const picked = f.stage ? fn.stages[f.stage - 1] : null;
    let i = 0;
    view.innerHTML = `<div class="page">${dealTabs('')}
      <div class="stages blk" data-css="--i:${i++}" role="group" aria-label="Этапы сделки">${fn.stages.map((st, k) => `<button type="button" class="stage" data-stage="${k + 1}" aria-pressed="${f.stage === k + 1}"
        title="${esc(st.reasons.map(([r, n]) => `${r}: ${n}`).join('\n'))}">
        <span class="nm">${k + 1}. ${esc(st.name)}</span><b>${fmt(st.reached)}</b><span class="ls">${st.lost ? `отказ ${fmt(st.lost)}` : ''}</span>
        <i class="fill" data-css="--w:${Math.max(2, st.reached / base * 100)}%;--k:${k}"></i></button>`).join('')}</div>
      ${picked && picked.lost ? `<p class="hint blk" data-css="--i:${i++};margin:-6px 0 0">Почему отказываются на этапе «${esc(picked.name)}»: ${picked.reasons.map(([r, n]) => `${esc(r)} · ${n}`).join('; ')}</p>` : ''}
      ${d.unlinked?.length ? `<div class="alert acc blk" data-css="--i:${i++}"><span class="pdot"></span><p><b>${plainItems(d.unlinked.length, 'презентация', 'презентации', 'презентаций')} без клиента</b>: шли не через Mango, бот не знает номер.</p>
        <button class="btn small" type="button" data-unlinked>Привязать</button></div>` : ''}
      <section class="card blk" data-css="--i:${i++}"><div class="card-h"><h2>${esc(DEAL_SHOW.find(x => x[0] === f.show)[1])}${picked ? ` · этап «${esc(picked.name)}»` : ''}</h2><span class="hint">${fmt(list.length)}</span>
        ${fn.paid ? `<span class="sp"></span><span class="hint">оплачено ${fmt(fn.paid)}${fn.paid_sum ? ' на ' + rub(fn.paid_sum) : ''}</span>` : ''}</div>
      ${list.length ? `<div class="tbl-wrap"><table class="tbl anim"><thead><tr><th>Клиент</th>${dir ? '<th class="hide-m">Менеджер</th>' : ''}<th>Этап</th><th class="hide-m">Последнее</th><th class="hide-m">Что дальше</th></tr></thead><tbody>
        ${list.slice(0, 300).map((x, r) => `<tr class="click" tabindex="0" data-deal="${esc(x.phone)}" data-css="--r:${Math.min(r, 30)}"><td><div class="t">${esc(dealName(x))}</div>${x.company ? `<div class="s num">${esc(phone(x.number))}</div>` : ''}</td>
          ${dir ? `<td class="hide-m nw">${esc(x.manager)}</td>` : ''}<td>${esc(x.stage_name)}<div data-css="margin-top:3px">${dealStatus(x)}</div></td><td class="hide-m nw">${esc(when(x.last_at))}</td>
          <td class="hide-m wrap">${x.status === 'lost' ? `<span class="s">${esc(x.lost.reason)}</span>` : esc(x.advice || '')}</td></tr>`).join('')}</tbody></table></div>`
        : '<div class="empty">Таких сделок нет.</div>'}</section>
      ${moreBlock('Как считаются сделки', '', `<p class="lead">Клиент — это номер телефона. Звонки и презентации CRM разбирает сама; Telegram или почту, договор и отказ отмечает менеджер в карточке сделки, оплату — директор. Отказ на холодном звонке CRM ставит сама, если последний разговор без результата и перезвона нет; причина берётся из разбора.</p>`, i++)}</div>`;
    bindDealTabs(view);
    $$('[data-stage]', view).forEach(b => b.addEventListener('click', () => { f.stage = f.stage === +b.dataset.stage ? 0 : +b.dataset.stage; render(); }));
    $('[data-unlinked]', view)?.addEventListener('click', () => unlinkedList(d.unlinked, dir, load));
    const tbody = $('tbody', view);
    tbody?.addEventListener('click', e => { const tr = e.target.closest('[data-deal]'); if (tr) dealCard(tr.dataset.deal, load); });
    tbody?.addEventListener('keydown', e => { const tr = e.target.closest('[data-deal]'); if (tr && e.key === 'Enter') dealCard(tr.dataset.deal, load); });
  }
  await load();
};

function unlinkedList(items, dir, onChange) {
  const box = modal(`${modalHead('Презентации без клиента', 'Эти презентации шли не через Mango (мессенджер, Zoom). Укажите номер клиента, и презентация попадёт в его сделку.')}
    <div class="rvm-body"><div class="list">${items.map(u => `<div class="item" data-u="${u.id}"><div class="main-l">
      <div class="t" data-css="white-space:normal">${u.company ? esc(u.company) + ' · ' : ''}<span class="hint">${esc(when(u.at))}${dir && u.manager ? ' · ' + esc(u.manager) : ''}</span></div>
      <div class="s" data-css="white-space:normal">${esc(u.summary || '')}${u.contact ? ` · в реестре: ${esc(u.contact)}` : ''}</div>
      <div class="row" data-css="margin-top:6px"><a class="btn small" href="https://drive.google.com/file/d/${encodeURIComponent(u.file_id)}/view" target="_blank" rel="noopener">Запись</a>
        ${S.me.readonly ? '' : '<button class="btn small primary" type="button" data-plink>Привязать к клиенту</button>'}</div></div></div>`).join('')}</div></div>`, true);
  box.addEventListener('click', e => {
    const b = e.target.closest('[data-plink]'); if (!b) return;
    const row = b.closest('[data-u]');
    if ($('.move', row)) return;
    b.insertAdjacentHTML('afterend', `<span class="move row"><input class="inp" inputmode="tel" placeholder="+7 999 123-45-67" aria-label="Номер клиента"><button class="btn small primary" type="button" data-pgo>Сохранить</button></span>`);
    b.remove();
    $('.move input', row).focus();
    $('[data-pgo]', row).addEventListener('click', async ev => {
      const btn = ev.currentTarget; // после await e.currentTarget уже null
      busy(btn, true);
      try {
        const r = await api(`/api/presentations/${row.dataset.u}/link`, {method: 'POST', body: {phone: $('.move input', row).value}});
        doneBtn(btn, r.call_found ? 'Привязано к сделке' : 'Привязано, сделка создана');
        row.style.opacity = .55;
        onChange && onChange();
      } catch (err) { busy(btn, false); toast(err.message, true); }
    });
  });
}

function dealTimeline(items) {
  return items.map(t => {
    const head = `<div class="rvm-ih"><b>${esc(t.title)}</b><span class="rvm-mut">${esc(when(t.at))}${t.by ? ' · ' + esc(t.by) : ''}</span>
      ${t.type === 'presentation' && TEMP[t.temperature] ? `<span class="rvm-chip ${TEMP[t.temperature][1]}">клиент ${TEMP[t.temperature][0]}</span>` : ''}
      ${t.amount ? `<span class="rvm-chip good">${rub(t.amount)}</span>` : ''}</div>`;
    const cls = t.type === 'event' ? '' : t.ok || (t.type === 'presentation' && !t.lost) ? 'good' : t.lost ? 'miss' : '';
    return `<div class="rvm-item ${cls}">${head}
      ${t.text ? `<div>${esc(t.text)}</div>` : ''}
      ${t.lost ? `<div class="rvm-q">Где остыл: ${esc(t.lost)}</div>` : ''}
      ${t.say ? `<div class="rvm-say">Как можно было: ${esc(t.say)}</div>` : ''}
      ${t.type === 'call' ? `<div><button class="btn small" type="button" data-review="${t.id}">Разбор и запись</button></div>` : ''}
      ${t.file_id ? `<div><a class="btn small" href="https://drive.google.com/file/d/${encodeURIComponent(t.file_id)}/view" target="_blank" rel="noopener">Запись презентации</a></div>` : ''}</div>`;
  }).join('');
}

async function dealCard(ph, onChange) {
  const box = modal(`${modalHead('Загружаю…')}<div class="rvm-body"><div class="sk line"></div><div class="sk line" data-css="width:60%"></div></div>`);
  let d;
  try { d = await api('/api/deals/' + encodeURIComponent(ph)); } catch (e) { $('h2', box).textContent = e.message; $('.rvm-body', box).remove(); return; }
  if (!box.isConnected) return;
  const ro = S.me.readonly;
  const acts = d.status === 'active' ? [
    ['presentation', 'Презентация проведена'], ['materials', 'Отправил материалы'], ['contract_sent', 'Договор отправлен'],
    ['contract_edits', 'Правки по договору'], ['contract_signed', 'Договор подписан'],
    ...(d.can_pay ? [['paid', 'Оплата получена']] : []), ['lost', 'Отказ']] : [['reopen', 'Снова в работе']];
  box.innerHTML = `${modalHead(dealName(d), `${d.company ? `<span class="num">${esc(phone(d.number))}</span> · ` : ''}${esc(d.manager)} · этап: ${esc(d.stage_name)}`, dealStatus(d))}
    <div class="rvm-body">
      ${dealSteps(d)}
      <div class="row">${S.me.role !== 'client' ? `<button class="btn small primary" type="button" data-dcall>${svg('phone', '')}${S.me.can_dial ? 'Позвонить' : 'Карточка клиента'}</button>` : ''}
        ${ro ? '' : '<button class="btn small" type="button" data-contract>Составить договор</button>'}</div>
      ${d.status === 'lost' ? `<div class="rvm-item miss"><b>Отказ: ${esc(d.lost.reason)}</b>${d.lost.note ? `<div>${esc(d.lost.note)}</div>` : ''}
        <div class="rvm-q">${d.lost.auto ? 'CRM поставила сама по последнему разговору. Если это не так, нажмите «Снова в работе».' : 'Отмечено ' + esc(when(d.lost.at))}</div></div>` : ''}
      ${d.advice ? `<section><h3>Что дальше</h3><div class="rvm-say">${esc(d.advice)}</div></section>` : ''}
      ${d.missing.length && d.status === 'active' ? `<section><h3>Для договора не хватает</h3><div class="chips">${d.missing.map(m => `<span class="chip warn">${esc(m)}</span>`).join('')}</div></section>` : ''}
      ${d.contracts.length ? `<section><h3>Договоры</h3>${d.contracts.map(c => `<div>${esc(contractTitle(c))}${c.by ? ` <span class="rvm-mut">· ${esc(c.by)}</span>` : ''}</div>`).join('')}</section>` : ''}
      ${ro ? '' : `<section><h3>Отметить</h3><div class="chips">${acts.map(([k, t]) => `<button class="btn small${k === 'lost' ? ' danger' : ''}" type="button" data-ev="${k}">${t}</button>`).join('')}
        <button class="btn small" type="button" data-ev="note">Заметка</button></div><div data-form></div></section>`}
      <section><h3>История</h3>${dealTimeline([...d.timeline].reverse()) || '<div class="rvm-mut">Пусто.</div>'}</section>
    </div>`;
  $$('[data-ev]', box).forEach(b => b.addEventListener('click', () => {
    $$('[data-ev]', box).forEach(x => x.setAttribute('aria-pressed', String(x === b)));
    dealForm(box, d, b.dataset.ev, onChange);
  }));
  $('[data-dcall]', box)?.addEventListener('click', () => { box.close(); workDrawer({kind: 'call', number: d.number, title: d.company || ''}, {ext: d.ext}); });
  $('[data-contract]', box)?.addEventListener('click', () => {
    S.contractDeal = {phone: d.phone, number: d.number, title: dealName(d) + (d.company ? ' · ' + phone(d.number) : ''), missing: d.missing};
    box.close();
    go('deals', 'contracts');
  });
}

function dealForm(box, d, kind, onChange) {
  const form = $('[data-form]', box);
  const label = {contract_edits: 'Какие правки попросил клиент', note: 'Заметка', lost: 'Что сказал клиент'}[kind];
  form.innerHTML = `<div class="outform">
    ${kind === 'lost' ? `<label class="field">Причина<select data-v="reason"><option value="">Выберите</option>${Object.entries(d.reasons).map(([k, t]) => `<option value="${k}">${esc(t)}</option>`).join('')}</select></label>` : ''}
    ${kind === 'paid' ? '<label class="field">Сумма, ₽<input data-v="amount" inputmode="decimal" placeholder="165 000"></label>' : ''}
    <label class="field wide">${esc(label || 'Комментарий, если нужно')}<textarea data-v="note" rows="2" maxlength="2000"></textarea></label>
    <div class="row wide"><button class="btn primary" type="button" data-save>Сохранить</button></div></div>`;
  $('[data-v="note"]', form).focus();
  $('[data-save]', form).addEventListener('click', async e => {
    const btn = e.currentTarget; // после await e.currentTarget уже null
    const v = k => $(`[data-v="${k}"]`, form)?.value;
    busy(btn, true);
    try {
      await api(`/api/deals/${encodeURIComponent(d.phone)}/event`, {method: 'POST', body: {kind, note: v('note'), reason: v('reason'), amount: v('amount')}});
      toast('Сохранено');
      box.close();
      onChange && onChange();
      dealCard(d.phone, onChange);
    } catch (err) { busy(btn, false); toast(err.message, true); }
  });
}

// ---------- договоры: генератор .docx и журнал ----------
const CONTRACT_JS = ['pizzip.min.js', 'docxtemplater.min.js', 'logic.js', 'generator.js'];
let contractJs = null;
function contractScripts() {
  // Библиотеки весят ~200 КБ, поэтому грузим их только при открытии генератора.
  contractJs = contractJs || CONTRACT_JS.reduce((p, name) => p.then(() => new Promise((ok, fail) => {
    const el = document.createElement('script');
    el.src = '/static/contracts/' + name;
    el.onload = ok;
    el.onerror = () => { contractJs = null; fail(new Error('Генератор договоров не загрузился. Обновите страницу.')); };
    document.head.appendChild(el);
  })), Promise.resolve());
  return contractJs;
}
const contractTitle = c => `${c.type_title}${c.num ? ' № ' + c.num : c.base_num ? ' к № ' + c.base_num : ''}${c.date ? ' от ' + ddmm(c.date) + '.' + c.date.slice(0, 4) : ''}${c.amount ? ' · ' + rub(c.amount) : ''}`;

async function contractsView(view) {
  const deal = S.contractDeal || null;
  S.contractDeal = null;
  setHead({title: 'Сделки'});
  const first = await loading(view, api('/api/contracts'), 'table');
  view.innerHTML = `<div class="page">${dealTabs('contracts')}<div data-gen></div><section class="card blk" data-css="--i:1" data-log></section></div>`;
  bindDealTabs(view);
  const journal = d => {
    const box = $('[data-log]', view);
    if (!box) return;
    const opened = !!$('[data-gen].card', view);
    box.innerHTML = `<div class="card-h"><h2>Журнал договоров</h2><span class="hint">${fmt(d.items.length)}</span><span class="sp"></span>
      ${S.me.readonly ? '' : `<button class="btn primary" type="button" data-new${opened ? ' hidden' : ''}>Новый договор</button>`}</div>
    ${d.items.length ? `<div class="tbl-wrap"><table class="tbl anim"><thead><tr><th class="n">№</th><th>Договор</th><th>Клиент</th><th class="hide-m">ИНН</th><th class="n">Сумма</th><th class="hide-m">Оформил</th></tr></thead><tbody>
      ${d.items.map((c, r) => `<tr${c.phone ? ` class="click" data-deal="${esc(c.phone)}"` : ''} data-css="--r:${Math.min(r, 30)}"><td class="n">${c.num ?? '—'}</td>
        <td>${esc(c.type_title)}${c.base_num ? ` к № ${esc(c.base_num)}` : ''}<div class="s">${c.date ? esc(ddmm(c.date) + '.' + c.date.slice(0, 4)) : ''}${c.tariff ? ' · ' + esc(c.tariff) : ''}</div></td>
        <td>${esc(c.company || '')}${c.phone ? '<div class="s">сделка →</div>' : ''}</td><td class="hide-m num">${esc(c.inn || '')}</td>
        <td class="n">${c.amount ? rub(c.amount) : ''}</td><td class="hide-m">${esc(c.by || '')}<div class="s">${esc(when(c.created_at))}</div></td></tr>`).join('')}
    </tbody></table></div>` : '<div class="empty">Договоров пока нет. Оформленный здесь договор попадёт в журнал с номером.</div>'}`;
    $$('[data-deal]', box).forEach(tr => tr.addEventListener('click', () => dealCard(tr.dataset.deal)));
    $('[data-new]', box)?.addEventListener('click', () => open(null));
  };
  const open = async withDeal => {
    const box = $('[data-gen]', view), btn = $('[data-new]', view);
    busy(btn, true);
    try { await contractScripts(); } catch (e) { busy(btn, false); toast(e.message, true); return; }
    if (btn) btn.hidden = true;
    box.className = 'card blk';
    await window.ContractGenerator.mount(box, {api, deal: withDeal, readonly: S.me.readonly,
      onDone: () => api('/api/contracts').then(journal).catch(e => toast(e.message, true))});
    box.scrollIntoView({behavior: reduce ? 'auto' : 'smooth', block: 'start'});
  };
  journal(first);
  if (deal) await open(deal);
}
