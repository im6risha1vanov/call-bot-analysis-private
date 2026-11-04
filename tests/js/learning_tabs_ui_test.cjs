/* Real app.js and ordinary CRM views, using an explicit DOM/event adapter.
   No iframe/practice.js controller is used. No layout/CSP claim. */
'use strict';
const assert=require('node:assert/strict'),vm=require('node:vm');
const {Document,settle}=require('./dom_adapter.cjs');
const {Storage,packet,read,clock}=require('./learning_workspace_test.cjs');
let nonce=0;
async function open(role='manager',entry='same-entry',storage=new Storage(),modules,hash='#learning',learning=true){
 const document=new Document(),location={pathname:learning?'/learning/workspace':'/',origin:'https://crm.invalid',href:'https://crm.invalid/learning/workspace',hash};
 const bootstrap=document.createElement('script');bootstrap.id='learning-workspace-context';bootstrap.textContent=JSON.stringify(packet(role,entry,'ui-account',modules));document.head.appendChild(bootstrap);document.body.innerHTML='<div id="root"></div>';
 const network=[],errors=[];const backend=learning?null:require('./learning_workspace_test.cjs').model(role);if(backend)backend.start('m1');
 const globals={console,URL,URLSearchParams,Response,Uint32Array,JSON,Number,document,location,crypto:{getRandomValues(a){a[0]=++nonce;return a;}},sessionStorage:storage,localStorage:new Storage(),matchMedia:()=>({matches:true}),innerWidth:1300,innerHeight:800,
  performance:{now:()=>0},requestAnimationFrame:fn=>fn(1000),scrollTo(){},getComputedStyle:()=>({}),queueMicrotask,
  setTimeout:(fn,ms)=>ms===1200?(queueMicrotask(fn),0):setTimeout(fn,0),clearTimeout,setInterval:()=>0,clearInterval(){},
  addEventListener(){},removeEventListener(){},navigator:{clipboard:{writeText:async()=>{} }},history:{pushState:(_,__,hash)=>location.hash=hash,replaceState:(_,__,hash)=>location.hash=hash},
  async fetch(resource,init={}){if(learning){network.push('RAW_FETCH');throw Error('NETWORK');}const opts={method:init.method||'GET',body:init.body?JSON.parse(init.body):undefined};network.push({path:resource,...opts});const data=await backend.request(resource,opts);if(resource==='/api/me')Object.assign(data,{learning:false,test:false,readonly:false});return new Response(JSON.stringify(data),{status:200});},WebSocket(){network.push('SOCKET');throw Error('NETWORK');},
  CustomEvent:class{constructor(type,values){this.type=type;Object.assign(this,values);}},
  MutationObserver:class{constructor(fn){this.fn=fn;}observe(){if(!document.observers.includes(this))document.observers.push(this);}disconnect(){document.observers=document.observers.filter(o=>o!==this);}}};
 globals.window=globals;
 const context=vm.createContext(globals);
 const run=code=>vm.runInContext(code,context,{timeout:10000});
 for(const file of ['learning/engine.js','learning/workspace-model.js','learning/workspace.js','app.js','helper.js','work.js','summary.js','calls.js','deals.js','people.js','more.js','review.js','automations.js','robot.js','learning/progress.js','learning/placement.js','learning/learning.js'])run(read('crm/static/'+file));
 document.onScript=script=>{try{if(script.src.includes('logic.js'))run(read('crm/static/contracts/logic.js'));if(script.src.includes('generator.js'))run(read('crm/static/contracts/generator.js'));script.onload?.();}catch(e){errors.push(e.stack);script.onerror?.();}};
 for(const listener of document.listeners.DOMContentLoaded||[])await listener.fn();await settle();
 return {context,document,globals,storage,network,errors,run,model:globals.AveronLearningWorkspace?.classroom};
}
async function click(ui,selector){const n=ui.document.querySelector(selector);assert(n,'Missing '+selector+'; page text: '+ui.document.body.textContent.slice(-1600));assert(!n.disabled,'Disabled '+selector);n.dispatch('click');await settle();}
async function fill(ui,selector,value,event='input'){const n=ui.document.querySelector(selector);assert(n,'Missing field '+selector);n.value=value;n.dispatch(event);await settle();}
async function start(ui){await click(ui,'[data-start-programme]');assert(ui.document.querySelector('[data-learning-guide]'),'No guide in CRM');assert(!ui.document.querySelector('iframe'));}
const state=ui=>ui.model.current();
function expected(ui){const s=state(ui),st=ui.model.lesson(ui.model.state.active).steps[s.step],r=ui.run('AveronLearningEngine.refs(AveronLearningWorkspace.classroom.current())'),a={};for(const[k,v]of Object.entries(st.expected))a[k]=Object.hasOwn(r,v)?r[v]:v;return {st,a};}
async function perform(ui){const {st,a}=expected(ui);assert(ui.document.querySelector('[data-example-image]'),'No automatic example for '+st.action);switch(st.action){
 case 'select_client':await click(ui,'[data-next] [data-i="1"]');break;
 case 'save_outcome':await click(ui,'[data-out="'+a.outcome+'"]');if(a.outcome==='callback'){await fill(ui,'[data-f="date"]',a.due_date);await fill(ui,'[data-f="time"]',a.due_time);await fill(ui,'[data-f="comment"]','Обсудить предложение');await click(ui,'[data-act="save"]');}break;
 case 'visit_page':if(st.screen.selector==='[data-contract]')await click(ui,'[data-contract]');else if(a.page==='settings'&&a.arg==='access')await click(ui,'[data-tab="access"]');else await click(ui,'#nav [data-go="'+a.page+'"]');break;
 case 'select_task':await click(ui,'.cb[data-id="'+a.task+'"] '+(a.open_context?'[data-call]':'[data-move]'));break;
 case 'move_task':await fill(ui,'.cb[data-id="'+a.task+'"] .move input[type="date"]',a.due_date);await fill(ui,'.cb[data-id="'+a.task+'"] .move input[type="time"]',a.due_time);await click(ui,'.cb[data-id="'+a.task+'"] [data-save]');break;
 case 'select_deal':await click(ui,'[data-deal="00000000001"]');break;
 case 'save_note':await click(ui,'[data-ev="note"]');await fill(ui,'[data-v="note"]',a.note);await click(ui,'.rvm [data-save]');break;
 case 'verify_history':await click(ui,'[data-deal="00000000001"]');break;
 case 'prepare_contract':await fill(ui,'#dg-name',a.company);await fill(ui,'#dg-inn',a.inn);ui.document.getElementById('dg-learningCheck').checked=true;await click(ui,'#dg-genBtn');break;
 case 'select_document':await click(ui,'[data-learning-target="contract-journal"] [data-deal="00000000001"]');break;
 case 'set_period':await click(ui,'[data-seg="period"] [data-v="'+a.period+'"]');break;
 case 'set_manager':if(ui.run('S.page')==='summary')await click(ui,'[data-seg="ext"] [data-v="102"]');else await fill(ui,'[data-ext-pick]','102','change');break;
 case 'select_call':if(ui.document.querySelector('[data-review="'+a.call+'"]'))await click(ui,'[data-review="'+a.call+'"]');else await click(ui,'tr[data-id="'+a.call+'"]');break;
 case 'close_review':case 'close_deal':await click(ui,'.rvm-x');break;
 case 'close_card':await click(ui,'#dr-x');break;
 case 'select_report':await click(ui,'[data-learning-report="'+a.report+'"]');break;
 case 'select_person':await click(ui,'[data-person="102"]');break;
 case 'filter_deals':await click(ui,'[data-seg="show"] [data-v="stalled"]');break;
 default:throw Error('No native action for '+st.action+'; '+ui.document.body.textContent.slice(-1500));
}}
async function until(ui,predicate){for(let n=0;n<100&&!predicate();n++){assert(state(ui)&&!ui.model.finished(),'Finished before target');const before=state(ui).step,action=expected(ui).st.action;await perform(ui);assert(state(ui).step>before,'No progress '+action+' '+state(ui).feedback+' '+ui.document.body.textContent.slice(-1200));}assert(predicate(),'No target after 100 actions');}
async function complete(ui){await until(ui,()=>ui.model.finished());assert.equal(ui.network.length,0);assert.equal(ui.errors.length,0,ui.errors.join(';'));}
async function main(){const cases=[],test=async(name,fn)=>{try{await fn();cases.push({name,result:'passed'});}catch(e){cases.push({name,result:'FAILED',error:e.stack});}};
 for(const role of ['manager','director'])await test(role+'-whole-programme-in-real-tabs-and-unlocks-every-replay',async()=>{
  const ui=await open(role);assert.equal(ui.document.querySelectorAll('[data-start-programme]').length,1);assert.equal(ui.document.querySelectorAll('[data-tab-lesson]').length,0);await start(ui);
  await complete(ui);assert(ui.model.courseDone());assert.equal(ui.document.querySelectorAll('[data-tab-lesson]').length,role==='manager'?6:9);
  assert(!ui.document.querySelector('[data-check-answer],[data-learn-next],[data-guide-next],[data-answer]'));assert(!/[МД][1-6]\./.test(ui.document.body.textContent));
  for(const t of ui.model.programme.tabs){await click(ui,'[data-tab-lesson="'+t.id+'"]');const firstId=state(ui).id;await complete(ui);assert(ui.model.courseDone());assert.equal(ui.document.querySelectorAll('[data-tab-lesson]').length,role==='manager'?6:9);await click(ui,'[data-tab-lesson="'+t.id+'"]');assert.notEqual(state(ui).id,firstId);assert.equal(state(ui).data.documents.length,t.id==='contracts'?0:ui.model.state.course.snapshots[t.id].data.documents.length);await complete(ui);}
 });
 await test('wrong-client-outcome-and-callback-date-do-not-progress',async()=>{
  const ui=await open();await start(ui);await click(ui,'[data-next] [data-i="0"]');assert.equal(state(ui).step,0);await perform(ui);const before=state(ui).step;await click(ui,'[data-out="not_interested"]');await fill(ui,'[data-f="comment"]','Не ответил');await click(ui,'[data-act="save"]');assert.equal(state(ui).step,before);assert.equal(state(ui).data.outcomes.length,0);await perform(ui);assert.equal(expected(ui).st.action,'save_outcome');await click(ui,'[data-out="callback"]');await fill(ui,'[data-f="date"]',clock.callback.due_date);await fill(ui,'[data-f="time"]','14:00');await fill(ui,'[data-f="comment"]','Обсудить предложение');const at=state(ui).step;await click(ui,'[data-act="save"]');assert.equal(state(ui).step,at);assert(!state(ui).createdTask);await fill(ui,'[data-f="time"]',clock.callback.due_time);await click(ui,'[data-act="save"]');assert.equal(expected(ui).st.action,'visit_page');assert.equal(state(ui).data.outcomes.length,2);assert.equal(new Set(state(ui).data.outcomes.map(x=>x.id)).size,2);assert.equal(ui.network.length,0);
 });
 await test('wrong-move-deal-note-and-contract-do-not-progress-or-write',async()=>{
  const ui=await open();await start(ui);await until(ui,()=>expected(ui).st.action==='move_task');await fill(ui,'.move input[type="date"]',clock.move.due_date);await fill(ui,'.move input[type="time"]','15:00');let at=state(ui).step;await click(ui,'.cb[data-id="task-created"] [data-save]');assert.equal(state(ui).step,at);assert.equal(state(ui).data.tasks.find(t=>t.id==='task-created').due_time,'15:00');await perform(ui);assert.equal(state(ui).data.tasks.filter(t=>t.client==='client-1'&&t.manager==='anna').length,1);
  await until(ui,()=>expected(ui).st.action==='select_deal');at=state(ui).step;await click(ui,'[data-deal="00000000002"]');assert.equal(state(ui).step,at);await click(ui,'.rvm-x');await perform(ui);await click(ui,'[data-ev="note"]');await fill(ui,'[data-v="note"]','Когда-нибудь');at=state(ui).step;await click(ui,'.rvm [data-save]');assert.equal(state(ui).step,at);assert.equal(state(ui).data.deals[0].history.length,1);await fill(ui,'[data-v="note"]',expected(ui).a.note);await click(ui,'.rvm [data-save]');await until(ui,()=>expected(ui).st.action==='prepare_contract');await fill(ui,'#dg-name','Другой клиент');await fill(ui,'#dg-inn','0000000000');ui.document.getElementById('dg-learningCheck').checked=true;at=state(ui).step;await click(ui,'#dg-genBtn');assert.equal(state(ui).step,at);assert.equal(state(ui).data.documents.length,0);await perform(ui);assert.equal(state(ui).data.documents.length,1);assert.equal(ui.network.length,0);
 });
 await test('wrong-manager-period-call-task-report-person-do-not-progress',async()=>{
  const ui=await open('director');await start(ui);await click(ui,'[data-seg="period"] [data-v="30"]');assert.equal(state(ui).step,0);await perform(ui);let at=state(ui).step;await click(ui,'[data-seg="ext"] [data-v="101"]');assert.equal(state(ui).step,at);await perform(ui);await until(ui,()=>expected(ui).st.action==='select_call');at=state(ui).step;await click(ui,'tr[data-id="call-weekly"]');assert.equal(state(ui).step,at);await click(ui,'.rvm-x');await perform(ui);await perform(ui);await until(ui,()=>expected(ui).st.action==='select_task');at=state(ui).step;await click(ui,'.cb[data-id="task-today"] [data-call]');assert.equal(state(ui).step,at);await click(ui,'#dr-x');await perform(ui);await perform(ui);await until(ui,()=>expected(ui).st.action==='select_report');at=state(ui).step;await click(ui,'[data-learning-report="report-other"]');assert.equal(state(ui).step,at);await perform(ui);await until(ui,()=>expected(ui).st.action==='select_person');at=state(ui).step;await click(ui,'[data-person="101"]');assert.equal(state(ui).step,at);await click(ui,'#nav [data-go="staff"]');await complete(ui);
 });
 await test('pause-refresh-resume-same-step-and-new-entry-clears',async()=>{
  let ui=await open();await start(ui);await perform(ui);await perform(ui);await click(ui,'[data-out="callback"]');await fill(ui,'[data-f="date"]',clock.callback.due_date);await fill(ui,'[data-f="time"]',clock.callback.due_time);await fill(ui,'[data-f="comment"]','Обсудить предложение');const id=state(ui).id,at=state(ui).step;
  await click(ui,'[data-learn-close]');assert(ui.model.state.paused);assert(!ui.document.querySelector('[data-learning-guide]'));assert.equal(ui.document.querySelectorAll('.learn-highlight').length,0);ui=await open('manager','same-entry',ui.storage,undefined,'#queue');assert.equal(state(ui).step,at);assert(ui.model.state.paused);await click(ui,'[data-learn-resume]');assert.equal(state(ui).id,id);assert.equal(ui.document.querySelector('[data-f="comment"]').value,'Обсудить предложение');await click(ui,'[data-act="save"]');assert.equal(state(ui).data.tasks.filter(t=>t.id==='task-created').length,1);ui=await open('manager','another-entry',ui.storage);assert.equal(state(ui),null);assert.equal(ui.document.querySelectorAll('[data-tab-lesson]').length,0);
 });
 await test('refresh-restores-local-move-note-contract-and-review',async()=>{
  let ui=await open();await start(ui);await until(ui,()=>expected(ui).st.action==='move_task');await fill(ui,'.move input[type="time"]','11:30');ui=await open('manager','same-entry',ui.storage,undefined,'#callbacks');assert.equal(ui.document.querySelector('.move input[type="time"]').value,'11:30');assert.equal(state(ui).data.tasks.find(t=>t.id==='task-created').due_time,'15:00');await until(ui,()=>expected(ui).st.action==='save_note');await click(ui,'[data-ev="note"]');await fill(ui,'[data-v="note"]','Черновик');ui=await open('manager','same-entry',ui.storage,undefined,'#deals');assert.equal(ui.document.querySelector('[data-v="note"]').value,'Черновик');await fill(ui,'[data-v="note"]',expected(ui).a.note);await click(ui,'.rvm [data-save]');await until(ui,()=>expected(ui).st.action==='prepare_contract');await fill(ui,'#dg-name','Учебный Кедр');ui=await open('manager','same-entry',ui.storage,undefined,'#deals/contracts');assert.equal(ui.document.querySelector('#dg-name').value,'Учебный Кедр');assert.equal(state(ui).data.documents.length,0);await until(ui,()=>expected(ui).st.action==='close_review');const at=state(ui).step;ui=await open('manager','same-entry',ui.storage,undefined,'#calls');assert.equal(state(ui).step,at);assert(ui.document.querySelector('.rvm'));await complete(ui);
 });
 await test('direct-address-cannot-unlock-lessons-or-other-role-and-disabled-modules',async()=>{
  const ui=await open('manager','same-entry',new Storage(),[],'#learning/d1');assert.equal(state(ui),null);assert.equal(ui.document.querySelectorAll('[data-start-programme]').length,1);assert.throws(()=>ui.model.startTab('calls'));await start(ui);assert(!ui.model.programme.tabs.some(t=>t.id==='contracts'));await complete(ui);assert.equal(ui.document.querySelectorAll('[data-tab-lesson]').length,5);
  const d=await open('director','same-entry',new Storage(),[]);await start(d);assert(!d.model.programme.tabs.some(t=>['auto','robot'].includes(t.id)));await complete(d);assert.equal(d.document.querySelectorAll('[data-tab-lesson]').length,7);
 });
 await test('learning-actions-never-fall-back-to-working-api',async()=>{const ui=await open();await start(ui);for(const path of ['/api/dial','/api/inn','/api/access','/api/automations','/api/robot/cab/campaigns','/api/gpt'])await assert.rejects(ui.globals.AveronLearningWorkspace.request(path,{method:'POST',body:{}}));delete ui.globals.AveronLearningWorkspace;await assert.rejects(ui.run('api("/api/outcome",{method:"POST",body:{}})'));assert.equal(ui.network.length,0);});
 await test('hidden-tabs-use-more-and-navigation-does-not-auto-act',async()=>{
  const ui=await open();await start(ui);await until(ui,()=>expected(ui).st.action==='visit_page'&&expected(ui).a.page==='reports');
  ui.document.getElementById('nav').classList.add('hidden');ui.document.body.appendChild(ui.document.createElement('span'));await settle();assert(ui.document.querySelector('.learn-instruction').textContent.includes('Ещё'));const at=state(ui).step;await click(ui,'#tabbar [data-sheet]');assert.equal(state(ui).step,at);assert(ui.document.querySelector('.sheet-list [data-go="reports"]'));await click(ui,'.sheet-list [data-go="reports"]');assert.equal(state(ui).step,at+1);assert.equal(ui.network.length,0);
 });
 await test('completed-programme-survives-reload-but-module-change-relocks',async()=>{
  let ui=await open('director','same-entry',new Storage(),['automations']);await start(ui);await complete(ui);const saved=ui.storage;ui=await open('director','same-entry',saved,['automations']);assert(ui.model.courseDone());assert.equal(ui.document.querySelectorAll('[data-tab-lesson]').length,8);ui=await open('director','same-entry',saved,['robot']);assert(!ui.model.courseDone());assert.equal(ui.document.querySelectorAll('[data-tab-lesson]').length,0);assert.equal(ui.document.querySelectorAll('[data-start-programme]').length,1);assert.deepEqual(Object.keys(ui.model.state.course.snapshots),[]);
 });
 await test('return-to-programme-pauses-and-continues-without-losing-action',async()=>{
  const ui=await open();await start(ui);await perform(ui);const at=state(ui).step,id=state(ui).id;await click(ui,'#nav [data-go="learning"]');assert(ui.model.state.paused);assert(!ui.document.querySelector('[data-learning-guide]'));await click(ui,'[data-start-programme]');assert.equal(state(ui).step,at);assert.equal(state(ui).id,id);assert.equal(expected(ui).st.action,'save_outcome');
 });
 await test('working-login-keeps-native-transport-and-queue-advance',async()=>{const ui=await open('manager','unused',new Storage(),undefined,'#queue',false);assert.equal(ui.globals.AveronLearningWorkspace,undefined);await click(ui,'[data-next] [data-i="1"]');await click(ui,'[data-out="no_answer"]');assert.equal(ui.network.filter(r=>r.method==='POST').length,1);assert.equal(ui.run('S.qnum'),'00000000003');});
 console.log(JSON.stringify({mode:'real ordinary CRM controllers + DOM/event adapter; not browser',cases},null,2));if(cases.some(c=>c.result==='FAILED'))process.exitCode=1;
}
if(require.main===module)main().catch(e=>{console.error(e);process.exitCode=1;});
module.exports={open,click,fill,start,state,expected,perform,until,complete};
