/* Real CRM forms and guarded fetch. Fake authorized API; no real records or network. */
'use strict';
const assert=require('node:assert/strict');
const {open,click,fill}=require('./learning_tabs_ui_test.cjs');
const {working}=require('./readability_fixture.cjs');
const {Storage,read}=require('./learning_workspace_test.cjs');
const {settle}=require('./dom_adapter.cjs');
const config=JSON.parse(read('crm/learning_guide.json')),examples=JSON.parse(read('crm/learning_examples.json'));
function backend(role='manager',enabled=true,modules=['contracts','automations','robot','gpt']){
 const base=working(role),reads=[],posts=[];let mode=enabled;
 return {base,reads,posts,mode:()=>mode,async request(path,opts={}){
  const url=new URL(path,'https://crm.invalid');if((opts.method||'GET')==='GET')reads.push(url.pathname);
  if(url.pathname==='/api/learning/mode'){posts.push(path);mode=opts.body.enabled;return {enabled:mode};}
  if(url.pathname==='/api/learning/guide')return {role,tabs:config[role].filter(t=>!t.module||modules.includes(t.module)),examples,progress:'not_stored'};
  const data=await base.request(path,opts);if(url.pathname==='/api/me')return {...data,learning_mode:mode,can_dial:true,webphone:false,modules};return data;
 }};
}
async function ui(role='manager',enabled=true,hash='#learning',modules){const api=backend(role,enabled,modules);return {api,view:await open(role,'ordinary-account',new Storage(),modules,hash,false,{backend:api,guide:true})};}
async function next(view){await click(view,'[data-guide-continue]');}
async function complete(view,role){
 const tabs=config[role].filter(t=>!t.module||view.run('S.me.modules').includes(t.module));let steps=0;const trace=[];
 for(;steps<140;steps++){
  assert(!view.document.querySelector('[data-ui-error]'),view.document.querySelector('[data-ui-error]')?.textContent);const card=view.document.querySelector('[data-guide-card]');assert(card,'Guide missing: '+view.document.body.textContent.slice(-1500));const ti=tabs.findIndex(t=>t.id===card.dataset.guideTab),index=Number(card.dataset.guideStep),step=tabs[ti].steps[index];
  trace.push(card.dataset.guideTab+':'+index+':'+view.run('S.page')+':'+view.run('S.arg'));
  if(!step){if(ti===tabs.length-1){await next(view);return steps;}await next(view);continue;}
  assert(card.querySelector('[data-guide-image]'));let target=view.document.querySelector(step.selector);
  if(step.match)target=target?.querySelector(step.match);
  if(!target||target.disabled||step.event==='read'){await next(view);continue;}
  if(step.event==='change'){target.value='102';target.dispatch('change');await settle();}
  else if(step.event==='click'){if(target.matches('[data-seg]'))target=target.querySelector('button');assert(target);target.dispatch('click');await settle();}
 }
 throw Error('Guide did not end: '+trace.slice(-12).join(' -> '));
}
async function main(){const cases=[],test=async(name,fn)=>{try{await fn();cases.push({name,result:'passed'});}catch(error){cases.push({name,result:'FAILED',error:error.stack});}};
 for(const role of ['manager','director'])await test(role+'-ordinary-account-can-enable-and-disable-without-a-classroom',async()=>{
  const {api,view}=await ui(role,false);assert(view.document.querySelector('[data-guide-enable]'));assert(!view.document.body.textContent.includes('Войти в учебную учётку'));assert(!view.globals.AveronLearningWorkspace);assert(!view.globals.AveronClassroom);await click(view,'[data-guide-enable]');assert.equal(api.mode(),true);assert.equal(view.globals.location.reloadCount,1);assert.deepEqual(api.posts,['/api/learning/mode']);assert.equal(api.base.events.length,0);
  const current=await open(role,'same',new Storage(),undefined,'#learning/start',false,{backend:api,guide:true});assert(current.document.querySelector('[data-guide-banner]'));assert(current.document.querySelector('[data-guide-card]'));assert.equal(current.run('S.me.role'),role);assert.equal(current.run('S.me.test'),false);await click(current,'[data-guide-disable]');assert.equal(api.mode(),false);assert.equal(current.globals.location.reloadCount,1);
 });
 for(const role of ['manager','director'])await test(role+'-whole-guide-is-read-only-and-does-not-store-records-or-progress',async()=>{
  const {api,view}=await ui(role,true,'#learning/start');const before=JSON.stringify(api.base.base.base),session=JSON.stringify([...view.storage.m]),prefs=JSON.stringify([...view.globals.localStorage.m]);await complete(view,role);
  assert.equal(JSON.stringify(api.base.base.base),before);assert.equal(api.base.events.length,0);assert.deepEqual(api.posts,[]);assert.equal(JSON.stringify([...view.storage.m]),session);assert.equal(JSON.stringify([...view.globals.localStorage.m]),prefs);assert(!view.globals.AveronLearningWorkspace);assert.equal(view.errors.length,0,view.errors.join('\n'));
 });
 await test('dial-outcome-task-note-and-contract-cannot-reach-working-api',async()=>{
  const {api,view}=await ui('manager',true,'#learning/start');await click(view,'[data-next] [data-i="1"]');const card=view.document.querySelector('[data-guide-card]');
  const phone=view.document.querySelector('[data-guide-phone]');assert(phone);assert.equal(phone.getAttribute('href'),'#');phone.dispatch('click');await settle();await click(view,'[data-act="dial"]');assert(view.document.querySelector('[data-guide-feedback]').textContent.includes('звонок не начинается'));await click(view,'[data-out="no_answer"]');await click(view,'[data-out="callback"]');await fill(view,'[data-f="date"]','2026-12-03');await fill(view,'[data-f="time"]','16:45');await fill(view,'[data-f="comment"]','Ничего не отправлять');const image=card.querySelector('[data-guide-image]');await click(view,'[data-act="save"]');
  assert.equal(api.base.events.length,0);for(const path of ['/api/outcome','/api/dial','/api/tasks/123','/api/deals/00000000001/event','/api/contracts/register','/api/gpt','/api/robot/call'])await assert.rejects(view.run('api('+JSON.stringify(path)+',{method:"POST",body:{}})'));
  for(const path of ['/api/webphone','/api/contracts/lookup/inn/1111111111','/api/robot/voices/sample'])await assert.rejects(view.run('api('+JSON.stringify(path)+')'));
  assert.equal(api.base.events.length,0);assert(!view.network.some(e=>e==='SOCKET'||e.path==='/api/dial'));assert.equal(view.document.querySelector('[data-guide-card]'),card);
 });
 await test('hide-resume-gif-help-and-periodic-refresh-keep-the-same-card',async()=>{
  const {api,view}=await ui('manager',true,'#learning/start');const card=view.document.querySelector('[data-guide-card]'),image=card.querySelector('[data-guide-image]'),source=image.src;
  await click(view,'[data-guide-play]');const paused=image.src;await click(view,'[data-page-help]');assert.equal(card.querySelector('[data-guide-image]'),image);await click(view,'[data-page-help-close]');await view.intervals.find(t=>t.ms===60000).fn();await settle();assert.equal(view.document.querySelector('[data-guide-card]'),card);assert.equal(card.querySelector('[data-guide-image]'),image);assert.equal(image.src,paused);
  await click(view,'[data-guide-close]');assert(!view.document.querySelector('[data-guide-card]'));await click(view,'[data-guide-resume]');assert(view.document.querySelector('[data-guide-card]'));assert.equal(api.base.events.length,0);
 });
 await test('empty-pages-and-disabled-modules-explain-without-an-assignment',async()=>{
  const api=backend('manager',true,[]),original=api.request.bind(api);api.request=async(path,opts={})=>path.startsWith('/api/queue')?{today:'2026-11-04',items:[],later:0}:original(path,opts);
  const view=await open('manager','empty',new Storage(),[],'#learning/start',false,{backend:api,guide:true});assert(view.document.querySelector('[data-guide-missing]').textContent.includes('список может быть пуст'));await next(view);assert(view.document.querySelector('[data-guide-card]'));const data=await api.request('/api/learning/guide');assert(!data.tabs.some(t=>t.id==='contracts'));assert.equal(api.base.events.length,0);
 });
 await test('acknowledgement-keeps-the-same-card-and-gif-without-a-second-notice',async()=>{
  const {api,view}=await ui('manager',true,'#learning/start'),card=view.document.querySelector('[data-guide-card]'),image=card.querySelector('[data-guide-image]'),timers=[],normal=view.globals.setTimeout;
  view.globals.setTimeout=(fn,ms)=>ms===1200?(timers.push(fn),0):normal(fn,ms);
  await click(view,'[data-next] [data-i="1"]');assert.equal(view.document.querySelector('[data-guide-card]'),card);assert.equal(card.querySelector('[data-guide-image]'),image);assert(card.querySelector('[data-guide-ack]'));assert.equal(view.document.querySelectorAll('[data-guide-card]').length,1);assert(!view.document.querySelector('.toast'));assert.equal(api.base.events.length,0);timers.shift()();await settle();assert.equal(view.document.querySelector('[data-guide-card]'),card);
 });
 await test('cannot-enable-learning-during-an-existing-call-or-pending-operation',async()=>{
  const {api,view}=await ui('manager',false);view.run('WP.session={}');await click(view,'[data-guide-enable]');assert.equal(api.mode(),false);assert.equal(api.posts.length,0);view.run('WP.session=null');
  const original=api.request.bind(api);let release;api.request=(path,opts)=>path==='/api/outcome'?new Promise(resolve=>release=()=>original(path,opts).then(resolve)):original(path,opts);
  const pending=view.run('api("/api/outcome",{method:"POST",body:{number:"00000000001",outcome:"no_answer"}})');assert.equal(view.globals.CRMGuideGuard.pending(),1);await click(view,'[data-guide-enable]');assert.equal(api.posts.length,0);assert.equal(api.mode(),false);release();await pending;assert.equal(view.globals.CRMGuideGuard.pending(),0);
 });
 await test('normal-mode-operational-behaviour-is-restored-after-exit',async()=>{
  const {api,view}=await ui('manager',false,'#queue');await view.run('api("/api/outcome",{method:"POST",body:{number:"00000000001",outcome:"no_answer"}})');assert.equal(api.base.events.length,1);assert(!view.document.querySelector('[data-guide-banner]'));assert(!view.document.querySelector('[data-guide-card]'));
 });
 console.log(JSON.stringify({mode:'real native controllers with guarded fetch and fictional authorized API, not browser',cases},null,2));if(cases.some(c=>c.result==='FAILED'))process.exitCode=1;
}
main().catch(error=>{console.error(error);process.exitCode=1;});
