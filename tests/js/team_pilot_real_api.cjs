const {chromium} = require(process.env.PLAYWRIGHT_CORE);
(async () => {
 const browser = await chromium.launch({headless:true, executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe'});
 try {
  const page = await browser.newPage();
  const errors=[]; page.on('pageerror', e => errors.push(e.message));
  await page.context().addCookies([{name:'crm_session',value:process.env.PILOT_SESSION,url:process.env.PILOT_URL}]);
  // Navigation must work even while the first meetings request is pending.
  await page.route('**/api/team/meetings', async route => { await new Promise(r=>setTimeout(r,800)); await route.continue(); });
  await page.goto(process.env.PILOT_URL+'#team-pilot');
  await page.locator('.team-panel [data-route="assignments"]').click({timeout:15000});
  await page.locator('.team-panel [data-route="assignments-created"]').click({timeout:10000});
  await page.getByText('Тестовое поручение',{exact:true}).waitFor({timeout:10000});
  if(errors.length) throw Error('Browser errors: '+errors.join('; '));
  console.log('Real API browser pilot smoke passed');
 } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exit(1)});
