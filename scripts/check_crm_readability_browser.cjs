/* Actual optional browser review. All requests are fulfilled locally with fictional
   data. No server, DB or providers are contacted. Never fabricate screenshots. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
let chromium;try{({chromium}=require(process.env.CRM_LEARNING_PLAYWRIGHT_MODULE||'playwright'));}catch(error){console.error('NOT RUN: Playwright/Chromium unavailable; no before/after screenshots created.');process.exit(2);}
const {packet}=require('../tests/js/learning_workspace_test.cjs'),{working}=require('../tests/js/readability_fixture.cjs');
const root=path.resolve(__dirname,'..'),before=process.env.CRM_READABILITY_BEFORE_ROOT||'/tmp/averon-crm-readability-before-20261105';
const out=path.resolve(process.env.CRM_READABILITY_SCREENSHOTS||path.join(root,'artifacts/readability'));
if(!fs.existsSync(path.join(before,'crm/static/index.html')))throw Error('Missing genuine before snapshot from a042710; do not substitute a mockup.');
const origin='http://127.0.0.1:8098',types={'.html':'text/html','.js':'application/javascript','.css':'text/css','.woff2':'font/woff2','.gif':'image/gif','.png':'image/png','.svg':'image/svg+xml'};
async function idle(page){await page.waitForFunction(()=>!window.AveronLearningWorkspace?.classroom?.presentationBusy&&!document.querySelector('[data-learning-success]'));}
async function prepare(browser,phase,role,theme,width,learning){
 const context=await browser.newContext({viewport:{width,height:width<600?844:1000},colorScheme:theme==='light'?'light':'dark',serviceWorkers:'block'}),page=await context.newPage(),backend=working(role),requests=[];
 const source=phase==='before'?before:root;
 const data=packet(role,'readability-'+phase+'-'+role,'fictional-review');
 if(phase==='before')data.programme=JSON.parse(fs.readFileSync(path.join(before,'crm/learning_programme.json'),'utf8'))[role];
 await page.route('**/*',async route=>{
  const req=route.request(),u=new URL(req.url());
  if(u.origin!==origin){requests.push('BLOCKED '+u.origin);return route.abort();}
  if(u.pathname.startsWith('/static/')){
   const file=path.resolve(source,'crm',u.pathname.slice(1));if(!file.startsWith(path.join(source,'crm/static')+path.sep))return route.abort();
   if(!fs.existsSync(file))return route.fulfill({status:404,body:'Asset missing'});
   return route.fulfill({body:fs.readFileSync(file),contentType:types[path.extname(file)]||'application/octet-stream'});
  }
  if(u.pathname.startsWith('/api/')){
   requests.push({path:u.pathname,method:req.method()});try{return route.fulfill({json:await backend.request(u.pathname+u.search,{method:req.method(),body:req.postData()?JSON.parse(req.postData()):undefined})});}catch(error){return route.fulfill({status:403,json:{error:error.message}});}
  }
  let html=fs.readFileSync(path.join(source,'crm/static/index.html'),'utf8');
  if(learning)html=html.replace('<head>','<head><script type="application/json" id="learning-workspace-context">'+JSON.stringify(data).replace(/</g,'\\u003c')+'</script>');
  return route.fulfill({contentType:'text/html',body:html});
 });
 await page.goto(origin+(learning?'/learning/workspace#learning':'/#queue'));await page.locator('#view').waitFor();await page.evaluate(t=>applyTheme(t==='auto'?null:t),theme);
 return {page,context,requests,backend};
}
async function main(){
 const browser=await chromium.launch({headless:true,...(process.env.CRM_LEARNING_CHROMIUM?{executablePath:process.env.CRM_LEARNING_CHROMIUM}:{})}),rows=[];
 fs.mkdirSync(out,{recursive:true,mode:0o700});
 try{for(const phase of ['before','after'])for(const role of ['manager','director'])for(const theme of ['light','dark','auto'])for(const width of [1600,390]){
  const b=await prepare(browser,phase,role,theme,width,false),{page}=b;
  await page.locator('[data-next] [data-i="1"]').click();await page.locator('[data-out="callback"]').click();await page.locator('[data-f="date"]').fill('2026-11-06');await page.locator('[data-f="time"]').fill('15:00');await page.locator('[data-f="comment"]').fill('Обсудить предложение');
  const capture=async(scene,p)=>{if(theme==='dark'&&width===1600)await p.screenshot({path:path.join(out,phase+'-'+role+'-'+scene+'.png'),fullPage:false});};
  await capture('callback',page);
  if(phase==='after'){
   await page.locator('[data-page-help]').click();await page.locator('.page-help').waitFor();await capture('help',page);await page.locator('[data-page-help-close]').click();assert.equal(await page.locator('[data-f="date"]').inputValue(),'2026-11-06');assert.equal(await page.locator('[data-f="comment"]').inputValue(),'Обсудить предложение');
  }else await capture('help-absent',page);
  assert.equal(b.requests.filter(r=>typeof r==='object'&&r.method!=='GET').length,0);await b.context.close();
  const l=await prepare(browser,phase,role,theme,width,true),lp=l.page;await lp.locator('[data-start-programme]').click();await idle(lp);
  if(role==='manager'){
   await lp.locator('[data-next] [data-i="1"]').click();await idle(lp);await lp.locator('[data-out="no_answer"]').click();await idle(lp);await lp.locator('[data-out="callback"]').click();
  }
  await lp.locator('[data-learning-guide]').waitFor();await capture('lesson',lp);
  const figure=await lp.locator('[data-example-image]').getAttribute('src'),step=await lp.locator('[data-learning-guide]').getAttribute('data-learn-step');
  if(phase==='after'){
   const text=await lp.locator('[data-learning-guide]').textContent();assert(!text.includes('Europe/Moscow'));assert(!/\d{4}-\d{2}-\d{2}/.test(text));
   await lp.locator('[data-page-help]').click();await lp.locator('.page-help').waitFor();await lp.locator('[data-page-help-close]').click();assert.equal(await lp.locator('[data-learning-guide]').getAttribute('data-learn-step'),step);assert.equal(await lp.locator('[data-example-image]').getAttribute('src'),figure);
  }
  assert.equal(l.requests.filter(r=>typeof r==='object'&&r.method!=='GET').length,0);rows.push({phase,role,theme,width,result:'passed'});await l.context.close();
 }}finally{await browser.close();}
 fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({actualBrowser:true,fictionalData:true,rows},null,2),{mode:0o600});console.log('Actual before/after screenshots:',out);
}
main().catch(error=>{console.error('FAILED / INCOMPLETE:',error.stack);process.exitCode=1;});
