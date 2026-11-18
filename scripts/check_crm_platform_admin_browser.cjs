'use strict';

const assert = require('node:assert/strict');
const {mkdir} = require('node:fs/promises');
const path = require('node:path');

const {chromium} = require(process.env.CRM_ADMIN_PLAYWRIGHT || 'playwright');
const origin = process.env.CRM_ADMIN_TEST_URL;
if (!origin || new URL(origin).hostname !== '127.0.0.1') {
  throw new Error('The browser test requires a disposable local CRM server.');
}
const out = process.env.CRM_ADMIN_SCREENSHOTS;
const route = '/api/platform/admin/companies/1';
const visit = (page, hash) => page.goto(`${origin}#${hash}`);
const dialog = page => page.getByRole('dialog').last();
const tab = (page, title) => page.getByRole('navigation', {name: 'Управление компанией'}).getByRole('button', {name: title, exact: true});
const submit = (node, title) => node.getByRole('button', {name: title, exact: true}).click();
const close = page => dialog(page).getByRole('button', {name: 'Закрыть', exact: true}).click();

async function login(page, login, password, hash) {
  await visit(page, hash);
  await page.getByLabel('Логин', {exact: true}).fill(login);
  await page.getByLabel('Пароль', {exact: true}).fill(password);
  await submit(page, 'Войти');
  await page.waitForURL(url => url.hash === `#${hash}`);
  await page.locator('#view').waitFor();
}

