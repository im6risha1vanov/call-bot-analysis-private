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
  function situation(l){if(l.kind!=='practice'||!l.available)return l.situation;const a=W?.state.attempts[l.id]||AveronLearningEngine.start(l,context,context.clock,'intro',0);return AveronLearningEngine.interpolate(l.situation,a);}
  function contextHint(){return W?'Все действия выполняются в обычных вкладках на учебных данных. Новый вход очищает примеры и прогресс; обновление страницы сохраняет текущую попытку.':'В учебной учётке доступны те же вкладки CRM с вымышленными записями. Каждый новый вход начинает обучение с чистого состояния.';}
  function resetFilters(){S.f={period:'today',ext:''};S.qnum=null;S.personTab='over';S.repFilter='all';S.calls={result:'',kind:'',q:''};S.deal={show:'active',days:'90',stage:0};}
  async function launch(id,repeat=false,mode='practice'){
    const l=W.lesson(id);if(!l?.available)throw new Error('Этот урок недоступен.');
    if(repeat&&!await askConfirm('Начать этот урок с чистых учебных данных?', 'Пройти заново'))return;
    W.start(id,repeat,{mode,manual:true});restorePlanned=true;
    if(!repeat&&W.drafts().$filters){try{S.f=JSON.parse(W.drafts().$filters);}catch(e){resetFilters();}}else resetFilters();
    const intro=$('[data-intro="'+id+'"]');if(intro)intro.open=false;
    if(W.current()?.ui.selectedClient)S.qnum=AveronClassroom.number(W.current().ui.selectedClient);
    const a=attempt();
    // Continue where the user left the actual CRM, including a selected card.
    const last=W.drafts()['$page'];
    const step=W.showing()?l.guide[Math.min(W.guide()?.index||0,l.guide.length-1)]:l.steps[W.visibleStep()];
    const page=repeat?null:last;
    const first=W.showing()?step:l.guide[0];
    await go(page||first.page, page?W.state.drafts[id]?.['$arg']||null:first.arg||null);
    schedule();
  }
  async function restoreSelection(){
    const a=attempt();if(W.showing()||!a||a.completed)return;
    const pending=lesson().steps[W.visibleStep()];
    // This opens a card, never saves or clicks an operational button.
    if(S.page==='deals'&&a.ui.selectedDeal&&['save_note','verify_history','answer_stalled'].includes(pending.action)) {
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
    if(S.page==='callbacks'&&pending.action==='answer_task'&&a.ui.selectedTask) {
      const task=a.data.tasks.find(t=>t.id===a.ui.selectedTask);if(task)workDrawer(W.task(task),{ext:AveronClassroom.ext(task.manager)});
    }
    if(S.page==='calls'&&a.ui.selectedCall&&pending.quiz)CRMReview.open(a.ui.selectedCall);
  }
  VIEWS.learning=async(view,arg)=>{
    context=W?W.context:await api('/api/learning/context'+(arg?'?lesson='+encodeURIComponent(arg):''));
    if(!view.isConnected)return;
    setHead({title:'Обучение'});
    if(arg&&W){await launch(arg,false,W.state.active===arg?W.state.mode:'practice');return;}
    const lessons=context.lessons;
    const done=W?lessons.filter(l=>l.available&&W.status(l.id)==='Пройден').length:0;
    view.innerHTML=`<div class="page"><p class="hint">${esc(contextHint())}</p>${!W?'<a class="btn primary" href="/learning/enter">Войти в учебную учётку</a>':''}<h2>Программа ${context.role==='director'?'директора':'менеджера'}</h2>${W?`<p role="status" data-learning-program>Пройдено ${done} из ${lessons.filter(l=>l.available).length} доступных уроков.</p>`:''}<div class="learn-catalog">${lessons.map(l=>`<section class="card"><h2>${esc(l.title)}</h2><p>${esc(l.result)}</p><details data-intro="${l.id}"><summary>Рабочая ситуация</summary><p>${esc(situation(l))}</p>${l.adaptation?`<p>${esc(l.adaptation)}</p>`:''}</details>${W?`<span class="chip">${esc(W.status(l.id))}</span>`:''}${l.available?W?`<button class="btn primary" data-lesson="${l.id}">${W.status(l.id)==='Не начат'?(l.kind==='guide'?'Показать, где нажать':'Попробовать самому'):W.status(l.id)==='Пройден'?'Посмотреть результат':'Продолжить'}</button>${l.kind==='practice'?`<button class="btn" data-show-lesson="${l.id}">Показать, где нажать</button>`:''}<button class="btn" data-repeat="${l.id}">Пройти заново</button>`:`<a class="btn" href="/learning/enter?lesson=${l.id}">Начать урок в учебной учётке</a>`:`<p class="hint">${esc(l.unavailable_reason)}</p>`}</section>`).join('')}</div></div>`;
    $$('[data-lesson]',view).forEach(b=>b.onclick=()=>launch(b.dataset.lesson,false,'practice').catch(e=>toast(e.message,true)));
    $$('[data-show-lesson]',view).forEach(b=>b.onclick=()=>launch(b.dataset.showLesson,false,'show').catch(e=>toast(e.message,true)));
    $$('[data-repeat]',view).forEach(b=>b.onclick=()=>launch(b.dataset.repeat,true).catch(e=>toast(e.message,true)));
  };
  let anchor=null, resume=null, frame=null, waitingSince=0, waitTimer=null, waitingKey='', focusHeading=false;
  const observed = typeof ResizeObserver==='function' ? new ResizeObserver(()=>queuePosition()) : null;
  const visibleStep=()=>W?.visibleStep();
  function guideState(){return W?.guide();}
  function activeStep(){const l=lesson();return W.showing()?l.guide[guideState().index]:l.steps[visibleStep()];}
  function showingDone(){return W.showing()?guideState()?.complete:attempt()?.completed&&!W.awaiting();}
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
    if(step.quiz) return targetNodes({selector:'.rvm-head, .dr-h, [data-learning-target="summary-kpis"]'}).slice(0,1);
    return targetNodes(W.showing()?step:step.screen);
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
    if(!W||!lesson()||$('.tour')){resume?.remove();resume=null;return;}
    if(!resume){
      resume=document.createElement('aside');resume.className='learn-resume';resume.setAttribute('aria-label','Урок на паузе');
      resume.innerHTML='<span>Урок на паузе</span><button class="btn small" data-learn-resume>Продолжить</button><button class="ghost" data-resume-program>К программе</button>';
      document.body.appendChild(resume);
      $('[data-learn-resume]',resume).onclick=()=>launch(lesson().id,false,W.state.mode).catch(e=>toast(e.message,true));
      $('[data-resume-program]',resume).onclick=()=>go('learning');
    }
    queuePosition();
  }
  function draw(){
    if(!W||!lesson()){panel?.remove();panel=null;resume?.remove();resume=null;clearHighlights();return;}
    if(W.state.paused){renderResume();return;}
    resume?.remove();resume=null;
    // Preserve the existing optional AveronGPT tour without stacking two tours.
    if($('.tour')){panel?.remove();panel=null;clearHighlights();return;}
    restoreFields();
    const l=lesson(),a=attempt(),isGuide=W.showing(),g=guideState(),done=showingDone();
    const index=isGuide?g.index:visibleStep(),step=done?null:activeStep();
    const visible=done?[]:anchorsFor(step);
    const previousAnchor=anchor;anchor=visible[0]||null;
    if(!panel){
      panel=document.createElement('section');panel.className='card learn-guide';panel.setAttribute('data-learning-guide','');
      panel.setAttribute('role','region');panel.setAttribute('aria-label','Подсказка урока');
      document.body.appendChild(panel);observed?.observe(panel);
    }
    const waitKey=[l.id,index,isGuide].join(':');
    if(previousAnchor!==anchor||waitingKey!==waitKey){
      waitingKey=waitKey;
      clearHighlights();observed?.disconnect();observed?.observe(panel);if(anchor)observed?.observe(anchor);
      waitingSince=Date.now();clearTimeout(waitTimer);
      if(!anchor&&!done)waitTimer=setTimeout(schedule,1400);
    }
    const waiting=!anchor&&!done&&Date.now()-waitingSince<1400;
    const pending=!isGuide&&W.awaiting();
    const sig=[l.id,index,isGuide,done,pending,!!anchor,waiting,S.page,S.arg].join(':');
    if(sig!==signature){
      signature=sig;
      const title=l.title.replace(/^[МД]\d+\.\s*/,''),count=isGuide?l.guide.length:l.steps.length;
      panel.dataset.learnStep=String(index);panel.dataset.mode=isGuide?'show':'practice';panel.dataset.ready=String(pending);
      const raw=done?l.result:(step.hint||step.text||step.title);
      const text=a&&!isGuide?AveronLearningEngine.interpolate(raw,a):raw;
      const token={id:l.id,index,attemptId:a?.id};
      panel.innerHTML=`<header class="learn-head"><div><b id="learn-heading" tabindex="-1" data-learn-heading>${esc(title)}</b><span class="learn-step">${done?(isGuide?'Показ завершён':'Урок пройден'):`Шаг ${index+1} из ${count}`}</span></div><button class="learn-close" type="button" aria-label="Приостановить урок" data-learn-close>×</button></header><div class="learn-body"><p class="learn-instruction">${esc(text)}</p>${!done&&step.quiz&&!isGuide?'<div data-learn-quiz>'+step.quiz.map(f=>`<label class="field">${esc(f.label)}${f.options?`<select class="inp" data-answer="${f.key}"><option value="">Выберите</option>${f.options.map(([v,t])=>`<option value="${esc(v)}">${esc(t)}</option>`).join('')}</select>`:`<input class="inp" type="${f.type||'text'}" data-answer="${f.key}">`}</label>`).join('')+`<button class="btn small" type="button" data-check-answer ${pending?'disabled':''}>Проверить ответ</button></div>`:''}<p class="learn-status" role="status" data-learn-feedback></p>${!anchor&&!done?`<p class="learn-missing" data-missing-target>${waiting?'Ожидаю появления элемента…':'Элемент не найден. Откройте нужную карточку или вернитесь к программе.'}</p>`:''}</div><footer class="learn-actions"><button class="ghost" type="button" data-learn-catalog>К программе</button>${anchor?'<button class="ghost" type="button" data-learn-focus aria-label="Перейти к выделенному элементу">К элементу</button>':''}${!done?`<button class="btn primary" type="button" ${isGuide?'data-guide-next':'data-learn-next'} ${(isGuide?!!anchor:pending)?'':'disabled'}>Далее →</button>`:''}</footer>`;
      $('[data-learn-close]',panel).onclick=close;
      $('[data-learn-catalog]',panel).onclick=()=>{close();go('learning');};
      $('[data-learn-focus]',panel)?.addEventListener('click',()=>{anchor?.scrollIntoView({block:'center',behavior:reduce?'auto':'smooth'});if(anchor?.matches('input,select,textarea,button'))anchor.focus({preventScroll:true});queuePosition();});
      $('[data-guide-next]',panel)?.addEventListener('click',async event=>{
        if(event.detail>1||!anchorsFor(step).length||W.state.paused||guideState().index!==token.index)return;
        const rec=AveronLearningProgress.advanceGuide({guide:g},g.index,l.guide.length,true);
        if(l.kind==='guide')W.state.guides[l.id]=rec.guide;else W.state.previews[l.id]=rec.guide;
        W.save();
        if(!rec.guide.complete){const next=l.guide[rec.guide.index];await go(next.page,next.arg||null);}
        focusHeading=true;signature='';schedule();
      });
      $('[data-learn-next]',panel)?.addEventListener('click',event=>{
        if(event.detail>1)return;
        if(W.confirm(token.id,token.index,token.attemptId)){focusHeading=true;signature='';schedule();}
      });
      $('[data-check-answer]',panel)?.addEventListener('click',()=>{W.touch();const action={type:step.action};$$('[data-answer]',panel).forEach(n=>action[n.dataset.answer]=n.value);W.action(action);});
      for(const f of $$('[data-answer]',panel))f.value=W.drafts()['quiz:'+f.dataset.answer]||'';
      if(focusHeading){focusHeading=false;$('[data-learn-heading]',panel)?.focus({preventScroll:true});}
    }
    const feedback=$('[data-learn-feedback]',panel);
    const message=pending?'Верно. Можно продолжить.':(!isGuide&&a?.feedback&&!a.feedback.startsWith('Верно.')?a.feedback:'');
    if(feedback&&feedback.textContent!==message)feedback.textContent=message;
    const next=anchor?[anchor]:[];
    for(const n of highlights)if(!next.includes(n))n.classList.remove('learn-highlight');
    for(const n of next)if(!n.classList.contains('learn-highlight'))n.classList.add('learn-highlight');
    highlights=next;
    queuePosition();
    // Saved results are still checked by the original engine. Acknowledgement
    // only changes the visible step; it never runs an action for the learner.
    if(!done&&!isGuide&&!pending&&a){
      const checkKey=[l.id,a.step,JSON.stringify(a.data.outcomes),JSON.stringify(a.data.tasks),JSON.stringify(a.data.deals)].join(':');
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
    if(!W.showing()&&W.current()&&!W.presenter()?.manual)W.start(W.state.active,false,{manual:true,mode:'practice'});
    W.changed=schedule;
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

    if(W.state.active&&S.page!=='learning'){W.draft('$page',S.page);W.draft('$arg',S.arg||'');}
    // Restore values only, never submit or click. Native forms remain in control.
    restoreFields();
    if(restorePlanned&&W.state.active&&S.page!=='learning') {restorePlanned=false;restoreSelection().then(schedule).catch(e=>toast(e.message,true));}
    schedule();
  };
}());
