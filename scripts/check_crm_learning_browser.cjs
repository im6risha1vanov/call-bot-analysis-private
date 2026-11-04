/* Real browser gate and screenshots. Requires Playwright + installed Chromium in
   a test environment. Runs ONLY against a loopback fictional learning bench. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const base=new URL(process.env.CRM_LEARNING_BASE_URL||'http://127.0.0.1:8098');
if(!['localhost','127.0.0.1','[::1]'].includes(base.hostname)||base.protocol!=='http:')throw Error('Use the isolated loopback learning bench, never production.');
const out=path.resolve(process.env.CRM_LEARNING_SCREENSHOTS||path.join(__dirname,'../artifacts/learning-floating'));
let chromium;
try{({chromium}=require(process.env.CRM_LEARNING_PLAYWRIGHT_MODULE||'playwright'));}
catch(e){console.error('NOT RUN: Playwright is unavailable. No browser checks or screenshots were produced.');process.exit(2);}
const rows=[];
function intersects(a,b,gap=0){return a.x<b.x+b.width+gap&&a.x+a.width>b.x-gap&&a.y<b.y+b.height+gap&&a.y+a.height>b.y-gap;}
async function state(page){return page.evaluate(()=>{const w=AveronLearningWorkspace.classroom,a=w.current(),st=w.lesson(w.state.active).steps[a.step],refs=AveronLearningEngine.refs(a);return {step:a.step,finished:w.finished(),st,values:st?Object.fromEntries(Object.entries(st.expected).map(([k,v])=>[k,Object.hasOwn(refs,v)?refs[v]:v])):{}};});}
async function perform(page){await page.waitForFunction(()=>!AveronLearningWorkspace.classroom.presentationBusy);const {st,values:a}=await state(page),click=s=>page.locator(s).last().click(),fill=(s,v)=>page.locator(s).fill(v);switch(st.action){
 case 'select_client':await click('[data-next] [data-i="1"]');break;
 case 'save_outcome':await click('[data-out="'+a.outcome+'"]');if(a.outcome==='callback'){await fill('[data-f="date"]',a.due_date);await fill('[data-f="time"]',a.due_time);await fill('[data-f="comment"]','Обсудить предложение');await click('[data-act="save"]');}break;
 case 'visit_page':{
  let target=page.locator(st.screen.selector).last();
  if(st.screen.selector.startsWith('#nav')&&!await target.isVisible()){
   const more=page.locator('#nav .more');if(await more.isVisible())await more.click();else await page.locator('#tabbar [data-sheet]').click();
   const choices=page.locator('[data-go="'+a.page+'"]');for(let i=0;i<await choices.count();i++)if(await choices.nth(i).isVisible()){target=choices.nth(i);break;}
  }
  await target.click();break;
 }
 case 'select_task':await click('.cb[data-id="'+a.task+'"] '+(a.open_context?'[data-call]':'[data-move]'));break;
 case 'move_task':await fill('.move input[type="date"]',a.due_date);await fill('.move input[type="time"]',a.due_time);await click('.cb[data-id="'+a.task+'"] [data-save]');break;
 case 'select_deal':await click('[data-deal="00000000001"]');break;
 case 'save_note':await click('[data-ev="note"]');await fill('[data-v="note"]',a.note);await click('.rvm [data-save]');break;
 case 'verify_history':await click('[data-deal="00000000001"]');break;
 case 'prepare_contract':await fill('#dg-name',a.company);await fill('#dg-inn',a.inn);await page.locator('#dg-learningCheck').check();await click('#dg-genBtn');break;
 case 'select_document':await click('[data-learning-target="contract-journal"] [data-deal="00000000001"]');break;
 case 'set_period':await click('[data-seg="period"] [data-v="'+a.period+'"]');break;
 case 'set_manager':if(await page.locator('[data-ext-pick]').count())await page.locator('[data-ext-pick]').selectOption('102');else await click('[data-seg="ext"] [data-v="102"]');break;
 case 'select_call':if(await page.locator('[data-review="'+a.call+'"]').count())await click('[data-review="'+a.call+'"]');else await click('tr[data-id="'+a.call+'"]');break;
 case 'close_review':case 'close_deal':await click('.rvm-x');break;
 case 'close_card':await click('#dr-x');break;
 case 'select_report':await click('[data-learning-report="'+a.report+'"]');break;
 case 'select_person':await click('[data-person="102"]');break;
 case 'filter_deals':await click('[data-seg="show"] [data-v="stalled"]');break;
 default:throw Error('Unimplemented native browser action: '+st.action);
}}
async function main(){
 const browser=await chromium.launch({headless:true,...(process.env.CRM_LEARNING_CHROMIUM?{executablePath:process.env.CRM_LEARNING_CHROMIUM}:{})});fs.mkdirSync(out,{recursive:true,mode:0o700});
 try{for(const theme of ['light','dark'])for(const role of ['director','manager']){
  const context=await browser.newContext({viewport:{width:1600,height:1000},colorScheme:theme}),page=await context.newPage(),apiRequests=[],errors=[];
  page.on('request',req=>{if(new URL(req.url()).pathname.startsWith('/api/'))apiRequests.push({url:req.url(),method:req.method()});});page.on('pageerror',e=>errors.push(e.message));
  await page.goto(new URL('/demo/'+role,base).href);await page.locator('[data-start-programme]').waitFor();await page.evaluate(t=>applyTheme(t),theme);await page.locator('[data-start-programme]').click();
  const card=page.locator('[data-learning-guide]');await card.waitFor();assert.equal(await card.evaluate(n=>getComputedStyle(n).position),'fixed');assert.equal(await card.evaluate(n=>n.parentElement===document.body),true);assert.equal(await page.locator('[data-learn-next],[data-check-answer],[data-answer]').count(),0);assert.equal(await page.locator('.banner').filter({hasText:'Учебная учётка'}).count(),1);
  if(role==='director'){
   for(let n=0;n<100;n++){const before=await state(page);if(before.finished)break;await perform(page);await page.waitForFunction(v=>AveronLearningWorkspace.classroom.current().step>v,before.step);}
   await page.waitForFunction(()=>!AveronLearningWorkspace.classroom.presentationBusy);await page.locator('[data-tab-lesson="calls"]').click();const filter=page.locator('[data-ext-pick]');await filter.waitFor();assert.equal((await page.locator('[data-learn-feedback]').textContent()).trim(),'');
   const before=(await state(page)).step;await filter.click();await page.keyboard.press('Escape');await filter.selectOption('101');assert.equal((await state(page)).step,before);assert((await page.locator('[data-learn-feedback]').textContent()).includes('Бориса'));
   const control=await filter.boundingBox(),box=await card.boundingBox();assert(!intersects(box,control,2));const header=await page.locator('#top').boundingBox(),table=await page.locator('#view').boundingBox();await card.evaluate(n=>n.style.display='none');assert.deepEqual(header,await page.locator('#top').boundingBox());assert.deepEqual(table,await page.locator('#view').boundingBox());await card.evaluate(n=>n.style.display='');await page.waitForTimeout(60);
   await page.screenshot({path:path.join(out,theme==='light'?'director-filter.png':'director-filter-dark.png')});await filter.selectOption('102');await page.waitForFunction(v=>AveronLearningWorkspace.classroom.current().step===v+1,before);
  }else{
   await perform(page);await perform(page);await page.waitForFunction(()=>AveronLearningWorkspace.classroom.current().step===3);await page.locator('[data-out="callback"]').click();const {clock}=JSON.parse(await page.locator('#learning-workspace-context').textContent());
   await page.locator('[data-f="date"]').fill(clock.callback.due_date);await page.locator('[data-f="time"]').fill(clock.callback.due_time);await page.locator('[data-f="comment"]').fill('Обсудить предложение');await page.waitForTimeout(80);
   for(const s of ['[data-f="date"]','[data-f="time"]','[data-act="save"]'])assert(!intersects(await card.boundingBox(),await page.locator(s).boundingBox(),2));await page.screenshot({path:path.join(out,theme==='dark'?'manager-callback.png':'manager-callback-light.png')});
   const before=(await state(page)).step;await page.locator('[data-learn-close]').click();assert.equal(await card.count(),0);assert.equal(await page.locator('.learn-highlight').count(),0);await page.locator('[data-learn-resume]').click();await card.waitFor();assert.equal((await state(page)).step,before);
  }
  if(role==='director'){
   await page.waitForFunction(()=>!AveronLearningWorkspace.classroom.presentationBusy);await page.locator('[data-learn-catalog]').click();await page.locator('[data-tab-lesson="deals"]').click();await perform(page);await perform(page);await page.waitForFunction(()=>!AveronLearningWorkspace.classroom.presentationBusy);await page.locator('.rvm').waitFor();
   assert(await card.evaluate(n=>Number(getComputedStyle(n).zIndex)>Number(getComputedStyle(document.querySelector('.rvm')).zIndex)));
   assert(await card.evaluate(n=>{const r=n.getBoundingClientRect();return n.contains(document.elementFromPoint(r.left+r.width/2,r.top+r.height/2));}));
   const handle=page.locator('[data-learn-drag]'),r=await handle.boundingBox(),before=await card.boundingBox();await page.mouse.move(r.x+20,r.y+15);await page.mouse.down();await page.mouse.move(r.x+100,r.y+65,{steps:8});await page.mouse.up();const after=await card.boundingBox();assert(after.x!==before.x||after.y!==before.y);assert.equal(await page.locator('.rvm').count(),1);
  }
  await page.setViewportSize({width:390,height:844});await page.evaluate(()=>window.dispatchEvent(new Event('resize')));await page.waitForTimeout(100);const box=await card.boundingBox();if(box)assert(box.x>=0&&box.y>=0&&box.x+box.width<=391&&box.y+box.height<=845);await page.keyboard.press('Tab');assert.equal(apiRequests.length,0);assert.equal(errors.length,0,errors.join(';'));rows.push({role,theme,result:'passed',apiRequests:0});await context.close();
 }}finally{await browser.close();}
 fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({actualBrowser:true,base:base.href,rows,note:'Physical mobile OS keyboard still needs device verification.'},null,2),{mode:0o600});console.log('Actual browser checks passed. Screenshots and results:',out);
}
main().catch(e=>{console.error('FAILED / INCOMPLETE:',e.stack);process.exitCode=1;});
