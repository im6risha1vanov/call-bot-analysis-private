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
async function main(){
 const browser=await chromium.launch({headless:true,...(process.env.CRM_LEARNING_CHROMIUM?{executablePath:process.env.CRM_LEARNING_CHROMIUM}:{})});
 fs.mkdirSync(out,{recursive:true,mode:0o700});
 try{
  for(const theme of ['light','dark'])for(const role of ['director','manager']){
   const context=await browser.newContext({viewport:{width:1600,height:1000},colorScheme:theme});
   const page=await context.newPage(),apiRequests=[],errors=[];
   page.on('request',req=>{if(new URL(req.url()).pathname.startsWith('/api/'))apiRequests.push({url:req.url(),method:req.method()});});
   page.on('pageerror',e=>errors.push(e.message));
   await page.goto(new URL('/demo/'+role,base).href);
   await page.getByRole('heading',{name:'Обучение',exact:true}).waitFor();
   assert(new URL(page.url()).pathname==='/learning/workspace');
   await page.evaluate(t=>applyTheme(t),theme);
   const id=role==='director'?'d2':'m2';
   await page.locator('[data-lesson="'+id+'"]').click();
   const card=page.locator('[data-learning-guide]');await card.waitFor();
   assert.equal(await card.evaluate(n=>getComputedStyle(n).position),'fixed');
   assert.equal(await card.evaluate(n=>n.parentElement===document.body),true);
   const palette=await card.evaluate(n=>{const probe=document.createElement('span');probe.style.position='fixed';probe.style.visibility='hidden';document.body.appendChild(probe);probe.style.color='var(--surface)';const surface=getComputedStyle(probe).color;probe.style.color='var(--fg)';const fg=getComputedStyle(probe).color;probe.remove();return {surface,fg,background:getComputedStyle(n).backgroundColor,color:getComputedStyle(n).color};});
   assert.equal(palette.background,palette.surface);assert.equal(palette.color,palette.fg);
   assert.equal(await page.locator('.banner').filter({hasText:'Учебная учётка'}).count(),1);
   if(role==='director'){
    const filter=page.locator('[data-ext-pick]');await filter.waitFor();
    assert.equal(await card.getAttribute('data-learn-step'),'0');
    assert.equal((await page.locator('[data-learn-feedback]').textContent()).trim(),'');
    assert(await page.locator('[data-learn-next]').isDisabled());
    await filter.click();await page.keyboard.press('Escape');
    await filter.selectOption('101');assert(await page.locator('[data-learn-next]').isDisabled());
    assert((await page.locator('[data-learn-feedback]').textContent()).includes('Бориса'));
    await filter.selectOption('102');await page.waitForFunction(()=>!document.querySelector('[data-learn-next]').disabled);
    assert.equal(await card.getAttribute('data-learn-step'),'0');
    const control=await filter.boundingBox(),box=await card.boundingBox();assert(!intersects(box,control,2));
    // Card leaves all page layout intact: removing it changes no header/table geometry.
    const header=await page.locator('#top').boundingBox(),table=await page.locator('#view').boundingBox();
    await card.evaluate(n=>n.style.display='none');const without=await page.locator('#top').boundingBox(),withoutTable=await page.locator('#view').boundingBox();await card.evaluate(n=>n.style.display='');await page.waitForTimeout(60);
    assert.deepEqual(header,without);assert.deepEqual(table,withoutTable);
    const filename=theme==='light'?'director-d2-filter.png':'director-d2-filter-dark.png';await page.screenshot({path:path.join(out,filename)});
    await page.locator('[data-learn-next]').click();await page.waitForFunction(()=>document.querySelector('[data-learning-guide]')?.dataset.learnStep==='1');
    assert.equal(await card.getAttribute('data-learn-step'),'1');
   }else{
    await page.locator('[data-next] [data-i="1"]').click();await page.locator('[data-learn-next]').click();
    await page.locator('[data-out="callback"]').click();
    const packet=await page.locator('#learning-workspace-context').textContent(),clock=JSON.parse(packet).clock;
    await page.locator('[data-f="date"]').fill(clock.callback.due_date);await page.locator('[data-f="time"]').fill(clock.callback.due_time);
    await page.locator('[data-f="comment"]').fill('Обсудить предложение');
    await page.waitForFunction(()=>document.querySelector('.outform')&&!document.querySelector('.outform').classList.contains('hidden'));
    for(const selector of ['[data-f="date"]','[data-f="time"]','[data-act="save"]'])assert(!intersects(await card.boundingBox(),await page.locator(selector).boundingBox(),2));
    const filename=theme==='dark'?'manager-m2-callback.png':'manager-m2-callback-light.png';await page.screenshot({path:path.join(out,filename)});
    await page.setViewportSize({width:390,height:844});await page.locator('[data-f="date"]').focus();await page.waitForTimeout(100);
    for(const selector of ['[data-f="date"]','[data-f="time"]','[data-act="save"]']) {const hint=await card.boundingBox();if(hint)assert(!intersects(hint,await page.locator(selector).boundingBox(),2));}
    await page.setViewportSize({width:1600,height:1000});await page.waitForTimeout(100);
    await page.locator('[data-act="save"]').click();await page.waitForFunction(()=>!document.querySelector('[data-learn-next]').disabled);
    const step=await card.getAttribute('data-learn-step');await page.locator('[data-learn-close]').click();assert.equal(await card.count(),0);assert.equal(await page.locator('.learn-highlight').count(),0);
    await page.locator('[data-learn-resume]').click();await card.waitFor();assert.equal(await card.getAttribute('data-learn-step'),step);
   }
   await page.setViewportSize({width:390,height:844});
   await page.locator('[data-learning-guide]').waitFor();
   await page.evaluate(()=>window.dispatchEvent(new Event('resize')));
   await page.waitForTimeout(100);
   const box=await card.boundingBox();if(box){assert(box.x>=0&&box.y>=0&&box.x+box.width<=391&&box.y+box.height<=845);}
   await page.keyboard.press('Tab');
   assert.equal(apiRequests.length,0,'A browser request escaped the local classroom: '+JSON.stringify(apiRequests));assert.equal(errors.length,0,errors.join(';'));
   rows.push({role,theme,result:'passed',apiRequests:0,cardPlacement:await card.getAttribute('data-placement')});
   await context.close();
  }
 }finally{await browser.close();}
 fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({actualBrowser:true,base:base.href,rows,note:'Desktop/mobile viewport checks; physical mobile OS keyboard still needs device verification.'},null,2),{mode:0o600});
 console.log('Actual browser checks passed. Screenshots and results:',out);
}
main().catch(e=>{console.error('FAILED / INCOMPLETE:',e.stack);process.exitCode=1;});
