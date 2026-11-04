/* Lessons guide real CRM views. In the classroom the same forms use local APIs.
   Outside it, the catalog offers an explicit entry; no production action is graded. */
(function () {
  'use strict';
  const W=window.AveronLearningWorkspace?.classroom, restoredInputs=new WeakSet();
  let context=null, panel=null, highlights=[], scheduled=false, signature='', verification='', restorePlanned=true;
  const lesson=()=>W?.lesson(W.state.active);
  const attempt=()=>W?.current();
  function clearHighlights(){for(const n of highlights)n.classList.remove('learn-highlight');highlights=[];}
  function close(){if(W){W.draft('$filters',JSON.stringify(S.f));W.pause();}panel?.remove();panel=null;signature='';clearHighlights();}
  function contextHint(){return 'Учебные примеры открываются в обычных вкладках CRM. Новый вход очищает данные и прогресс, обновление страницы сохраняет попытку.';}
  function resetFilters(){S.f={period:'today',ext:''};S.qnum=null;S.personTab='over';S.repFilter='all';S.calls={result:'',kind:'',q:''};S.deal={show:'active',days:'90',stage:0};}
  async function launch(id=null){
    if(!W)return;
    const replay=id&&!id.startsWith('programme_')&&!id.startsWith('replay_');
    const fresh=replay||!W.state.attempts[W.programme?.id];
    const tab=replay?W.startTab(id):W.startCourse();restorePlanned=true;
    if(!fresh&&W.drafts().$filters){try{S.f=JSON.parse(W.drafts().$filters);}catch(e){resetFilters();}}else resetFilters();
    const a=attempt();
    if(a?.ui.selectedClient)S.qnum=AveronClassroom.number(a.ui.selectedClient);
    if(replay){S.f.period=a.ui.period;S.f.ext=a.ui.manager?AveronClassroom.ext(a.ui.manager):'';S.deal.show=a.ui.dealFilter||'active';}
    const last=fresh?null:W.drafts()['$page'];
    await go(last||tab.page,last?W.drafts()['$arg']||null:tab.arg||null);schedule();
  }
  async function resumeProgramme(){
    if(!W.inProgramme()||!W.state.active?.startsWith('replay_'))return launch();
    W.state.paused=false;W.save();restorePlanned=true;
    const t=W.tab(),d=W.drafts();await go(d.$page||t.page,d.$arg||t.arg||null);schedule();
  }
  async function restoreSelection(){
    const a=attempt();if(W.showing()||!a||a.completed)return;
    const pending=lesson().steps[W.visibleStep()];
    // This opens a card, never saves or clicks an operational button.
    if(S.page==='deals'&&a.ui.selectedDeal&&['save_note','verify_history','close_deal'].includes(pending.action)) {
      const phone=AveronClassroom.number(a.data.deals.find(d=>d.id===a.ui.selectedDeal).client);
      await dealCard(phone);
      if(pending.action==='save_note'&&W.drafts().$noteOpened==='true') {
        const box=$$('.rvm').at(-1),d=await W.request('/api/deals/'+phone);
        if(box)dealForm(box,d,'note',()=>reload());
      }
    }
    if(S.page==='callbacks'&&pending.action==='move_task'&&W.drafts().$moving===a.ui.selectedTask) {
      const row=$('.cb[data-id="'+a.ui.selectedTask+'"]'),task=a.data.tasks.find(t=>t.id===a.ui.selectedTask);
      if(row&&task&&!$('.move',row))callbackMove(row,W.task(task),a.clock.today);
    }
    if(S.page==='callbacks'&&pending.action==='close_card'&&a.ui.selectedTask) {
      const task=a.data.tasks.find(t=>t.id===a.ui.selectedTask);if(task)workDrawer(W.task(task),{ext:AveronClassroom.ext(task.manager)});
    }
    if(['calls','staff'].includes(S.page)&&a.ui.selectedCall&&pending.action==='close_review')CRMReview.open(a.ui.selectedCall);
  }
  VIEWS.learning=async(view,arg)=>{
    context=W?W.context:await api('/api/learning/context');
    if(!view.isConnected)return;setHead({title:'Обучение'});
    if(!W){view.innerHTML='<div class="page"><h2>Обучение по вкладкам</h2><p>Пошагово познакомьтесь с вкладками своей программы на учебных примерах.</p><a class="btn primary" href="/learning/enter">Войти в учебную учётку</a></div>';return;}
    if(!W.programme){view.innerHTML='<div class="page">Обновите тестовый стенд, чтобы загрузить программу по вкладкам.</div>';return;}
    // A direct address cannot unlock individual lessons or another role.
    if(arg&&W.courseDone()&&W.programme.tabs.some(t=>t.id===arg)){await launch(arg);return;}
    const done=W.courseDone(),a=W.state.attempts[W.programme.id],tabs=W.programme.tabs;
    const count=done?tabs.length:tabs.filter(t=>a&&a.step>=t.end).length;
    view.innerHTML=`<div class="page"><h2>Программа ${context.role==='director'?'директора':'менеджера'}</h2><p class="hint">${esc(contextHint())}</p><p role="status" data-learning-program>${done?'Обучение пройдено. Можно повторить отдельную вкладку.':`Пройдено вкладок: ${count} из ${tabs.length}.`}</p>${done?`${W.inProgramme()&&!W.finished()&&W.state.paused?'<button class="btn" data-resume-tab>Продолжить урок</button>':''}<div class="learn-catalog">${tabs.map(t=>`<section class="card"><h2>${esc(t.label)}</h2><p>${esc(t.description)}</p><button class="btn primary" data-tab-lesson="${t.id}">Повторить урок</button></section>`).join('')}</div>`:`<button class="btn primary" data-start-programme>${a?'Продолжить обучение':'Пройти обучение'}</button><p class="hint">${esc(tabs.map(t=>t.title).join(' → '))}</p>`}</div>`;
    $('[data-start-programme]',view)?.addEventListener('click',()=>launch().catch(e=>toast(e.message,true)));
    $('[data-resume-tab]',view)?.addEventListener('click',()=>resumeProgramme().catch(e=>toast(e.message,true)));
    $$('[data-tab-lesson]',view).forEach(b=>b.onclick=()=>launch(b.dataset.tabLesson).catch(e=>toast(e.message,true)));
  };
  let anchor=null, resume=null, frame=null, waitingSince=0, waitTimer=null, waitingKey='';
  const observed = typeof ResizeObserver==='function' ? new ResizeObserver(()=>queuePosition()) : null;
  const visibleStep=()=>W?.visibleStep();
  function activeStep(){const l=lesson();return l?.steps[visibleStep()];}
  function showingDone(){return W.finished();}
  function targetNodes(step){
    let selector=step?.selector || (step?.target?'[data-learning-target="'+step.target+'"]':'');
    if(!selector)return[];
    const scope=$$('.rvm').at(-1)||($('#drawer')?.classList.contains('open')?$('#drawer'):document);
    const shared=window.tourFind ? tourFind(selector) : null;
    return $$(selector,scope).filter(n=>!n.closest('.hidden')&&n.isConnected&&(!n.checkVisibility||n.checkVisibility())&&(n===shared||(n.getBoundingClientRect().width>0&&n.getBoundingClientRect().height>0)));
  }
  function anchorsFor(step){
    if(!step)return[];
    if(!W.showing()&&step.action==='save_outcome') {
      const form=targetNodes({selector:'.outform:not(.hidden)'});
      if(form.length)return form;
      return targetNodes({selector:'[data-out="'+step.expected.outcome+'"]'});
    }
    if(!W.showing()&&step.action==='move_task') {
      const moving=targetNodes({selector:'.cb .move'});if(moving.length)return moving;
    }

    const screen=step.screen;
    if(screen?.selector?.includes('#nav')){
      const direct=$$('[data-go="'+step.expected.page+'"]').filter(usable);
      return direct.length?direct:$$('#nav .more,#tabbar [data-sheet]').filter(usable);
    }
    return targetNodes(screen);
  }
  function viewport(){const v=window.visualViewport;return {left:v?.offsetLeft||0,top:v?.offsetTop||0,width:v?.width||innerWidth,height:v?.height||window.innerHeight||768};}
  function usable(n){const r=n.getBoundingClientRect();return n.isConnected&&!n.closest('.hidden')&&(!n.checkVisibility||n.checkVisibility())&&r.width>0&&r.height>0;}
  function obstaclesFor(card){
    const nodes=$$('input,select,textarea,button,a[href],[contenteditable="true"],.banner,.gpt.open');
    const result=nodes.filter(n=>!card.contains(n)&&!n.closest('.learn-resume')&&n!==anchor&&!anchor?.contains(n)&&usable(n)).map(n=>n.getBoundingClientRect());
    const focused=document.activeElement;
    // Native select option lists are not DOM children: reserve their opening area.
    if(focused?.tagName.toLowerCase()==='select'&&!card.contains(focused)&&usable(focused)) {
      const r=focused.getBoundingClientRect(),v=viewport();
      result.push({left:r.left,top:r.bottom,width:r.width,height:Math.min(300,Math.max(0,v.top+v.height-r.bottom),Math.max(3,focused.options?.length||5)*30)});
      const above=Math.min(300,Math.max(0,r.top-v.top),Math.max(3,focused.options?.length||5)*30);
      result.push({left:r.left,top:r.top-above,width:r.width,height:above});
    }
    return result;
  }
  function queuePosition(){if(frame!==null)return;frame=true;requestAnimationFrame(()=>{frame=null;position();});}
  function position(){
    const card=panel||resume;if(!card?.isConnected)return;
    const v=viewport();
    card.hidden=false;card.style.visibility='hidden';card.dataset.compact='false';
    card.style.width=Math.min(panel?300:290,Math.max(1,v.width-20))+'px';
    card.style.maxHeight=Math.max(44,v.height-20)+'px';
    const r=card.getBoundingClientRect(),target=anchor&&usable(anchor)?anchor.getBoundingClientRect():null;
    const result=AveronLearningPlacement.place({target,card:{width:r.width||300,height:r.height||160},viewport:v,obstacles:obstaclesFor(card),narrow:v.width<680});
    if(result.blocked){
      // While the keyboard or another popup fills the available area, use a tiny
      // recoverable control. Never put an opaque box over the protected field.
      card.dataset.compact='true';
      const small=AveronLearningPlacement.place({target,card:{width:Math.min(220,v.width-20),height:44},viewport:v,obstacles:obstaclesFor(card),narrow:true});
      if(small.blocked){card.hidden=true;return;}
      card.style.width=small.width+'px';applyPosition(card,small);
    }else{card.dataset.compact='false';applyPosition(card,result);}
    if(anchor&&target){
      const inView=AveronLearningPlacement.overlap(AveronLearningPlacement.rect(target),AveronLearningPlacement.rect(v));
      anchor.classList.toggle('learn-highlight',inView);
    }
  }
  function applyPosition(card,result){
    card.hidden=false;card.style.visibility='';card.style.left=result.left+'px';card.style.top=result.top+'px';
    card.style.setProperty('--arrow-x',result.arrowX+'px');card.style.setProperty('--arrow-y',result.arrowY+'px');
    card.dataset.placement=result.side;card.dataset.detached=String(result.detached);
  }
  function schedule(){if(scheduled)return;scheduled=true;queueMicrotask(()=>{scheduled=false;draw();});}
  function renderResume(){
    panel?.remove();panel=null;signature='';clearHighlights();anchor=null;observed?.disconnect();
    if(!W||!W.inProgramme()||W.finished()||$('.tour')||S.page==='learning'){resume?.remove();resume=null;return;}
    if(!resume){
      resume=document.createElement('aside');resume.className='learn-resume';resume.setAttribute('aria-label','Урок на паузе');
      resume.innerHTML='<span>Урок на паузе</span><button class="btn small" data-learn-resume>Продолжить</button><button class="ghost" data-resume-program>К программе</button>';
      document.body.appendChild(resume);
      $('[data-learn-resume]',resume).onclick=()=>resumeProgramme().catch(e=>toast(e.message,true));
      $('[data-resume-program]',resume).onclick=()=>go('learning');
    }
    queuePosition();
  }
  function draw(){
    if(!W||!W.inProgramme()){panel?.remove();panel=null;resume?.remove();resume=null;clearHighlights();return;}
    if(W.state.paused){renderResume();return;}
    resume?.remove();resume=null;
    // Preserve the existing optional AveronGPT tour without stacking two tours.
    if($('.tour')){panel?.remove();panel=null;clearHighlights();return;}
    restoreFields();
    const l=lesson(),a=attempt(),done=showingDone();
    if(done&&W.state.active.startsWith('replay_')&&S.page!=='learning'){go('learning');return;}
    if(done&&S.page==='learning'){panel?.remove();panel=null;clearHighlights();return;}
    const index=visibleStep(),step=done?null:activeStep();
    const visible=done?[]:anchorsFor(step);
    const previousAnchor=anchor;anchor=visible[0]||null;
    if(!panel){
      panel=document.createElement('section');panel.className='card learn-guide';panel.setAttribute('data-learning-guide','');
      panel.setAttribute('role','region');panel.setAttribute('aria-label','Подсказка урока');
      document.body.appendChild(panel);observed?.observe(panel);
    }
    const waitKey=[l.id,a.id,index].join(':');
    if(previousAnchor!==anchor||waitingKey!==waitKey){
      waitingKey=waitKey;
      clearHighlights();observed?.disconnect();observed?.observe(panel);if(anchor)observed?.observe(anchor);
      waitingSince=Date.now();clearTimeout(waitTimer);
      if(!anchor&&!done)waitTimer=setTimeout(schedule,1400);
    }
    const waiting=!anchor&&!done&&Date.now()-waitingSince<1400;
    const sig=[l.id,a.id,index,done,!!anchor,anchor?.dataset.go||anchor?.className,waiting,S.page,S.arg].join(':');
    if(sig!==signature){
      signature=sig;
      const tab=W.tab(),title=tab.label,count=tab.end-tab.start;
      panel.dataset.learnStep=String(index);panel.dataset.mode='practice';
      const viaMore=anchor?.matches('.more,[data-sheet]')&&step?.action==='visit_page';
      const destination=step&&W.programme.tabs.find(t=>t.page===step.expected.page)?.title||'Обучение';
      const raw=done?'Урок пройден. Выберите следующую вкладку в программе.':viaMore?'Откройте «Ещё», затем «'+destination+'».':(step.hint||step.title);
      const explain=done?'Можно повторить отдельный урок с чистой учебной ситуацией.':AveronLearningEngine.interpolate(step.explain||'',a);
      const text=AveronLearningEngine.interpolate(raw,a);
      panel.innerHTML=`<header class="learn-head"><div><b id="learn-heading" tabindex="-1" data-learn-heading>${esc(title)}</b><span class="learn-step">${done?'Урок пройден':`Шаг ${index-tab.start+1} из ${count}`}</span></div><button class="learn-close" type="button" aria-label="Приостановить урок" data-learn-close>×</button></header><div class="learn-body"><p class="learn-instruction">${esc(text)}</p><p class="learn-explanation">${esc(explain)}</p><p class="learn-status" role="status" data-learn-feedback></p>${!anchor&&!done?`<p class="learn-missing" data-missing-target>${waiting?'Ожидаю появления элемента…':'Элемент не найден. Откройте нужную вкладку или вернитесь к программе.'}</p>`:''}</div><footer class="learn-actions"><button class="ghost" type="button" data-learn-catalog>К программе</button>${anchor?'<button class="ghost" type="button" data-learn-focus aria-label="Перейти к выделенному элементу">К элементу</button>':''}</footer>`;
      $('[data-learn-close]',panel).onclick=close;
      $('[data-learn-catalog]',panel).onclick=()=>{
        if(step?.action==='visit_page'&&step.expected.page==='learning')go('learning');else{close();go('learning');}
      };
      $('[data-learn-focus]',panel)?.addEventListener('click',()=>{anchor?.scrollIntoView({block:'center',behavior:reduce?'auto':'smooth'});if(anchor?.matches('input,select,textarea,button'))anchor.focus({preventScroll:true});queuePosition();});
    }
    const feedback=$('[data-learn-feedback]',panel);
    const message=a?.feedback&&!a.feedback.startsWith('Верно.')&&!a.completed?a.feedback:'';
    if(feedback&&feedback.textContent!==message)feedback.textContent=message;
    const next=anchor?[anchor]:[];
    for(const n of highlights)if(!next.includes(n))n.classList.remove('learn-highlight');
    for(const n of next)if(!n.classList.contains('learn-highlight'))n.classList.add('learn-highlight');
    highlights=next;
    queuePosition();
    // Observe only visible saved results. This never clicks or saves on behalf of the learner.
    if(!done&&a){
      const checkKey=[l.id,a.id,W.state.active,a.step,JSON.stringify(a.data.outcomes),JSON.stringify(a.data.tasks),JSON.stringify(a.data.deals)].join(':');
      if(checkKey===verification)return;
      if(step.action==='verify_outcome'&&$('[data-learning-target="client-history"]')?.textContent.includes('Не взял трубку')){verification=checkKey;W.observe('verify_outcome');}
      if(step.action==='verify_history'&&$('.rvm-body')?.textContent.includes(AveronLearningEngine.refs(a).expectedNote)){verification=checkKey;W.observe('verify_history');}
      if(step.action==='verify_task'&&S.page==='callbacks'&&$('[data-learning-target="callback-list"]')){verification=checkKey;W.observe('verify_task');}
      if(step.action==='open_section'&&step.expected.section==='contracts'&&S.page==='deals'&&S.arg==='contracts'&&$('[data-learning-target="contract-journal"]'))W.observe('open_section',{section:'contracts'});
    }
  }
  function fieldKey(input){
    if(input.dataset.answer)return 'quiz:'+input.dataset.answer;
    if(input.dataset.f)return 'work:'+S.qnum+':'+input.dataset.f;
    if(input.dataset.v&&input.tagName.toLowerCase()==='textarea')return 'deal:'+input.dataset.v;
    if(input.closest('.move')&&input.tagName.toLowerCase()==='input')return 'move:'+input.closest('.cb').dataset.id+':'+input.type;
    if(input.id?.startsWith('dg-'))return 'contract:'+input.id;
    return null;
  }
  if(W){
    if(W.state.active&&!W.inProgramme()){W.state.active=null;W.save();}
    W.changed=schedule;
    document.addEventListener('crm-review-closed',event=>W.observe('close_review',{call:event.detail.id}));
    document.addEventListener('crm-deal-closed',event=>{const d=W.data().deals.find(d=>AveronClassroom.number(d.client)===event.detail.number);if(d)W.observe('close_deal',{deal:d.id});});
    document.addEventListener('crm-card-closed',event=>{const a=W.current(),t=a?.data.tasks.find(t=>t.id===a.ui.taskContext);if(t&&AveronClassroom.number(t.client)===event.detail.number)W.observe('close_card',{task:t.id});});
    document.addEventListener('click',event=>{
      const node=event.target;
      if(!node.closest('.learn-guide,.learn-resume')&&node.closest('[data-next] .item,.cb button,[data-out],[data-act="save"],[data-deal],[data-review],[data-person],[data-learning-report],[data-seg] button'))W.touch();
      const queueItem=node.closest('[data-next] .item');if(queueItem)W.observe('select_client',{client:['client-2','client-1','client-3'][+queueItem.dataset.i]});
      const row=node.closest('.cb');if(row&&node.closest('[data-move],[data-call]'))W.observe('select_task',{task:row.dataset.id});
      const filter=node.closest('[data-seg="show"] button');if(filter)W.observe('filter_deals',{filter:filter.dataset.v});
      const report=node.closest('[data-learning-report]');if(report&&!node.closest('button,a'))W.observe('select_report',{report:report.dataset.learningReport});
      const contract=node.closest('[data-learning-target="contract-journal"] [data-deal]');
      if(contract){const doc=W.data().documents.find(d=>AveronClassroom.number(W.data().deals.find(x=>x.id===d.deal).client)===contract.dataset.deal);if(doc)W.observe('select_document',{document:doc.id});}
      if(row&&node.closest('[data-move]'))W.draft('$moving',$('.move',row)?'':row.dataset.id);
      if(node.closest('[data-ev="note"]'))W.draft('$noteOpened','true');
      if(node.closest('[data-contract]'))W.draft('$contractOpened','true');
      const out=node.closest('[data-out]');if(out){W.draft('$outcome',out.dataset.out);W.draft('$outcomeNumber',S.qnum||'');}
      schedule();
    },true);
    document.addEventListener('input',event=>{if(event.target.dataset.answer)W.editAnswer();const key=fieldKey(event.target);if(key)W.draft(key,event.target.type==='checkbox'?String(event.target.checked):event.target.value);},true);
    document.addEventListener('change',event=>{if(event.target.matches('[data-ext-pick]'))W.touch();if(event.target.dataset.answer)W.editAnswer();const key=fieldKey(event.target);if(key)W.draft(key,event.target.type==='checkbox'?String(event.target.checked):event.target.value);},true);
    document.addEventListener('keydown',event=>{if(event.key==='Escape'&&panel?.contains(event.target)){event.stopImmediatePropagation();event.preventDefault();close();}},true);
    addEventListener('scroll',queuePosition,true);addEventListener('resize',queuePosition);
    window.visualViewport?.addEventListener('resize',queuePosition);window.visualViewport?.addEventListener('scroll',queuePosition);
    document.addEventListener('focusin',queuePosition,true);document.addEventListener('focusout',queuePosition,true);
    new MutationObserver(records=>{
      const clean=classes=>String(classes||'').split(/\s+/).filter(c=>c!=='learn-highlight').sort().join(' ');
      if(records.some(r=>r.type!=='attributes'||(!panel?.contains(r.target)&&!resume?.contains(r.target)&&(r.attributeName==='hidden'||clean(r.oldValue)!==clean(r.target.className)))))schedule();
    }).observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['class','hidden'],attributeOldValue:true});
    document.addEventListener('animationend',queuePosition,true);document.addEventListener('transitionend',queuePosition,true);
  }
  function restoreFields(){
    if(!W)return;
    const drafts=W.drafts();
    for(const node of $$('input,textarea,select')) {
      const key=fieldKey(node);if(!key||!Object.hasOwn(drafts,key)||(document.activeElement===node&&restoredInputs.has(node)))continue;
      restoredInputs.add(node);
      if(node.type==='checkbox')node.checked=drafts[key]==='true';
      else if(node.value!==drafts[key])node.value=drafts[key];
    }
  }
  window.learningFocusScope=box=>W&&box?.isConnected?(panel||resume):null;
  window.learningPageChanged=()=>{
    if(!W)return;
    const targets={summary:{'summary-filters':'#top .bar-f'},calls:{'call-filters':'#top .bar-f'},callbacks:{'callback-filters':'#top .bar-f'},deals:{'deal-filters':'#top .bar-f'},settings:{'settings-tabs':'#view .tabs'},robot:{'robot-tabs':'#view .tabs'}};
    for(const [id,selector] of Object.entries(targets[S.page]||{})){const n=$(selector);if(n)n.dataset.learningTarget=id;}

    if(W.state.active&&S.page!=='learning'&&!W.state.paused){W.draft('$page',S.page);W.draft('$arg',S.arg||'');}
    if(W.state.active)W.draft('$filters',JSON.stringify(S.f));
    // Restore values only, never submit or click. Native forms remain in control.
    restoreFields();
    if(restorePlanned&&W.state.active&&S.page!=='learning') {restorePlanned=false;restoreSelection().then(schedule).catch(e=>toast(e.message,true));}
    schedule();
  };
}());
