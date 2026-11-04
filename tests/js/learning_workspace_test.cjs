/* Actual classroom model + engine, no HTTP, DB, npm or model providers. */
'use strict';
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../..'),read=p=>fs.readFileSync(path.join(root,p),'utf8');
const catalog=JSON.parse(read('crm/learning_lessons.json'));
const clock={today:'2026-11-04',timezone:'Europe/Moscow',started_at:'2026-11-04T10:00:00Z',week_start:'2026-10-26',week_end:'2026-11-01',callback:{due_date:'2026-11-05',due_time:'15:00',moscow_date:'2026-11-05'},move:{due_date:'2026-11-06',due_time:'11:30',moscow_date:'2026-11-06'}};
class Storage{constructor(){this.m=new Map();}get length(){return this.m.size;}key(n){return [...this.m.keys()][n];}getItem(k){return this.m.get(k)||null;}setItem(k,v){this.m.set(k,v);}removeItem(k){this.m.delete(k);}}
let seed=0,network=0;
const ctx=vm.createContext({URL,Uint32Array,crypto:{getRandomValues(a){a[0]=++seed;return a;}},fetch(){network++;throw Error('Unexpected network');}});
for(const file of ['engine.js','workspace-model.js'])vm.runInContext(read('crm/static/learning/'+file),ctx,{filename:file});
function packet(role='manager', entry='entry-1',scope='account-1',modules=['contracts','automations','robot','gpt']){return {role,entry,scope,modules,clock,programme:JSON.parse(read('crm/learning_programme.json'))[role],lessons:catalog.lessons.filter(l=>l.role===role).map(l=>({...l,available:!l.module||modules.includes(l.module),guide:l.guide.filter(s=>!s.module||modules.includes(s.module))}))};}
function model(role='manager',entry='entry-1',storage=new Storage(),scope='account-1',modules){return new ctx.AveronClassroom.Classroom(packet(role,entry,scope,modules),storage);}
function values(m){const l=m.lesson(m.state.active),s=m.current(),step=l.steps[s.step],r=ctx.AveronLearningEngine.refs(s),a={type:step.action};for(const [k,v]of Object.entries(step.expected))a[k]=Object.hasOwn(r,v)?r[v]:v;if(a.comment)a.comment='Обсудить предложение';return a;}
async function perform(m,a){
 const num=ctx.AveronClassroom.number,ext=ctx.AveronClassroom.ext,per=m.current().ui.period,person=ext(m.current().ui.manager);
 const get=p=>m.request(p),post=(p,body)=>m.request(p,{method:'POST',body});
 switch(a.type){
 case 'select_client':return get('/api/card?number='+num(a.client));
 case 'save_outcome':return post('/api/outcome',{number:num(a.client),outcome:a.outcome,comment:a.comment,due_date:a.due_date,due_time:a.due_time});
 case 'open_section':return m.navigation(a.section==='contracts'?'deals':a.section,a.section==='contracts'?'contracts':null);
 case 'select_task':return m.observe('select_task',{task:a.task});
 case 'move_task':return post('/api/tasks/'+a.task,{action:'move',due_date:a.due_date,due_time:a.due_time});
 case 'select_deal':return get('/api/deals/'+num(m.data().deals.find(d=>d.id===a.deal).client));
 case 'save_note':return post('/api/deals/'+num(m.data().deals.find(d=>d.id===a.deal).client)+'/event',{kind:'note',note:a.note});
 case 'prepare_contract':return post('/api/contracts/register',{deal:num(m.data().deals.find(d=>d.id===a.deal).client),client:a.company,inn:a.inn});
 case 'select_document':return m.observe('select_document',{document:a.document});
 case 'set_period':return get('/api/'+(m.page==='summary'?'summary':'journal')+'?period='+a.period+'&ext='+person);
 case 'set_manager':if(m.page==='callbacks'){m.observe('set_manager',{manager:a.manager});return get('/api/callbacks?ext='+ext(a.manager));}return get('/api/'+(m.page==='summary'?'summary':'journal')+'?period='+per+'&ext='+ext(a.manager));
 case 'select_call':return get('/api/review/'+a.call);
 case 'select_report':return m.observe('select_report',{report:a.report});
 case 'select_person':m.navigation('staff',ext(a.manager));return get('/api/person?person='+ext(a.manager));
 default:return m.action(a);
 }
}
async function main(){
 const cases=[],test=async(name,fn)=>{try{await fn();cases.push({name,result:'passed'});}catch(e){cases.push({name,result:'FAILED',error:e.stack});}};
 for(const l of catalog.lessons.filter(l=>l.kind==='practice'))await test('native-local-api-'+l.id,async()=>{
  const m=model(l.role);m.start(l.id);m.navigation(l.guide[0].page);
  const s=m.current(),step=s.step;const bad=m.action({type:l.steps[step].action,client:'client-2',deal:'deal-2',period:'today',manager:'anna',report:'report-other',filter:'all'});assert.equal(bad,false);assert.equal(m.current().step,step);
  for(let n=0;n<30&&!m.current().completed;n++){const before=m.current().step;await perform(m,values(m));assert(m.current().step>before,'Native API action failed at '+l.id+':'+before+' '+m.current().feedback);}
  assert.equal(m.status(l.id),'Пройден');
  if(l.id==='m2'){const tasks=m.data().tasks.filter(t=>t.id===m.current().createdTask);assert.equal(tasks.length,1);assert.equal(tasks[0].original_time,'15:00');assert.equal(tasks[0].due_time,'11:30');assert.equal(tasks[0].note,'Обсудить предложение');}
 });
 await test('new-entry-clears-data-reload-preserves-and-scope-isolated',async()=>{const storage=new Storage(),m=model('manager','first',storage);m.start('m1');await perform(m,values(m));m.draft('work:date','2026-11-05');const id=m.current().id;const reload=model('manager','first',storage);assert.equal(reload.current().id,id);assert.equal(reload.current().step,1);assert.equal(reload.drafts()['work:date'],'2026-11-05');const other=model('manager','first',storage,'another-account');assert.equal(other.current(),null);const fresh=model('manager','next-login',storage);assert.equal(fresh.current(),null);assert.equal(fresh.status('m1'),'Не начат');assert.equal(storage.getItem(m.key),null);assert(storage.getItem(other.key));});
 await test('repeat-resets-only-selected-lesson',async()=>{const m=model();m.start('m1');await perform(m,values(m));const id=m.current().id;m.start('m2');const other=m.current().id;m.start('m1',true);assert.notEqual(m.current().id,id);assert.equal(m.current().step,0);assert.equal(m.current().data.outcomes.length,0);assert.equal(m.state.attempts.m2.id,other);});
 await test('roles-modules-and-unknown-working-routes-fail-closed',async()=>{const m=model('manager','x',new Storage(),'account',[]);assert.throws(()=>m.start('d1'));assert.throws(()=>m.start('m4'));for(const p of ['/api/contracts','/api/contracts/config','/api/person?person=102','/api/review/not-a-fixture','/api/contracts/lookup/inn/0000000000','https://other.invalid/api/me'])await assert.rejects(m.request(p));for(const p of ['/api/dial','/api/gpt','/api/automations','/api/access','/api/robot/call','/api/review/call-1/train','/api/trainings','/api/contracts/register'])await assert.rejects(m.request(p,{method:'POST',body:{}}));assert.equal(network,0);});
 await test('m2-wrong-date-and-double-save-do-not-create-tasks',async()=>{const m=model();m.start('m2');await perform(m,values(m));const a=values(m);await assert.rejects(perform(m,{...a,due_time:'14:00'}));assert.equal(m.data().outcomes.length,0);await perform(m,a);const count=m.data().tasks.length;await assert.rejects(perform(m,a));assert.equal(m.data().tasks.length,count);});
 await test('disabled-modules-and-director-readonly-settings',async()=>{const m=model('director','x',new Storage(),'account',[]);assert.equal(m.lesson('d6').guide.length,3);assert.equal(m.readonly('settings'),true);assert.equal(m.readonly('queue'),false);await assert.rejects(m.request('/api/automations'));await assert.rejects(m.request('/api/robot/cab'));});
 await test('midnight-does-not-change-active-callback-situation',async()=>{const storage=new Storage(),m=model('manager','same',storage);m.start('m2');await perform(m,values(m));const fresh=packet('manager','same');fresh.clock={...clock,today:'2026-11-05',callback:{...clock.callback,due_date:'2026-11-06'}};const resumed=new ctx.AveronClassroom.Classroom(fresh,storage);assert.equal(resumed.current().clock.callback.due_date,'2026-11-05');assert.equal(resumed.current().clock.today,'2026-11-04');await perform(resumed,values(resumed));assert.equal(resumed.data().tasks.find(t=>t.id==='task-created').due_date,'2026-11-05');});
 await test('broken-bootstrap-fails-closed',async()=>{let calls=0;const document={getElementById:()=>({textContent:'not-json'}),addEventListener(){}};const context={URL,Response,location:{pathname:'/learning/workspace',origin:'https://crm.invalid',href:'https://crm.invalid/learning/workspace'},document,fetch(){calls++;}};context.window=context;vm.runInNewContext(read('crm/static/learning/workspace.js'),context);await assert.rejects(context.AveronLearningWorkspace.request('/api/me'));const response=await context.fetch('/api/outcome',{method:'POST',body:'{}'});assert.equal(response.status,403);assert.equal(calls,0);});
 console.log(JSON.stringify({mode:'actual pure local router, no browser',cases,networkCalls:network},null,2));if(cases.some(c=>c.result==='FAILED'))process.exitCode=1;
}
if(require.main===module)main().catch(e=>{console.error(e);process.exitCode=1;});
module.exports={Storage,packet,read,model,clock};
