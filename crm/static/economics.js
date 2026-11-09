'use strict';

const ECON_PERIODS = [['7', '7 дней'], ['30', '30 дней'], ['90', '90 дней']];
const ECON_STATE = {verified: 'Получено из API', unavailable: 'Данные недоступны', missing: 'Ключ не настроен'};

function economicAmount(value, unit) {
  if (value == null) return '—';
  if (unit === 'credits') return new Intl.NumberFormat('ru-RU').format(value) + ' кредитов';
  return new Intl.NumberFormat('ru-RU', {style: 'currency', currency: unit, maximumFractionDigits: 2}).format(value);
}

function economicsProvider(item) {
  const metrics = item.metrics.map(row =>
    `<div class="row"><span>${esc(row.name)}</span><b>${esc(economicAmount(row.amount, row.unit))}</b></div>`).join('');
  const info = item.source ? `<a href="${esc(item.source)}" target="_blank" rel="noopener noreferrer">Документация</a>` : '';
  return `<section class="card blk">
    <div class="card-h"><h2>${esc(item.name)}</h2><span class="chip ${item.status === 'verified' ? 'good' : ''}">${esc(ECON_STATE[item.status])}</span></div>
    ${metrics}
    <p class="hint">${item.scope === 'company' ? 'Счёт компании' : 'Общий аккаунт проекта'}${item.at ? ' · проверено ' + esc(fmtWhen(item.at)) + ' МСК' : ''}.</p>
    <p class="hint">${esc(item.note || '')} ${info}</p>
  </section>`;
}

VIEWS.economics = async (view, arg) => {
  const days = ECON_PERIODS.some(([value]) => value === arg) ? arg : '30';
  const data = await loading(view, api('/api/economics' + qs({days})), 'page');
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
      <p class="hint">${esc(data.from)} — ${esc(data.to)}. Суммы рассчитаны по учёту CRM. Валюты показаны отдельно.</p>
      ${expenses ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Сценарий</th><th>Сумма</th><th>Источник</th></tr></thead><tbody>${expenses}</tbody></table></div>`
        : '<div class="empty">В CRM пока нет записей о расходах.</div>'}
    </section>
    <div class="card-h"><h2>Балансы, лимиты и API-ключи</h2></div>
    <div class="grid2">${data.providers.map(economicsProvider).join('')}</div>
  </div>`;
  $$('[data-tab]', view).forEach(button => button.addEventListener('click', () => {
    if (button.dataset.tab !== days) go('economics', button.dataset.tab);
  }));
};
