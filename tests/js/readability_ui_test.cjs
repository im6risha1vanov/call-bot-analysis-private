'use strict';
const assert=require('node:assert/strict'),{settle}=require('./dom_adapter.cjs');
const {open,click,fill,start,state,until,complete,expected,perform}=require('./learning_tabs_ui_test.cjs');
const {Storage,read,clock}=require('./learning_workspace_test.cjs');
const {working}=require('./readability_fixture.cjs');
const T=require('../../crm/static/presentation.js');
async function main(){const cases=[],coverage=[],test=async(name,fn)=>{try{await fn();cases.push({name,result:'passed'});}catch(e){cases.push({name,result:'FAILED',error:e.stack});}};
 await test('dates-labels-and-errors-are-presentation-only',()=>{
  const input='2026-11-06';assert.equal(T.date(input),'6 ноября');assert.equal(input,'2026-11-06');assert.equal(T.date('2026-02-30'),'Дата не указана');assert.equal(T.date('2027-01-01',{year:true}),'1 января 2027');
  assert.equal(T.date('2026-11-06',{today:'2026-11-05',relative:true}),'завтра');assert.equal(T.date('2026-11-06',{today:'2026-11-06',relative:true}),'сегодня');assert.equal(T.date('2026-11-06',{today:'2026-11-06'}),'6 ноября');
  assert.equal(T.zone('Europe/Moscow'),'по Москве');assert.equal(T.zone('Asia/Yekaterinburg'),'по Екатеринбургу');assert.equal(T.label('callback',{callback:'Перезвонить'}),'Перезвонить');assert.equal(T.label('raw_server_state',{},'Статус не указан'),'Статус не указан');
  for(const raw of ['Traceback SELECT phone FROM clients', '{"error":"пароль"}','asyncpg.InvalidTextRepresentation: клиент 123','HTTP 500','Ошибка /opt/callbot-astra/test.py:12'])assert(!T.error(raw).includes(raw));assert(T.error('Укажите дату в поле «Дата».').includes('«Дата»'));
 });
 for(const role of ['manager','director'])await test(role+'-working-callback-preserves-input-payload-schedule-and-history',async()=>{
  const backend=working(role),ui=await open(role,'working',new Storage(),undefined,'#queue',false,{backend});
  await click(ui,'[data-next] [data-i="1"]');await click(ui,'[data-out="callback"]');await fill(ui,'[data-f="date"]','2026-11-06');await fill(ui,'[data-f="time"]','15:00');await fill(ui,'[data-f="comment"]','Обсудить предложение');
  await click(ui,'[data-page-help]');assert(ui.document.querySelector('.page-help').textContent.includes('Итог звонка'));assert.equal(ui.document.querySelector('[data-f="date"]').value,'2026-11-06');await click(ui,'[data-page-help-close]');assert.equal(ui.document.querySelector('[data-f="time"]').value,'15:00');await click(ui,'[data-act="save"]');
  const saved=backend.events[0];assert.equal(saved.path,'/api/outcome');assert.equal(saved.body.due_date,'2026-11-06');assert.equal(saved.body.due_time,'15:00');assert.equal(saved.body.outcome,'callback');assert.equal(saved.body.comment,'Обсудить предложение');
  await ui.run('go("callbacks")');await settle();assert(ui.document.querySelector('.time-caption').textContent.includes('по Москве'));assert(ui.document.querySelector('.cb[data-id="working-task"]').textContent.includes('6 ноября'));
  await click(ui,'.cb[data-id="working-task"] [data-move]');await fill(ui,'.cb[data-id="working-task"] .move input[type="date"]','2026-11-07');await fill(ui,'.cb[data-id="working-task"] .move input[type="time"]','11:30');await click(ui,'.cb[data-id="working-task"] [data-save]');assert.equal(backend.events[1].body.due_date,'2026-11-07');assert.equal(backend.events[1].body.due_time,'11:30');
  const task=backend.base.base.tasks.find(t=>t.id==='working-task');assert.equal(task.original_date,'2026-11-06');assert.equal(task.original_time,'15:00');assert.equal(task.note,'Обсудить предложение');assert.equal(backend.base.base.tasks.filter(t=>t.id==='working-task').length,1);
 });
 for(const role of ['manager','director'])await test(role+'-lesson-help-preserves-progress-drafts-gif-and-pause',async()=>{
  const ui=await open(role);await start(ui);if(role==='manager'){await perform(ui);await perform(ui);await click(ui,'[data-out="callback"]');await fill(ui,'[data-f="date"]',clock.callback.due_date);await fill(ui,'[data-f="time"]','15:00');await fill(ui,'[data-f="comment"]','Не потерять введённый текст');}
  const a=state(ui),at=a.step,id=a.id,gif=ui.document.querySelector('[data-example-image]'),source=gif.src;
  await click(ui,'[data-page-help]');assert(ui.document.querySelector('.page-help'));assert.equal(state(ui).id,id);assert.equal(state(ui).step,at);assert.equal(ui.document.querySelector('[data-example-image]'),gif);assert.equal(gif.src,source);
  await click(ui,'[data-page-help-close]');assert.equal(state(ui).step,at);if(role==='manager')assert.equal(ui.document.querySelector('[data-f="comment"]').value,'Не потерять введённый текст');await click(ui,'[data-example-toggle]');assert.notEqual(gif.src,source);assert.equal(state(ui).step,at);assert.equal(ui.network.length,0);
  assert(!ui.document.querySelector('[data-learning-guide]').textContent.includes('Europe/'));assert(!/\d{4}-\d{2}-\d{2}/.test(ui.document.querySelector('[data-learning-guide]').textContent));
 });
 for(const role of ['manager','director'])await test(role+'-working-sections-help-cards-tables-empty-and-errors',async()=>{
  const backend=working(role),ui=await open(role,'working',new Storage(),undefined,role==='manager'?'#queue':'#summary',false,{backend});
  const pages=role==='manager'?['queue','callbacks','deals','calls','me','callcenter','sources','reports','learning']:['summary','calls','callbacks','deals','staff','callcenter','sources','reports','auto','robot','settings','learning'];
  for(const page of pages){await ui.run('go('+JSON.stringify(page)+')');await settle();assert(!ui.document.querySelector('[data-ui-error]'),page+': '+ui.document.querySelector('#view').textContent);assert(ui.document.querySelector('[data-page-help]'),page);await click(ui,'[data-page-help]');assert(ui.document.querySelector('.page-help h2').textContent.includes(ui.run('PAGES[S.page].name')),page);assert(ui.document.querySelectorAll('.page-help section').length>=1);coverage.push({role,page,mode:'working UI with fictional API',help:true,table:!!ui.document.querySelector('table'),empty:!!ui.document.querySelector('.empty')});await click(ui,'[data-page-help-close]');}
  await ui.run('go("deals","contracts")');await settle();assert(!ui.document.querySelector('[data-ui-error]'));await click(ui,'[data-page-help]');assert(ui.document.querySelector('.page-help').textContent.includes('Журнал документов'));await click(ui,'[data-page-help-close]');
  await ui.run('go("settings","access")');await settle();if(role==='manager')assert.equal(ui.run('S.page'),'queue');
  // Unknown technical server responses never become visible error text.
  ui.globals.fetch=async()=>new Response(JSON.stringify({error:'asyncpg.InvalidDatetimeFormat: 2026-11-06 SQLSTATE 22007'}),{status:500});
  await assert.rejects(ui.run('api("/api/tasks/1",{method:"POST",body:{due_date:"2026-11-06",due_time:"15:00"}})'),e=>e.message.includes('перезвон')&&!e.message.includes('asyncpg'));
  await click(ui,'[data-page-help]');ui.globals.fetch=async()=>new Response(JSON.stringify({error:'HTTP auth_session expired'}),{status:401});await assert.rejects(ui.run('api("/api/me")'),e=>e.message.includes('Время входа истекло'));assert(!ui.document.querySelector('.page-help'));assert(ui.document.querySelector('[name="username"]'));
  assert.equal(backend.events.length,0);assert.equal(backend.failures.length,0,JSON.stringify(backend.failures));assert.equal(ui.errors.length,0,ui.errors.join(';'));
 });
 await test('help-and-native-modal-share-keyboard-focus-without-grading',async()=>{
  const ui=await open();await start(ui);await until(ui,()=>expected(ui).st.action==='select_deal');await click(ui,'[data-page-help]');await perform(ui);const at=state(ui).step,last=ui.document.querySelectorAll('.page-help button').at(-1),first=ui.document.querySelector('.rvm-x');last.focus();const e=last.dispatch('keydown',{key:'Tab'});assert(e.defaultPrevented);assert.equal(ui.document.activeElement,first);assert.equal(state(ui).step,at);await click(ui,'[data-page-help-close]');assert.equal(state(ui).step,at);assert.equal(ui.network.length,0);
 });
 await test('fourth-step-example-demonstrates-the-real-callback-fields-without-filling-them',async()=>{
  const ui=await open();await start(ui);await perform(ui);await perform(ui);
  assert.equal(expected(ui).st.action,'save_outcome');assert.equal(expected(ui).a.outcome,'callback');
  assert(ui.document.querySelector('.learn-step').textContent.includes('Шаг 4'));
  const image=ui.document.querySelector('[data-example-image]');assert(image.closest('[data-example]').dataset.example==='save_outcome_callback');assert.equal(image.getAttribute('height'),'360');
  assert(ui.document.querySelector('.learn-example-note').textContent.includes('свои допустимые'));
  const at=state(ui).step;await click(ui,'[data-example-toggle]');await click(ui,'[data-example-toggle]');assert.equal(state(ui).step,at);assert(!state(ui).createdTask);
  await click(ui,'[data-out="callback"]');for(const field of ['date','time','comment'])assert(ui.document.querySelector('[data-f="'+field+'"]'));
  assert.equal(ui.document.querySelector('[data-f="comment"]').value,'');assert.equal(ui.network.length,0);
 });
 await test('director-examples-distinguish-weekly-report-and-employee-card',async()=>{
  const ui=await open('director');await start(ui);assert.equal(ui.document.querySelector('[data-example]').dataset.example,'set_period_summary');await perform(ui);assert.equal(ui.document.querySelector('[data-example]').dataset.example,'set_manager_summary');await until(ui,()=>expected(ui).st.action==='select_report');
  assert.equal(ui.document.querySelector('[data-example]').dataset.example,'select_report_weekly');await perform(ui);
  await until(ui,()=>expected(ui).st.action==='select_call'&&expected(ui).st.expected.person);
  assert.equal(ui.document.querySelector('[data-example]').dataset.example,'select_call_staff');assert.equal(ui.network.length,0);
 });
 for(const role of ['manager','director'])await test(role+'-help-media-is-scoped-independent-and-keeps-lesson-drafts',async()=>{
  const ui=await open(role);await start(ui);
  if(role==='manager'){await perform(ui);await perform(ui);await click(ui,'[data-out="callback"]');await fill(ui,'[data-f="comment"]','Сохранить мой черновик');}
  const before=JSON.stringify(ui.model.state),image=ui.document.querySelector('[data-example-image]'),source=image.src;
  ui.globals.matchMedia=()=>({matches:false});await click(ui,'[data-page-help]');
  const figures=ui.document.querySelectorAll('[data-help-example]');assert(figures.length>=2);
  const first=figures[0],second=figures[1],selector='[data-help-example="'+first.dataset.helpExample+'"]';
  const otherSource=second.querySelector('[data-help-example-image]').src,helpImage=first.querySelector('[data-help-example-image]');assert(helpImage.src.endsWith('.gif'));
  await click(ui,selector+' [data-help-example-toggle]');assert(helpImage.src.endsWith('.png'));assert.equal(first.querySelector('button').getAttribute('aria-pressed'),'false');assert.equal(second.querySelector('img').src,otherSource);
  await click(ui,selector+' [data-help-example-toggle]');assert(helpImage.src.endsWith('.gif'));
  assert.equal(ui.document.querySelector('[data-example-image]'),image);assert.equal(image.src,source);assert.equal(JSON.stringify(ui.model.state),before);
  await click(ui,'[data-page-help-close]');assert.equal(JSON.stringify(ui.model.state),before);if(role==='manager')assert.equal(ui.document.querySelector('[data-f="comment"]').value,'Сохранить мой черновик');assert.equal(ui.network.length,0);
 });
 for(const role of ['manager','director'])await test(role+'-help-examples-follow-tab-role-and-reduced-motion-in-working-ui',async()=>{
  const backend=working(role),ui=await open(role,'help-media',new Storage(),undefined,role==='manager'?'#queue':'#summary',false,{backend});
  const examples=JSON.parse(read('crm/learning_examples.json'));
  const expectedKeys={queue:['select_client','save_outcome_no_answer','save_outcome_callback'],callbacks:['move_task','select_task_context'],deals:['select_deal','save_note','filter_deals'],calls:role==='director'?['set_manager','set_period','select_call']:['set_period','select_call'],reports:[role==='director'?'select_report_weekly':'select_report'],summary:['set_period_summary','set_manager_summary'],staff:['select_person','select_call_staff'],settings:['visit_access'],auto:['visit_auto'],robot:['visit_robot'],learning:['visit_learning','save_outcome_callback']};
  for(const page of Object.keys(expectedKeys).filter(page=>role==='director'||!['summary','staff','settings','auto','robot'].includes(page))){
   await ui.run('go('+JSON.stringify(page)+')');await settle();const count=ui.network.length;await click(ui,'[data-page-help]');
   const figures=ui.document.querySelectorAll('[data-help-example]');assert.deepEqual(figures.map(n=>n.dataset.helpExample),expectedKeys[page],page);
   for(const figure of figures){const key=figure.dataset.helpExample;assert(examples[key],key);const image=figure.querySelector('img');assert.equal(image.src,'/static/learning/examples/'+examples[key].poster);assert.equal(image.getAttribute('loading'),'lazy');assert.equal(figure.querySelector('button').getAttribute('type'),'button');}
   assert.equal(ui.network.length,count);await click(ui,'[data-page-help-close]');
  }
  await ui.run('go("deals","contracts")');await settle();await click(ui,'[data-page-help]');assert.deepEqual(ui.document.querySelectorAll('[data-help-example]').map(n=>n.dataset.helpExample),['compose','prepare_contract','select_document']);assert(ui.document.querySelector('.page-help').textContent.includes('учебном журнале'));
  assert.equal(backend.events.length,0);assert.equal(backend.failures.length,0);assert.equal(ui.errors.length,0);
 });
 await test('guided-note-is-free-text-and-keeps-what-the-user-entered',async()=>{
  const ui=await open();await start(ui);await until(ui,()=>expected(ui).st.action==='save_note');
  await click(ui,'[data-ev="note"]');const note='Уточнить подходящий объём в следующем разговоре';const before=state(ui).step;await fill(ui,'[data-v="note"]',note);await click(ui,'.rvm [data-save]');assert(state(ui).step>before);assert.equal(state(ui).data.deals[0].history.at(-1).text,note);await complete(ui);assert(ui.model.courseDone());
 });
 console.log(JSON.stringify({mode:'pure presentation + actual native CRM controllers with fictional APIs; not browser',cases,coverage},null,2));if(cases.some(c=>c.result==='FAILED'))process.exitCode=1;
}
main().catch(e=>{console.error(e);process.exitCode=1;});
