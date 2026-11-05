/* Floating hints: geometry, dragging, feedback + actual CRM controllers.
   Numeric rectangles below are fixtures; this is NOT a visual/browser test. */
'use strict';
const assert=require('node:assert/strict');
const P=require('../../crm/static/learning/placement.js');
const {model,Storage}=require('./learning_workspace_test.cjs');
const {open,click,fill,start,state,until,complete,expected,perform}=require('./learning_tabs_ui_test.cjs');
const {settle}=require('./dom_adapter.cjs');
function bounds(result,viewport,target,obstacles=[]){assert(!result.blocked);const r=P.rect(result),v=P.rect(viewport);assert(r.left>=v.left&&r.top>=v.top&&r.right<=v.right&&r.bottom<=v.bottom);if(target)assert(!P.overlap(r,P.rect(target),5));for(const b of obstacles)assert(!P.overlap(r,P.rect(b),4));}
const target={left:420,top:120,width:180,height:36},v={left:0,top:0,width:1366,height:900},card={width:300,height:160};
async function main(){const cases=[],test=async(name,fn)=>{try{await fn();cases.push({name,result:'passed'});}catch(e){cases.push({name,result:'FAILED',error:e.stack});}};
 await test('geometry-right-first-on-wide-screen',()=>{const r=P.place({target,card,viewport:v});assert.equal(r.side,'right');assert.equal(r.left,614);bounds(r,v,target);});
 await test('geometry-respects-neighbour-controls',()=>{const obstacles=[{left:610,top:0,width:756,height:900}];const r=P.place({target,card,viewport:v,obstacles});assert.notEqual(r.side,'right');bounds(r,v,target,obstacles);});
 await test('geometry-small-viewport-and-keyboard',()=>{for(const height of [844,420,300]){const viewport={left:0,top:20,width:390,height},field={left:20,top:55,width:350,height:48};const r=P.place({target:field,card,viewport,narrow:true});bounds(r,viewport,field);}});
 await test('geometry-tall-card-and-visual-viewport-offset',()=>{const viewport={left:12,top:80,width:390,height:420},field={left:20,top:90,width:350,height:48};const r=P.place({target:field,card:{width:300,height:260},viewport,narrow:true});bounds(r,viewport,field);});
 await test('geometry-no-anchor-and-offscreen',()=>{for(const field of [null,{left:100,top:-200,width:100,height:40}]){const r=P.place({target:field,card,viewport:v});bounds(r,v);assert(r.detached);}});
 await test('geometry-cannot-cover-full-viewport-field',()=>{const viewport={left:0,top:0,width:390,height:300};assert(P.place({target:viewport,card,viewport,narrow:true}).blocked);});
 await test('auto-hint-has-explanation-no-quiz-no-next-and-one-advance',async()=>{
  const ui=await open();await start(ui);assert(ui.document.querySelector('.learn-explanation').textContent.includes('Очередь')||ui.document.querySelector('.learn-explanation').textContent.includes('клиенты'));assert(!ui.document.querySelector('[data-learn-next],[data-check-answer],[data-answer]'));
  const old=ui.document.querySelector('[data-next] [data-i="1"]');await click(ui,'[data-next] [data-i="1"]');assert.equal(state(ui).step,1);old.dispatch('click');await settle();assert.equal(state(ui).step,1);assert(ui.document.querySelector('[data-learn-heading]').textContent.startsWith('Урок один.'));assert.equal(ui.network.length,0);
 });
 await test('director-filter-floating-right-wrong-choice-and-auto-progress',async()=>{
  const ui=await open('director');await start(ui);await complete(ui);await click(ui,'[data-tab-lesson="calls"]');
  ui.document.rectFor=node=>node.classList.contains('learn-guide')?null:node.matches('[data-ext-pick]')?target:node.classList.contains('banner')?{left:232,top:0,width:1000,height:44}:{left:10,top:10,width:36,height:32};ui.document.body.appendChild(ui.document.createElement('span'));await settle();
  let c=ui.document.querySelector('[data-learning-guide]');assert.equal(c.parentNode,ui.document.body);assert.equal(c.dataset.placement,'right');assert.equal(ui.document.querySelector('[data-learn-feedback]').textContent,'');
  const at=state(ui).step;await fill(ui,'[data-ext-pick]','101','change');assert.equal(state(ui).step,at);assert(ui.document.querySelector('[data-learn-feedback]').textContent.includes('Бориса'));await fill(ui,'[data-ext-pick]','102','change');assert.equal(state(ui).step,at+1);assert.equal(expected(ui).st.action,'set_period');assert(!ui.document.querySelector('[data-learn-next]'));
 });
 await test('pause-and-resume-preserve-current-auto-step',async()=>{const ui=await open();await start(ui);await perform(ui);const at=state(ui).step;await click(ui,'[data-learn-close]');assert(!ui.document.querySelector('[data-learning-guide]'));assert.equal(ui.document.querySelectorAll('.learn-highlight').length,0);await click(ui,'[data-learn-resume]');assert.equal(state(ui).step,at);assert(ui.document.querySelector('[data-learning-guide]'));});
 await test('missing-target-and-late-target-have-recovery',async()=>{const ui=await open();await start(ui);const items=ui.document.querySelector('[data-next]');items.remove();await settle();assert(ui.document.querySelector('[data-missing-target]'));assert.equal(ui.document.querySelectorAll('.learn-highlight').length,0);assert(ui.document.querySelector('[data-learn-catalog]'));assert(ui.document.querySelector('[data-learn-focus]').disabled);ui.document.getElementById('view').appendChild(items);await settle();assert(!ui.document.querySelector('[data-missing-target]'));assert(ui.document.querySelector('.learn-highlight'));assert(!ui.document.querySelector('[data-learn-focus]').disabled);});
 await test('callback-card-protects-fields-wide-and-narrow',async()=>{
  const ui=await open();await start(ui);await perform(ui);await perform(ui);
  ui.document.rectFor=n=>n.classList.contains('learn-guide')?null:n.matches('.outform')?{left:260,top:200,width:440,height:220}:n.matches('[data-f="date"]')?{left:280,top:240,width:180,height:36}:n.matches('[data-f="time"]')?{left:480,top:240,width:180,height:36}:n.matches('[data-act="save"]')?{left:280,top:370,width:210,height:36}:{left:10,top:10,width:36,height:32};await click(ui,'[data-out="callback"]');
  for(const selector of ['[data-f="date"]','[data-f="time"]','[data-act="save"]'])assert(!P.overlap(P.rect(ui.document.querySelector('[data-learning-guide]').getBoundingClientRect()),P.rect(ui.document.querySelector(selector).getBoundingClientRect())));
  ui.globals.innerWidth=390;ui.globals.innerHeight=420;ui.document.rectFor=n=>n.classList.contains('learn-guide')?null:n.matches('.outform')?{left:10,top:60,width:370,height:220}:n.matches('[data-f="date"]')?{left:20,top:100,width:150,height:36}:n.matches('[data-f="time"]')?{left:190,top:100,width:150,height:36}:n.matches('[data-act="save"]')?{left:20,top:230,width:210,height:36}:{left:10,top:10,width:36,height:32};ui.document.body.appendChild(ui.document.createElement('span'));await settle();const c=ui.document.querySelector('[data-learning-guide]'),r=P.rect(c.getBoundingClientRect());assert(!c.hidden);assert(r.left>=0&&r.top>=0&&r.right<=390&&r.bottom<=420);for(const selector of ['[data-f="date"]','[data-f="time"]','[data-act="save"]'])assert(!P.overlap(r,P.rect(ui.document.querySelector(selector).getBoundingClientRect())));
 });
 await test('native-modal-keyboard-focus-includes-floating-controls',async()=>{const ui=await open();await start(ui);await until(ui,()=>expected(ui).st.action==='save_note');const c=ui.document.querySelector('[data-learning-guide]'),last=c.querySelector('[data-learn-focus]'),first=ui.document.querySelector('.rvm-x');last.focus();assert(last.dispatch('keydown',{key:'Tab'}).defaultPrevented);assert.equal(ui.document.activeElement,first);first.focus();assert(first.dispatch('keydown',{key:'Tab',shiftKey:true}).defaultPrevented);assert.equal(ui.document.activeElement,last);assert.equal(ui.network.length,0);});
 await test('manual-placement-clamps-and-protects-target-and-controls',()=>{
  const controls=[{left:950,top:450,width:180,height:48}];let r=P.move({position:{left:740,top:520},card,viewport:v,target,obstacles:controls});bounds(r,v,target,controls);assert.equal(r.left,740);assert.equal(r.top,520);
  r=P.move({position:{left:430,top:125},card,viewport:v,target,obstacles:controls});bounds(r,v,target,controls);
  const small={left:0,top:40,width:390,height:420};r=P.move({position:{left:900,top:900},card,viewport:small});bounds(r,small);assert.equal(r.right,380);assert.equal(r.bottom,450);
 });
 await test('hint-layer-is-above-native-deal-modal-and-backdrop',async()=>{
  const fs=require('node:fs'),path=require('node:path'),root=path.resolve(__dirname,'../..');
  const css=fs.readFileSync(path.join(root,'crm/static/learning/learning.css'),'utf8'),native=fs.readFileSync(path.join(root,'crm/static/review.css'),'utf8');
  const z=Number(/\.learn-guide\{[^}]*z-index:(\d+)/.exec(css)[1]),modal=Number(/\.rvm\{[^}]*z-index:(\d+)/.exec(native)[1]);assert(z>modal);assert(css.includes('touch-action:none'));
  const ui=await open('director');await start(ui);await until(ui,()=>expected(ui).st.action==='close_deal');assert(ui.document.querySelector('.rvm'));assert.equal(ui.document.querySelector('[data-learning-guide]').parentNode,ui.document.body);assert(ui.document.querySelector('.learn-purpose').textContent.includes('Причина')||ui.document.querySelector('.learn-purpose').textContent.includes('причину'));assert.equal(ui.network.length,0);
 });
 await test('pointer-drag-touch-cancel-reset-and-keyboard-do-not-grade',async()=>{
  const ui=await open();ui.document.rectFor=n=>n.classList.contains('learn-guide')?null:n.matches('[data-next] .item')?target:{left:10,top:10,width:36,height:32};await start(ui);
  let c=ui.document.querySelector('[data-learning-guide]'),h=c.querySelector('[data-learn-drag]'),r=c.getBoundingClientRect();const at=state(ui).step;
  h.dispatch('pointerdown',{pointerId:1,button:0,isPrimary:true,clientX:r.left+10,clientY:r.top+10});ui.document.body.dispatch('pointermove',{pointerId:1,clientX:790,clientY:550});ui.document.body.dispatch('pointerup',{pointerId:1,clientX:790,clientY:550});await settle();r=c.getBoundingClientRect();assert.equal(r.left,780);assert.equal(r.top,540);assert.equal(state(ui).step,at);assert(ui.model.drafts().$hintPosition.includes('780'));
  h.dispatch('pointerdown',{pointerId:2,pointerType:'touch',isPrimary:true,clientX:r.left+5,clientY:r.top+5});ui.document.body.dispatch('pointermove',{pointerId:2,clientX:910,clientY:650});ui.document.body.dispatch('pointercancel',{pointerId:2});await settle();assert.equal(c.getBoundingClientRect().left,r.left);assert.equal(c.getBoundingClientRect().top,r.top);
  h.dispatch('keydown',{key:'ArrowDown'});await settle();assert.equal(c.getBoundingClientRect().top,550);h.dispatch('keydown',{key:'Home'});await settle();assert.equal(ui.model.drafts().$hintPosition,'');assert.equal(c.dataset.placement,'right');
  const old=c.getBoundingClientRect();h.dispatch('pointerdown',{pointerId:4,button:2,clientX:old.left,clientY:old.top});ui.document.body.dispatch('pointermove',{pointerId:4,clientX:900,clientY:600});assert.equal(c.getBoundingClientRect().left,old.left);assert.equal(state(ui).step,at);assert.equal(ui.network.length,0);
 });
 await test('manual-position-persists-resumes-and-stays-in-smaller-viewport',async()=>{
  let ui=await open();const rectangles=n=>n.classList.contains('learn-guide')?null:n.matches('[data-next] .item')?target:{left:10,top:10,width:36,height:32};ui.document.rectFor=rectangles;await start(ui);
  let c=ui.document.querySelector('[data-learning-guide]'),h=c.querySelector('[data-learn-drag]'),r=c.getBoundingClientRect();h.dispatch('pointerdown',{pointerId:7,button:0,clientX:r.left,clientY:r.top});ui.document.body.dispatch('pointerup',{pointerId:7,clientX:780,clientY:540});await settle();const saved=ui.model.drafts().$hintPosition;await click(ui,'[data-learn-close]');
  ui=await open('manager','same-entry',ui.storage,undefined,'#queue');ui.document.rectFor=rectangles;await click(ui,'[data-learn-resume]');assert.equal(ui.model.drafts().$hintPosition,saved);c=ui.document.querySelector('[data-learning-guide]');assert.equal(c.getBoundingClientRect().left,780);
  ui.globals.innerWidth=390;ui.globals.innerHeight=420;ui.document.body.appendChild(ui.document.createElement('span'));await settle();r=c.getBoundingClientRect();assert(r.left>=0&&r.top>=0&&r.right<=390&&r.bottom<=420);assert.equal(state(ui).step,0);
 });
 await test('automatic-gif-can-be-paused-without-touching-exercise',async()=>{
  const ui=await open();ui.globals.matchMedia=()=>({matches:false});await start(ui);let image=ui.document.querySelector('[data-example-image]');assert(image.src.endsWith('/select_client.gif'));assert.equal(image.dataset.playing,'true');const at=state(ui).step;await click(ui,'[data-example-toggle]');image=ui.document.querySelector('[data-example-image]');assert(image.src.endsWith('.png'));await click(ui,'[data-example-toggle]');assert(image.src.endsWith('.gif'));assert.equal(state(ui).step,at);assert.equal(ui.network.length,0);
 });
 await test('success-tick-holds-1200ms-and-blocks-repeat-writes',async()=>{
  const ui=await open();await start(ui);const timers=[],normal=ui.globals.setTimeout;ui.globals.setTimeout=(fn,ms)=>{if(ms===1200){timers.push(fn);return 0;}return normal(fn,ms);};
  await click(ui,'[data-next] [data-i="0"]');assert(!ui.document.querySelector('[data-learning-success]'));assert.equal(timers.length,0);
  await click(ui,'[data-next] [data-i="1"]');assert.equal(state(ui).step,1);assert(ui.model.presentationBusy);assert(ui.document.querySelector('[data-learning-success]').textContent.includes('Выполнено'));assert.equal(ui.document.querySelector('[data-learning-guide]').dataset.learnStep,'0');assert.equal(timers.length,1);
  await assert.rejects(ui.globals.AveronLearningWorkspace.request('/api/outcome',{method:'POST',body:{number:'00000000001',outcome:'no_answer'}}));assert.equal(state(ui).data.outcomes.length,0);timers.shift()();await settle();assert(!ui.model.presentationBusy);assert(!ui.document.querySelector('[data-learning-success]'));assert.equal(ui.document.querySelector('[data-learning-guide]').dataset.learnStep,'1');
  await click(ui,'[data-out="no_answer"]');assert(ui.document.querySelector('[data-learning-success]').textContent.includes('сохранён'));assert.equal(state(ui).data.outcomes.length,1);await click(ui,'[data-learn-close]');assert(!ui.model.presentationBusy);assert(ui.model.state.paused);assert.equal(state(ui).data.outcomes.length,1);assert.equal(ui.network.length,0);
 });
 console.log(JSON.stringify({mode:'geometry fixtures and real controller DOM model; not browser screenshots',cases},null,2));if(cases.some(c=>c.result==='FAILED'))process.exitCode=1;
}
main().catch(e=>{console.error(e);process.exitCode=1;});
