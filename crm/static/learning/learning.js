/* Lessons guide real CRM views. In the classroom the same forms use local APIs.
   Outside it, the catalog offers an explicit entry; no production action is graded. */
(function () {
  'use strict';
  const W=window.AveronLearningWorkspace?.classroom, restoredInputs=new WeakSet();
  let context=null, panel=null, highlights=[], scheduled=false, signature='', verification='', hints=true, restorePlanned=true;
  const lesson=()=>W?.lesson(W.state.active);
  const attempt=()=>W?.current();
  function clearHighlights(){for(const n of highlights)n.classList.remove('learn-highlight');highlights=[];}
  function close(){W?.pause();panel?.remove();panel=null;signature='';clearHighlights();}
  function contextHint(){return W?'Все действия выполняются в обычных вкладках на учебных данных. Новый вход очищает примеры и прогресс; обновление страницы сохраняет текущую попытку.':'В учебной учётке доступны те же вкладки CRM с вымышленными записями. Каждый новый вход начинает обучение с чистого состояния.';}
  function resetFilters(){S.f={period:'today',ext:''};S.qnum=null;S.personTab='over';S.repFilter='all';S.calls={result:'',kind:'',q:''};S.deal={show:'active',days:'90',stage:0};}
  async function launch(id,repeat=false){
    const l=W.lesson(id);if(!l?.available)throw new Error('Этот урок недоступен.');
    if(repeat&&!await askConfirm('Начать этот урок с чистых учебных данных?', 'Пройти заново'))return;
    W.start(id,repeat);resetFilters();restorePlanned=true;
    if(W.current()?.ui.selectedClient)S.qnum=AveronClassroom.number(W.current().ui.selectedClient);
    const a=attempt();
    // Continue where the user left the actual CRM, including a selected card.
    const last=W.state.drafts[id]?.['$page'];
    const step=l.kind==='guide'?l.guide[Math.min(W.state.guides[id]?.index||0,l.guide.length-1)]:l.steps[a.step];
    const page=repeat?null:last;
    const first=l.kind==='guide'?step:l.guide[0];
    await go(page||first.page, page?W.state.drafts[id]?.['$arg']||null:first.arg||null);
    schedule();
  }
  async function restoreSelection(){
    const a=attempt();if(!a||a.completed)return;
    const pending=lesson().steps[a.step];
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
    if(arg&&W){await launch(arg);return;}
    const lessons=context.lessons;
    const done=W?lessons.filter(l=>l.available&&W.status(l.id)==='Пройден').length:0;
    view.innerHTML=`<div class="page"><p class="hint">${esc(contextHint())}</p>${!W?'<a class="btn primary" href="/learning/enter">Войти в учебную учётку</a>':''}<h2>Программа ${context.role==='director'?'директора':'менеджера'}</h2>${W?`<p role="status" data-learning-program>Пройдено ${done} из ${lessons.filter(l=>l.available).length} доступных уроков.</p>`:''}<div class="learn-catalog">${lessons.map(l=>`<section class="card"><h2>${esc(l.title)}</h2><p>${esc(l.result)}</p>${W?`<span class="chip">${esc(W.status(l.id))}</span>`:''}${l.available?W?`<button class="btn primary" data-lesson="${l.id}">${W.status(l.id)==='Не начат'?'Начать урок':W.status(l.id)==='Пройден'?'Посмотреть результат':'Продолжить'}</button><button class="btn" data-repeat="${l.id}">Пройти заново</button>`:`<a class="btn" href="/learning/enter?lesson=${l.id}">Начать урок в учебной учётке</a>`:`<p class="hint">${esc(l.unavailable_reason)}</p>`}</section>`).join('')}</div></div>`;
    $$('[data-lesson]',view).forEach(b=>b.onclick=()=>launch(b.dataset.lesson).catch(e=>toast(e.message,true)));
    $$('[data-repeat]',view).forEach(b=>b.onclick=()=>launch(b.dataset.repeat,true).catch(e=>toast(e.message,true)));
  };
  function targetNodes(step){
    const selector=step?.selector || (step?.target?'[data-learning-target="'+step.target+'"]':'');
    if(!selector)return[];
    const scope=$$('.rvm').at(-1)||($('#drawer')?.classList.contains('open')?$('#drawer'):document);
    const shared=window.tourFind ? tourFind(selector) : null;
    return $$(selector,scope).filter(n=>!n.closest('.hidden')&&n.isConnected&&(n===shared||(n.getBoundingClientRect().width>0&&n.getBoundingClientRect().height>0)));
  }
  function schedule(){if(scheduled)return;scheduled=true;queueMicrotask(()=>{scheduled=false;draw();});}
  function draw(){
    if(!W||!lesson()||W.state.paused){panel?.remove();panel=null;clearHighlights();return;}
    restoreFields();
    const l=lesson(),a=attempt(),isGuide=l.kind==='guide',g=W.state.guides[l.id];
    const done=isGuide?g?.complete:a?.completed;
    const step=done?null:isGuide?l.guide[g.index]:l.steps[a.step];
    const host=$$('.rvm .rvm-body').at(-1)||($('#drawer')?.classList.contains('open')?$('#drawer .dr-b'):null)||$('.main')||$('#view')?.parentNode;
    if(!host)return;
    if(!panel){panel=document.createElement('section');panel.className='card learn-guide';panel.setAttribute('data-learning-guide','');panel.setAttribute('aria-label','Подсказка урока');}
    if(panel.parentNode!==host)host.insertBefore(panel,host.firstChild);
    const visible=done?[]:targetNodes(isGuide?step:step.screen);
    const sig=[l.id,a?.step,g?.index,done,visible.length>0,S.page,S.arg].join(':');
    if(sig!==signature){
      signature=sig;
      const situation=a?AveronLearningEngine.interpolate(l.situation,a):l.situation;
      panel.innerHTML=`<div class="row"><b>${esc(l.title)}</b><span class="sp"></span><button class="btn small" data-learn-close>Приостановить урок</button></div>${done?`<h3>Урок пройден</h3><p>${esc(l.result)}</p><button class="btn primary" data-learn-catalog>К программе обучения</button>`:`<details><summary>Рабочая ситуация</summary><p>${esc(situation)}</p>${l.adaptation?`<p>${esc(l.adaptation)}</p>`:''}</details><h3>Что сделать</h3><p>${esc(isGuide?step.text:step.title)}</p><p class="hint">${isGuide?esc(step.result):(step.action==='open_section'&&$('.rvm')?'Закройте карточку крестиком, затем откройте указанный раздел.':'Сделайте действие сами. Подсказка ничего не нажимает и не сохраняет за вас.')}</p>${step.quiz?'<div data-learn-quiz>'+step.quiz.map(f=>`<label class="field">${esc(f.label)}${f.options?`<select class="inp" data-answer="${f.key}"><option value="">Выберите</option>${f.options.map(([v,t])=>`<option value="${esc(v)}">${esc(t)}</option>`).join('')}</select>`:`<input class="inp" type="${f.type||'text'}" data-answer="${f.key}">`}</label>`).join('')+'<button class="btn primary" data-check-answer>Проверить ответ</button></div>':''}<p role="status" data-learn-feedback></p><div class="row"><button class="btn small" data-learn-hint>${hints?'Скрыть выделение':'Выделить нужный блок'}</button>${!visible.length&&step.screen?`<button class="btn small" data-learn-open>Открыть раздел «${esc(PAGES[step.screen.page]?.name||'CRM')}»</button>`:''}${isGuide?`<button class="btn primary" data-guide-next ${visible.length?'':'disabled'}>${g.index===l.guide.length-1?'Завершить показ':'Далее'}</button>`:''}<button class="btn small" data-learn-catalog>К программе</button></div>`}`;
      $('[data-learn-close]',panel).onclick=close;
      $('[data-learn-catalog]',panel).onclick=()=>{close();go('learning');};
      $('[data-learn-hint]',panel)?.addEventListener('click',()=>{hints=!hints;signature='';schedule();});
      $('[data-learn-open]',panel)?.addEventListener('click',()=>{if($('.rvm')){toast('Сначала закройте карточку крестиком.',true);return;}go(step.screen.page);});
      $('[data-guide-next]',panel)?.addEventListener('click',async()=>{
        if(!targetNodes(step).length)return;
        const rec=AveronLearningProgress.advanceGuide({guide:g},g.index,l.guide.length,true);
        W.state.guides[l.id]=rec.guide;W.save();
        if(!rec.guide.complete){const next=l.guide[rec.guide.index];await go(next.page,next.arg||null);}
        signature='';schedule();
      });
      $('[data-check-answer]',panel)?.addEventListener('click',()=>{const action={type:step.action};$$('[data-answer]',panel).forEach(n=>action[n.dataset.answer]=n.value);W.action(action);});
      for(const f of $$('[data-answer]',panel))f.value=W.drafts()['quiz:'+f.dataset.answer]||'';
    }
    const feedback=$('[data-learn-feedback]',panel);
    const message=a?.feedback||(!visible.length&&!step?.quiz?'Нужный блок пока не виден. Откройте карточку или указанный раздел.':'');
    if(feedback&&feedback.textContent!==message)feedback.textContent=message;
    const next=hints?visible:[];
    for(const n of highlights)if(!next.includes(n))n.classList.remove('learn-highlight');
    for(const n of next)if(!n.classList.contains('learn-highlight'))n.classList.add('learn-highlight');
    highlights=next;
    // Verification reads the visible saved result. It cannot create the result.
    if(!done&&a){
      const checkKey=[l.id,a.step,JSON.stringify(a.data.outcomes),JSON.stringify(a.data.tasks),JSON.stringify(a.data.deals)].join(':');
      if(checkKey===verification)return;
      if(step.action==='verify_outcome'&&$('[data-learning-target="client-history"]')?.textContent.includes('Не взял трубку')){verification=checkKey;W.observe('verify_outcome');}
      if(step.action==='verify_history'&&$('.rvm-body')?.textContent.includes(AveronLearningEngine.refs(a).expectedNote)){verification=checkKey;W.observe('verify_history');}
      if(step.action==='verify_task'&&S.page==='callbacks'&&$('[data-learning-target="callback-list"]')){verification=checkKey;W.observe('verify_task');}
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
    W.changed=schedule;
    document.addEventListener('click',event=>{
      const node=event.target;
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
    document.addEventListener('input',event=>{const key=fieldKey(event.target);if(key)W.draft(key,event.target.type==='checkbox'?String(event.target.checked):event.target.value);},true);
    document.addEventListener('change',event=>{const key=fieldKey(event.target);if(key)W.draft(key,event.target.type==='checkbox'?String(event.target.checked):event.target.value);},true);
    addEventListener('keydown',event=>{if(event.key==='Escape')close();});
    new MutationObserver(schedule).observe(document.body,{childList:true,subtree:true});
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
