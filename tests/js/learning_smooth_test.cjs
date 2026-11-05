/* Actual controllers + local/fake APIs. No layout or real browser claims. */
'use strict';
const assert=require('node:assert/strict');
const {open,click,fill,start,state,expected,perform,until,complete}=require('./learning_tabs_ui_test.cjs');
const {settle}=require('./dom_adapter.cjs');
const {Storage}=require('./learning_workspace_test.cjs');
const {working}=require('./readability_fixture.cjs');
async function main(){const cases=[],test=async(name,fn)=>{try{await fn();cases.push({name,result:'passed'});}catch(error){cases.push({name,result:'FAILED',error:error.stack});}};
 await test('success-stays-in-the-same-card-and-keeps-gif-until-next-step',async()=>{
  const ui=await open();ui.globals.matchMedia=()=>({matches:false});await start(ui);
  const card=ui.document.querySelector('[data-learning-guide]'),image=card.querySelector('[data-example-image]'),source=image.src,timers=[],normal=ui.globals.setTimeout;
  ui.globals.setTimeout=(fn,ms)=>ms===1200?(timers.push(fn),0):normal(fn,ms);
  await click(ui,'[data-next] [data-i="1"]');assert.equal(ui.document.querySelector('[data-learning-guide]'),card);assert(card.contains(ui.document.querySelector('[data-learning-success]')));assert.equal(card.querySelector('[data-example-image]'),image);assert.equal(image.src,source);assert(!ui.document.querySelector('.toast'));assert.equal(ui.document.querySelectorAll('[data-learning-guide]').length,1);assert.equal(timers.length,1);
  ui.run('learningPageChanged()');await settle();assert.equal(card.querySelector('[data-example-image]'),image);assert.equal(timers.length,1);
  const r=card.getBoundingClientRect(),handle=card.querySelector('[data-learn-drag]');handle.dispatch('pointerdown',{pointerId:17,button:0,clientX:r.left,clientY:r.top});ui.document.body.dispatch('pointermove',{pointerId:17,clientX:700,clientY:500});ui.document.body.dispatch('pointerup',{pointerId:17,clientX:700,clientY:500});await settle();assert.notEqual(card.getBoundingClientRect().left,r.left);assert.equal(card.querySelector('[data-example-image]'),image);
  timers.shift()();await settle();assert.equal(ui.document.querySelector('[data-learning-guide]'),card);assert(!ui.document.querySelector('[data-learning-success]'));
  const nextImage=card.querySelector('[data-example-image]');await click(ui,'[data-out="no_answer"]');assert(!ui.document.querySelector('.toast'));assert.equal(card.querySelector('[data-example-image]'),nextImage);assert(card.querySelector('[data-learning-success]'));timers.shift()();await settle();assert.equal(ui.network.length,0);
 });
 await test('guided-forms-accept-other-valid-dates-notes-and-fictional-details',async()=>{
  const ui=await open();await start(ui);await until(ui,()=>expected(ui).st.action==='save_outcome'&&expected(ui).a.outcome==='callback');
  await click(ui,'[data-out="callback"]');await fill(ui,'[data-f="date"]','2026-12-03');await fill(ui,'[data-f="time"]','16:45');await fill(ui,'[data-f="comment"]','Согласовать объём');await click(ui,'[data-act="save"]');
  let task=state(ui).data.tasks.find(t=>t.id==='task-created');assert.equal(task.original_date,'2026-12-03');assert.equal(task.original_time,'16:45');assert.equal(task.note,'Согласовать объём');
  await until(ui,()=>expected(ui).st.action==='move_task');await fill(ui,'.move input[type="date"]','2026-12-04');await fill(ui,'.move input[type="time"]','09:20');await click(ui,'.cb[data-id="task-created"] [data-save]');
  task=state(ui).data.tasks.find(t=>t.id==='task-created');assert.equal(task.original_date,'2026-12-03');assert.equal(task.due_date,'2026-12-04');assert.equal(task.due_time,'09:20');assert.equal(state(ui).data.tasks.filter(t=>t.id==='task-created').length,1);
  await until(ui,()=>expected(ui).st.action==='save_note');await click(ui,'[data-ev="note"]');await fill(ui,'[data-v="note"]','Согласовать участников следующего разговора');await click(ui,'.rvm [data-save]');
  assert.equal(state(ui).data.deals[0].history.at(-1).text,'Согласовать участников следующего разговора');
  await until(ui,()=>expected(ui).st.action==='prepare_contract');await fill(ui,'#dg-name','Учебная Берёза');await fill(ui,'#dg-inn','1111111111');ui.document.getElementById('dg-learningCheck').checked=true;await click(ui,'#dg-genBtn');assert.equal(state(ui).data.documents[0].company,'Учебная Берёза');assert.equal(state(ui).data.documents[0].inn,'1111111111');await complete(ui);assert(ui.model.courseDone());assert.equal(ui.network.length,0);
 });
 await test('learning-background-tick-preserves-hint-gif-and-does-not-complete-an-action',async()=>{
  const ui=await open();ui.globals.matchMedia=()=>({matches:false});await start(ui);await click(ui,'[data-page-help]');
  const card=ui.document.querySelector('[data-learning-guide]'),image=card.querySelector('[data-example-image]'),source=image.src,help=ui.document.querySelector('[data-help-example-image]');
  const at=state(ui).step;await ui.intervals.find(t=>t.ms===60000).fn();await settle();
  assert.equal(state(ui).step,at);assert.equal(ui.document.querySelector('[data-learning-guide]'),card);assert.equal(card.querySelector('[data-example-image]'),image);assert.equal(image.src,source);assert.equal(ui.document.querySelector('[data-help-example-image]'),help);assert.equal(ui.network.length,0);
 });
 await test('guided-required-fields-and-wrong-record-still-do-not-save',async()=>{
  const ui=await open();await start(ui);await until(ui,()=>expected(ui).st.action==='save_outcome'&&expected(ui).a.outcome==='callback');const at=state(ui).step;
  const request=body=>ui.globals.AveronLearningWorkspace.request('/api/outcome',{method:'POST',body:{number:'00000000001',outcome:'callback',comment:'Своя заметка',due_date:'2026-12-03',due_time:'16:45',...body}});
  for(const body of [{due_date:'2026-02-30'},{due_time:'25:60'},{comment:''},{number:'00000000002'}]){await assert.rejects(request(body));assert.equal(state(ui).step,at);assert(!state(ui).createdTask);}
  assert.equal(ui.network.length,0);
 });
 for(const role of ['manager','director'])await test(role+'-background-tick-keeps-identical-view-history-and-help-image',async()=>{
  const backend=working(role),ui=await open(role,'quiet-'+role,new Storage(),undefined,'#queue',false,{backend});await click(ui,'[data-page-help]');await click(ui,'[data-help-example-toggle]');
  const view=ui.document.querySelector('#view'),work=ui.document.querySelector('[data-work]'),history=ui.document.querySelector('.hist'),child=history.firstChild,help=ui.document.querySelector('[data-help-example-image]'),source=help.src;
  const originalFetch=ui.globals.fetch,reverse=value=>Array.isArray(value)?value.map(reverse):value&&typeof value==='object'?Object.fromEntries(Object.entries(value).reverse().map(([key,v])=>[key,reverse(v)])):value;
  ui.globals.fetch=async(path,opts)=>{const response=await originalFetch(path,opts);return new Response(JSON.stringify(reverse(await response.json())),{status:response.status});};
  const timer=ui.intervals.find(t=>t.ms===60000);assert(timer);await timer.fn();await settle();
  assert.equal(ui.document.querySelector('#view'),view);assert.equal(ui.document.querySelector('[data-work]'),work);assert.equal(ui.document.querySelector('.hist'),history);assert.equal(history.firstChild,child);assert.equal(ui.document.querySelector('[data-help-example-image]'),help);assert.equal(help.src,source);assert.equal(backend.events.length,0);
  backend.base.base.calls.find(c=>c.client==='client-2').conclusion='Новое наблюдение по разговору';await timer.fn();await settle();assert.equal(ui.document.querySelector('[data-work]'),work);assert.equal(ui.document.querySelector('.hist'),history);assert(history.textContent.includes('Новое наблюдение'));assert.equal(ui.document.querySelector('[data-help-example-image]'),help);
  backend.base.base.clients[0].company='Учебный Кедр · Новые данные';await timer.fn();await settle();assert.equal(ui.document.querySelector('#view'),view);assert.notEqual(ui.document.querySelector('[data-work]'),work);assert(ui.document.querySelector('#view').textContent.includes('Новые данные'));assert.equal(ui.document.querySelector('[data-help-example-image]'),help);assert.equal(backend.events.length,0);
 });
 await test('quiet-refresh-does-not-replace-a-form-opened-while-request-is-pending',async()=>{
  const backend=working(),ui=await open('manager','race',new Storage(),undefined,'#queue',false,{backend}),fetch=ui.globals.fetch;let release;
  ui.globals.fetch=(path,opts)=>path.startsWith('/api/queue')?new Promise(resolve=>release=()=>fetch(path,opts).then(resolve)):fetch(path,opts);
  const pending=ui.run('refreshQueueInBackground()');assert(release);await click(ui,'[data-out="callback"]');await fill(ui,'[data-f="comment"]','Не потерять черновик');const field=ui.document.querySelector('[data-f="comment"]');release();assert.equal(await pending,false);assert.equal(ui.document.querySelector('[data-f="comment"]'),field);assert.equal(field.value,'Не потерять черновик');assert.equal(backend.events.length,0);
 });
 await test('quiet-refresh-error-and-overlapping-calls-keep-visible-content',async()=>{
  const backend=working(),ui=await open('manager','quiet-error',new Storage(),undefined,'#queue',false,{backend}),originalFetch=ui.globals.fetch,work=ui.document.querySelector('[data-work]');
  ui.globals.fetch=async()=>{throw Error('synthetic network failure');};assert.equal(await ui.run('refreshQueueInBackground()'),false);assert.equal(ui.document.querySelector('[data-work]'),work);assert(!ui.document.querySelector('.toast'));
  let release;ui.globals.fetch=(path,opts)=>path.startsWith('/api/queue')?new Promise(resolve=>release=()=>resolve(new Response(JSON.stringify({today:'2026-11-04',later:0,items:[]})))):originalFetch(path,opts);const pending=ui.run('refreshQueueInBackground()');assert.equal(await ui.run('refreshQueueInBackground()'),false);await ui.run('go("reports")').catch(()=>{});release();assert.equal(await pending,false);assert.equal(ui.run('S.page'),'reports');
 });
 console.log(JSON.stringify({mode:'actual controllers and explicit DOM with fake/local APIs, not browser',cases},null,2));if(cases.some(c=>c.result==='FAILED'))process.exitCode=1;
}
main().catch(error=>{console.error(error);process.exitCode=1;});
