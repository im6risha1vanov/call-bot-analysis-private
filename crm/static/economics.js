'use strict';

const ECON_PERIODS = [['7', '7 дней'], ['30', '30 дней'], ['90', '90 дней']];
const ECON_STATUS = {
  depleted: ['Пополнить', 'bad', 0],
  inactive: ['Неактивен', 'bad', 0],
  limit: ['Лимит ключа исчерпан', 'bad', 0],
  check: ['Проверить оплату', 'warn', 1],
  unavailable: ['Остаток недоступен', 'warn', 1],
  verified: ['Получены данные', '', 2],
  available: ['Есть остаток', 'good', 2],
  unlimited: ['Без лимита', 'good', 2],
  missing: ['Не подключён', '', 3],
};

function economicAmount(value, unit) {
  if (value == null) return '—';
  if (unit === 'credits' || unit === 'tokens') {
    return new Intl.NumberFormat('ru-RU').format(value) + (unit === 'credits' ? ' кредитов' : ' токенов');
  }
  return new Intl.NumberFormat('ru-RU', {style: 'currency', currency: unit, maximumFractionDigits: 2}).format(value);
}

const economicsStatus = item => ECON_STATUS[item.funding] || ECON_STATUS[item.status];

function economicsProvider(item, index) {
  const [label, tone] = economicsStatus(item);
  const metrics = item.metrics.map(row =>
    `<div class="row"><span>${esc(row.name)}</span><b>${esc(economicAmount(row.amount, row.unit))}</b></div>`).join('');
  return `<section class="card blk econ-provider${tone === 'bad' ? ' econ-priority' : ''}">
    <div class="card-h"><h2>${esc(item.name)}</h2><span class="chip ${tone}">${esc(label)}</span></div>
    ${metrics}
    ${item.expires_at ? `<p class="hint">Ключ до ${esc(fmtWhen(item.expires_at))} МСК</p>` : ''}
    ${item.at ? `<p class="hint">Проверено ${esc(fmtWhen(item.at))} МСК</p>` : ''}
    <button class="btn ghost econ-details" type="button" data-econ-provider="${index}" aria-label="Подробнее о ${esc(item.name)}">Что зависит от сервиса ${svg('chev')}</button>
  </section>`;
}

function economicsDetails(item) {
  const [label, tone] = economicsStatus(item);
  openDrawer({title: item.name, sub: `<span class="chip ${tone}">${esc(label)}</span>`, html: `
    <section class="econ-drawer-block"><h3>Где используется</h3><p>${esc(item.uses)}</p></section>
    <section class="econ-drawer-block econ-impact"><h3>Если средства закончатся</h3><p>${esc(item.impact)}</p></section>
    <section class="econ-drawer-block"><h3>Что нужно для обновления</h3>
      <p>${esc(item.setup)}</p>${item.note ? `<p class="hint">${esc(item.note)}</p>` : ''}
      ${item.source ? `<a href="${esc(item.source)}" target="_blank" rel="noopener noreferrer">Документация провайдера ↗</a>` : ''}
    </section>`});
}

VIEWS.economics = async (view, arg) => {
  const days = ECON_PERIODS.some(([value]) => value === arg) ? arg : '30';
  setHead({actions: '<button class="btn" type="button" data-econ-refresh>Обновить</button>'});
  $('[data-econ-refresh]', $('#top'))?.addEventListener('click', reload);
  const data = await loading(view, api('/api/economics' + qs({days})), 'page');
  const providers = [...data.providers].sort((a, b) => economicsStatus(a)[2] - economicsStatus(b)[2]);
  const totals = data.totals.map(row => esc(economicAmount(row.amount, row.unit))).join(' · ');
  const expenses = data.expenses.map(row => `<tr>
    <td>${esc(row.name)}</td><td class="nw">${esc(economicAmount(row.amount, row.unit))}</td><td>${esc(row.note)}</td>
  </tr>`).join('');
  view.innerHTML = `<div class="page">
    <section class="card blk">
      <div class="card-h"><h2>Дневной лимит ИИ</h2></div>
      <div class="row"><span>Учтено сегодня</span><b>${esc(economicAmount(data.today_rub, 'RUB'))}</b></div>
      <div class="row"><span>Лимит CRM</span><b>${data.ai_limit_rub == null ? 'Без ограничения' : esc(economicAmount(data.ai_limit_rub, 'RUB'))}</b></div>
      <p class="hint">Показатель включает расходы, которые участвуют в расчёте дневного лимита CRM.</p>
    </section>
    <section class="card blk">
      <div class="card-h"><h2>Расходы по данным CRM</h2></div>
      ${tabsHtml(ECON_PERIODS, days)}
      <div class="econ-total"><span>Итого за период</span><strong>${totals || '—'}</strong></div>
      <p class="hint">${esc(data.from)} — ${esc(data.to)}. Сумма включает учтённые расходы и оценки CRM. Валюты показаны отдельно.</p>
      ${expenses ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Сценарий</th><th>Сумма</th><th>Источник</th></tr></thead><tbody>${expenses}</tbody></table></div>`
        : '<div class="empty">В CRM пока нет записей о расходах.</div>'}
    </section>
    <div class="card-h"><h2>Что нужно пополнить</h2></div>
    <div class="grid2">${providers.map(economicsProvider).join('')}</div>
  </div>`;
  $$('[data-tab]', view).forEach(button => button.addEventListener('click', () => {
    if (button.dataset.tab !== days) go('economics', button.dataset.tab);
  }));
  $$('[data-econ-provider]', view).forEach(button => button.addEventListener('click', () => {
    economicsDetails(providers[Number(button.dataset.econProvider)]);
  }));
};