async function main() {
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CRM_ADMIN_CHROME,
  });
  try {
    const admin = await browser.newContext({viewport: {width: 1440, height: 940}});
    const page = await admin.newPage();
    const errors = [];
    const observed = [];
    page.on('pageerror', error => { errors.push(error.message); console.error(error); });
    page.on('request', req => {
      if (req.url().includes('/api/platform/admin/') && req.method() !== 'GET') {
        observed.push({method: req.method(), path: new URL(req.url()).pathname, headers: req.headers()});
      }
    });
    await login(page, 'grigory-test', 'test-grigory-1', 'platform-admin');
    await page.getByRole('heading', {name: 'Профиль компании'}).waitFor();
    assert.equal(await page.getByRole('heading', {name: 'Админка платформы', exact: true}).count(), 1);
    if (out) {
      await mkdir(out, {recursive: true});
      await page.screenshot({path: path.join(out, 'desktop.png')});
    }

    await page.locator('[data-company-settings] input[name=name]').fill('Averon QA');
    await page.locator('[name=ai_day_limit_rub]').fill('1250');
    await submit(page, 'Сохранить настройки');
    await page.getByRole('heading', {name: 'Averon QA', exact: true}).waitFor();
    await page.getByText('Лимит 1 250 ₽', {exact: true}).waitFor();

    await tab(page, 'Права').click();
    await page.locator('[data-role]').selectOption('manager');
    await page.getByRole('checkbox', {name: 'Запретить: Отчёты · Экспорт'}).check();
    await submit(page, 'Сохранить права');
    await page.waitForResponse(r => r.url().endsWith('/permissions/manager') && r.request().method() === 'GET');
    assert(await page.getByRole('checkbox', {name: 'Запретить: Отчёты · Экспорт'}).isChecked());

    await tab(page, 'Учётные записи').click();
    await submit(page, 'Добавить учётную запись');
    await dialog(page).getByLabel('Добавочный номер менеджера').fill('101');
    await dialog(page).getByLabel('Имя', {exact: true}).fill('Тестовый менеджер');
    await dialog(page).getByLabel('Логин', {exact: true}).fill('browser-manager-test');
    await dialog(page).getByLabel('Пароль', {exact: true}).fill('browser-password-25');
    await submit(dialog(page), 'Сохранить');
    await page.getByText('Тестовый менеджер', {exact: true}).waitFor();
    await submit(page.locator('.pa-row').filter({hasText: 'Тестовый менеджер'}), 'Права');
    await dialog(page).getByText('Отчёты · Экспорт').waitFor();
    await close(page);

    await tab(page, 'Воронки').click();
    await submit(page.locator('.pa-row').filter({hasText: 'Продажи'}), 'Открыть');
    await submit(dialog(page), 'Редактировать');
    await submit(dialog(page), 'Добавить этап');
    const stage = dialog(page).locator('[data-stage-row]').filter({has: page.locator('input[data-code]:not([readonly])')});
    await stage.getByLabel('Код', {exact: true}).fill('consultation');
    await stage.getByLabel('Этап', {exact: true}).fill('Повторная консультация');
    await stage.getByLabel('Срок, дней', {exact: true}).fill('2');
    await submit(dialog(page), 'Добавить переход');
    await dialog(page).locator('[data-from]').selectOption('presentation');
    await dialog(page).locator('[data-to]').selectOption('consultation');
    await submit(dialog(page), 'Сохранить черновик');
    await submit(dialog(page), 'Проверить изменения');
    await submit(dialog(page), 'Опубликовать воронку');
    await page.getByText('Версия 1 опубликована', {exact: false}).waitFor();

    await tab(page, 'Автоматизации').click();
    await submit(page, 'Новое правило');
    await dialog(page).getByLabel('Название', {exact: true}).fill('Уведомление о консультации');
    await dialog(page).locator('[name=trigger]').selectOption('deal.stage_changed');
    await submit(dialog(page), 'Добавить условие');
    await dialog(page).locator('[data-cond-field]').selectOption('transition.to');
    await dialog(page).getByLabel('Значение', {exact: true}).fill('consultation');
    await dialog(page).locator('[data-action-type]').selectOption('email.send');
    await dialog(page).locator('[data-action-key=to]').fill('team@example.test');
    await dialog(page).locator('[data-action-key=subject]').fill('Новая консультация');
    await dialog(page).locator('[data-action-key=text]').fill('Этап: {transition.to}');
    await submit(dialog(page), 'Сохранить черновик');
    await submit(dialog(page), 'Симуляция');
    await submit(dialog(page), 'Запустить симуляцию');
    await dialog(page).getByText('Условия выполнены. Ничего не отправлено.').waitFor();
    await submit(dialog(page), 'Готово');
    await submit(dialog(page), 'Опубликовать');
    await submit(dialog(page), 'Опубликовать');
    await dialog(page).getByText('Опубликованная версия', {exact: false}).waitFor();
    await close(page);

    const manager = await browser.newContext();
    const staff = await manager.newPage();
    staff.on('pageerror', error => errors.push(error.message));
    await login(staff, 'browser-manager-test', 'browser-password-25', 'deals');
    await staff.locator('[data-deal="9990000001"]').click();
    await dialog(staff).locator('select[name=stage]').selectOption('consultation');
    await submit(dialog(staff), 'Перевести сделку');
    await dialog(staff).getByText('Этап: Повторная консультация', {exact: true}).waitFor();
    assert.equal((await manager.request.get(origin + 'api/platform/admin/catalog')).status(), 403);
    await manager.close();

    await page.reload();
    await tab(page, 'Автоматизации').click();
    await submit(page.locator('.pa-row').filter({hasText: 'Уведомление о консультации'}), 'Открыть');
    await submit(dialog(page), 'Выполнения');
    await dialog(page).getByText('Успешно', {exact: true}).last().waitFor();
    await close(page);
    await close(page);

    await tab(page, 'Интеграции').click();
    await submit(page.locator('.pa-row').filter({hasText: 'Mango Office'}), 'Проверить');
    await page.locator('[data-operation]').getByText('Успешно', {exact: true}).waitFor();

    await tab(page, 'Журнал').click();
    await page.locator('[data-audit-filter]').selectOption('settings');
    await submit(page.locator('[data-audit-list] .pa-row').first(), 'Подробнее');
    await dialog(page).getByText('После изменения', {exact: true}).waitFor();
    await submit(dialog(page), 'Восстановить «после»');
    await submit(dialog(page), 'Восстановить');
    await page.getByText('Версия настроек восстановлена').waitFor();

    await submit(page, 'Новая компания');
    await dialog(page).getByLabel('Название компании').fill('Север QA');
    await dialog(page).getByLabel('Логин директора').fill('north-browser-test');
    await dialog(page).getByLabel('Пароль директора').fill('north-password-test');
    await submit(dialog(page), 'Создать компанию');
    await page.getByRole('heading', {name: 'Север QA', exact: true}).waitFor();
    await page.getByRole('searchbox', {name: 'Найти компанию'}).fill('Averon');
    await page.getByRole('button', {name: /Averon QA/}).click();
    await page.getByRole('heading', {name: 'Averon QA', exact: true}).waitFor();

    const mobile = await browser.newContext({viewport: {width: 390, height: 844}, storageState: await admin.storageState()});
    const phone = await mobile.newPage();
    phone.on('pageerror', error => errors.push(error.message));
    await visit(phone, 'platform-admin/1/pipelines');
    await phone.getByRole('heading', {name: 'Воронки продаж', exact: true}).waitFor();
    const picker = phone.getByRole('button', {name: /Выбрать компанию/});
    await picker.click();
    await phone.getByRole('searchbox', {name: 'Найти компанию'}).waitFor();
    await picker.click();
    await phone.getByRole('searchbox', {name: 'Найти компанию'}).waitFor({state: 'hidden'});
    assert(await phone.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    if (out) await phone.screenshot({path: path.join(out, 'mobile.png')});

    assert.deepEqual(errors, []);
    assert(observed.some(item => item.method === 'PATCH' && item.path === route + '/settings'));
    assert(observed.some(item => item.method === 'POST' && item.path.endsWith('/publish')));
    assert(observed.every(item => item.headers['x-crm'] === '1' && /^[a-f0-9]{40}$/.test(item.headers['idempotency-key'])));
    console.log(`Browser workflow passed, ${observed.length} authenticated admin mutations.`);
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
