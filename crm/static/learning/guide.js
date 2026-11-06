/* Orientation in the ordinary account: real authorized reads, no exercise records.
   Only the current screen/step lives in memory. Reload starts the explanation again. */
(function(){
 'use strict';
 let context=null,loading=null,panel=null,highlight=null,signature='',stepIndex=0,tabIndex=0,started=false,paused=false,busy=false,timer=null,manual=null,drag=null,scheduled=false;
 const guard=window.CRMGuideGuard;
 const active=()=>guard.active()&&!!S.me?.learning_mode;
 const tab=()=>context?.tabs[tabIndex];
 function step(){
  const current=tab();if(!current)return null;
  if(stepIndex<current.steps.length)return current.steps[stepIndex];
  const next=context.tabs[tabIndex+1];
  return next?{selector:'#nav [data-go="'+next.page+'"]',title:'Перейдите в «'+next.title+'»',text:'Раздел просмотрен. Найдите следующий раздел в меню CRM.',event:'navigate',next:next.id}:
   {selector:'',title:'Обзор завершён',text:'Вы познакомились с основными разделами. Можно вернуться к любой вкладке или выключить режим обучения и начать обычную работу.',event:'finish'};
 }
 async function load(){if(context)return context;if(!loading)loading=api('/api/learning/guide').then(data=>{context=data;return data;}).finally(()=>loading=null);return loading;}
 function clearHighlight(){highlight?.classList.remove('learn-highlight');highlight=null;}
 function dismiss(){clearTimeout(timer);timer=null;busy=false;panel?.remove();panel=null;signature='';clearHighlight();}
 function pause(){paused=true;dismiss();banner();}
 function banner(){
  const node=$('[data-guide-banner]');if(!node)return;
  const button=$('[data-guide-resume]',node);button.textContent=paused?'Продолжить подсказки':'Показать подсказки';
  button.onclick=async()=>{await load();started=true;paused=false;selectCurrent();draw();};
  $('[data-guide-disable]',node).onclick=()=>toggle(false);
 }
 function matchesTab(t){return t.page===S.page&&(t.arg?S.arg===t.arg:!(S.page==='deals'&&S.arg==='contracts'));}
 function selectCurrent(){const index=context?.tabs.findIndex(matchesTab);if(index>=0&&index!==tabIndex){tabIndex=index;stepIndex=0;manual=null;signature='';}}
 async function toggle(on){
  if(on&&(S.busy||guard.pending())){toast('Дождитесь завершения текущего действия, затем включите режим обучения.',true);return;}
  if(on&&(typeof WP!=='undefined'&&WP.session)){toast('Сначала завершите текущий звонок, затем включите режим обучения.',true);return;}
  const previous=guard.active();if(on)guard.setMode(true);
  try{await api('/api/learning/mode',{method:'POST',body:{enabled:on}});guard.setMode(on);dismiss();location.href=on?'/#learning/start':'/'+(location.hash||'#learning');location.reload();}
  catch(error){guard.setMode(previous);toast(CRMText.error(error,'Не удалось переключить режим обучения. Попробуйте ещё раз.'),true);}
 }
 async function page(view,arg){
  await load();if(!view.isConnected)return;setHead({title:'Обучение'});banner();
  if(!active()){
   view.innerHTML='<div class="page"><h2>Как пользоваться CRM</h2><p>Включите подсказки в своём обычном аккаунте. Вы увидите доступные вам реальные вкладки, клиентов и звонки.</p><p>В режиме обучения звонки, отправки и изменения не выполняются. Подсказки и GIF объясняют, где находится действие и когда оно пригодится. Никаких учебных записей и результатов нет.</p><button class="btn primary" type="button" data-guide-enable>Включить режим обучения</button></div>';
   $('[data-guide-enable]',view).onclick=()=>toggle(true);return;
  }
  view.innerHTML='<div class="page"><h2>Обзор CRM</h2><p>Вы в своём аккаунте. Доступны реальные данные в пределах ваших прав. Рабочие действия отключены; ничего сохранять или отвечать на вопросы не нужно.</p><button class="btn primary" type="button" data-guide-start>Пройти обучение</button><p class="hint">'+esc(context.tabs.map(t=>t.title).join(' → '))+'</p></div>';
  const start=async()=>{started=true;paused=false;tabIndex=0;stepIndex=0;manual=null;await go(tab().page,tab().arg||null);draw();};
  $('[data-guide-start]',view).onclick=start;
  if(arg==='start')await start();
 }
 function example(st){const key=st.example,item=context.examples?.[key];if(!item||!/^[a-z_]+$/.test(key))return '';const still=matchMedia('(prefers-reduced-motion: reduce)').matches;
  return `<figure class="learn-example" data-guide-example="${key}"><figcaption>Пример расположения и действий</figcaption><img width="${item.width}" height="${item.height}" src="/static/learning/examples/${key}.${still?'png':'gif'}" alt="${esc(item.alt)}" data-guide-image data-playing="${!still}"><button class="ghost" type="button" data-guide-play aria-pressed="${!still}">${still?'Включить пример':'Приостановить пример'}</button><p class="learn-example-note">В GIF вымышленные значения. Повторять их или сохранять что-либо не требуется.</p></figure>`;
 }
 function visible(node){const r=node?.getBoundingClientRect();return node?.isConnected&&!node.closest('.hidden')&&(!node.checkVisibility||node.checkVisibility())&&r?.width>0&&r?.height>0;}
 function anchor(st){if(!st?.selector)return null;return $$(st.selector).find(visible)||null;}
 function place(){if(!panel?.isConnected||drag)return;const v=window.visualViewport,viewport={left:v?.offsetLeft||0,top:v?.offsetTop||0,width:v?.width||innerWidth,height:v?.height||innerHeight};
  panel.style.maxHeight=Math.max(70,viewport.height-20)+'px';panel.style.width=Math.min(300,viewport.width-20)+'px';
  const r=panel.getBoundingClientRect(),target=visible(highlight)?highlight.getBoundingClientRect():null;
  const obstacles=$$('input,textarea,select,button,a[href],.page-help,.banner').filter(n=>!panel.contains(n)&&n!==highlight&&!highlight?.contains(n)&&visible(n)).map(n=>n.getBoundingClientRect());
  const options={target,card:{width:r.width||300,height:r.height||180},viewport,obstacles,narrow:viewport.width<680};
  let p=manual?AveronLearningPlacement.move({...options,position:manual}):AveronLearningPlacement.place(options);
  if(p.blocked){panel.dataset.compact='true';p=AveronLearningPlacement.place({...options,card:{width:220,height:44}});}else panel.dataset.compact='false';
  panel.hidden=!!p.blocked;if(p.blocked)return;
  panel.style.left=p.left+'px';panel.style.top=p.top+'px';panel.style.setProperty('--arrow-x',p.arrowX+'px');panel.style.setProperty('--arrow-y',p.arrowY+'px');panel.dataset.placement=p.side;panel.dataset.detached=String(p.detached);
 }
 function draw(){
  if(!active()||!started||paused||S.page==='learning'||!context?.tabs.some(matchesTab)||$('.tour')){if(panel)dismiss();return;}
  const st=step();if(!st)return;const nextAnchor=anchor(st);if(highlight!==nextAnchor){clearHighlight();highlight=nextAnchor;highlight?.classList.add('learn-highlight');}
  if(!panel){panel=document.createElement('section');panel.className='card learn-guide';panel.setAttribute('data-guide-card','');panel.setAttribute('role','region');panel.setAttribute('aria-label','Подсказка по CRM');document.body.appendChild(panel);}
  const key=[tab()?.id,stepIndex,st.title].join(':');
  if(signature!==key){signature=key;manual=null;
   panel.dataset.guideTab=tab().id;panel.dataset.guideStep=String(stepIndex);
   panel.innerHTML=`<header class="learn-head"><div class="learn-handle" data-guide-drag tabindex="0" role="button" aria-label="Переместить подсказку: перетащите заголовок или используйте стрелки"><b>${esc(tab().title)}: ${esc(st.title)}</b><span class="learn-step">Шаг ${Math.min(stepIndex+1,tab().steps.length)} из ${tab().steps.length}</span><span class="learn-move-note">Перетащите за заголовок</span></div><button class="learn-close" type="button" data-guide-close aria-label="Скрыть подсказку">×</button></header><div class="learn-body"><p class="learn-instruction">${esc(st.text)}</p><p class="learn-purpose"><b>Когда пригодится.</b> ${esc(st.event==='navigate'?'Меню помогает быстро перейти к нужному делу.':st.event==='finish'?'В обычной работе используйте подсказки справки, если забыли порядок действий.':'Эта подсказка показывает обычный порядок работы. В режиме обучения рабочее действие не выполняется.')}</p>${example(st)}<p class="learn-status" data-guide-feedback role="status"></p><p class="learn-missing" data-guide-missing></p></div><footer class="learn-actions"><button class="ghost" type="button" data-guide-program>К программе</button><button class="ghost" type="button" data-guide-focus>К элементу</button><button class="btn small" type="button" data-guide-continue>Понятно</button></footer>`;
   $('[data-guide-close]',panel).onclick=pause;$('[data-guide-program]',panel).onclick=()=>{pause();go('learning');};
   $('[data-guide-focus]',panel).onclick=()=>{manual=null;highlight?.scrollIntoView({block:'center',behavior:reduce?'auto':'smooth'});if(highlight?.matches('button,input,select,textarea'))highlight.focus({preventScroll:true});place();};
   $('[data-guide-continue]',panel).onclick=()=>{if(st.event==='finish'){pause();go('learning');}else advance(false);};
   $('[data-guide-play]',panel)?.addEventListener('click',event=>{const image=$('[data-guide-image]',panel),key=image.closest('[data-guide-example]').dataset.guideExample,play=image.dataset.playing!=='true';image.src='/static/learning/examples/'+key+(play?'.gif':'.png');image.dataset.playing=String(play);event.currentTarget.textContent=play?'Приостановить пример':'Включить пример';event.currentTarget.setAttribute('aria-pressed',String(play));});
   $('[data-guide-image]',panel)?.addEventListener('load',place);
  }
  const missing=$('[data-guide-missing]',panel),message=!highlight&&st.selector?'На экране сейчас нет этого элемента: список может быть пуст или действие недоступно. Посмотрите пример и продолжите обзор.':'';
  if(missing.textContent!==message)missing.textContent=message;
  $('[data-guide-focus]',panel).disabled=!highlight;
  const button=$('[data-guide-continue]',panel);button.textContent=st.event==='finish'?'К программе':st.event==='navigate'?'Открыть раздел':!highlight?'Продолжить обзор':'Понятно';
  place();
 }
 function advance(acknowledge=true){
  if(busy||!started)return;const st=step();if(!st)return;
  if(st.event==='navigate'){const index=context.tabs.findIndex(t=>t.id===st.next);tabIndex=index;stepIndex=0;manual=null;signature='';go(tab().page,tab().arg||null);return;}
  const next=()=>{busy=false;stepIndex++;manual=null;draw();};
  if(!acknowledge||!panel){next();return;}
  busy=true;const body=$('.learn-body',panel);let badge=$('[data-guide-ack]',panel);
  if(!badge){badge=document.createElement('div');badge.className='learn-success';badge.setAttribute('data-guide-ack','');badge.setAttribute('role','status');badge.innerHTML='<svg viewBox="0 0 52 52" aria-hidden="true"><circle cx="26" cy="26" r="23"/><path d="m14 26 8 8 16-17"/></svg><b>Действие показано</b><p>Рабочие данные не изменены.</p>';body.insertBefore(badge,body.firstChild);body.scrollTop=0;}
  timer=setTimeout(next,1200);
 }
 function match(st,node){return node&&st&&(!st.match||node.closest(st.match))&&!!node.closest(st.selector);}
 function onInteraction(event){if(!active()||!started||paused||busy||event.target.closest('[data-guide-card],.page-help,.tour'))return;const st=step();if(st?.event===event.type&&match(st,event.target))queueMicrotask(()=>advance());}
 function actionExplanation(detail){
  const node=detail.element,label=detail.label||'Это действие';
  let meaning='«'+label+'» выполняет рабочую операцию.';
  if(node?.matches('[data-act="dial"],[data-guide-phone]'))meaning='В обычной работе эта кнопка начинает соединение с клиентом. Здесь звонок не начинается.';
  else if(node?.matches('[data-out="no_answer"]'))meaning='«Не взял трубку» сразу фиксирует недозвон. Здесь итог звонка не меняется.';
  else if(node?.matches('[data-act="save"]'))meaning='«Сохранить» фиксирует выбранный итог; для перезвона сохраняет срок и комментарий. Здесь запись не создаётся.';
  else if(node?.matches('[data-save]')&&node.closest('.move'))meaning='Эта кнопка меняет срок того же перезвона, не создавая вторую задачу. Здесь срок остаётся прежним.';
  else if(node?.matches('[data-save]')&&node.closest('.rvm'))meaning='В обычной работе выбранное событие или заметка появятся в истории этой сделки. Здесь история не изменяется.';
  else if(node?.matches('#dg-genBtn'))meaning='В обычной работе формируется документ и запись в журнале. Здесь документ не создаётся и номер не занимается.';
  else if(/^Удалить/.test(label))meaning='В обычной работе удаляется выбранная запись. В режиме обучения она остаётся на месте.';
  else if(/^Отправить/.test(label))meaning='Эта кнопка передаёт введённые данные адресату. В режиме обучения отправки нет.';
  return meaning+' Посмотрите порядок действий в подсказке или справке.';
 }
 function blocked(event){if(!active())return;const st=step(),detail=event.detail||{};
  if(panel){const line=$('[data-guide-feedback]',panel);line.textContent=actionExplanation(detail);if(st?.event==='click'&&match(st,detail.element))advance();}
  else {load().then(()=>{started=true;paused=false;selectCurrent();draw();if(panel)$('[data-guide-feedback]',panel).textContent='Режим обучения: рабочее действие не выполнено. Посмотрите описание и пример.';else if($('[data-guide-banner] span'))$('[data-guide-banner] span').textContent='Режим обучения: рабочее действие не выполнено. Его назначение описано в справке вкладки.';});}
 }
 function schedule(){if(scheduled)return;scheduled=true;queueMicrotask(()=>{scheduled=false;draw();});}
 function pageChanged(){banner();if(!active())return;if(started){selectCurrent();schedule();}}
 function install(){
  VIEWS.learning=page;window.learningPageChanged=pageChanged;window.learningFocusScope=()=>panel;window.learningShowsSuccess=()=>busy&&!!panel;
  document.addEventListener('click',onInteraction,true);document.addEventListener('change',onInteraction,true);
  document.addEventListener('crm-guide-mode',()=>{if(!guard.active()){started=false;paused=false;context=null;dismiss();}});
  document.addEventListener('crm-guide-blocked',blocked);document.addEventListener('crm-help-changed',schedule);
  document.addEventListener('pointerdown',event=>{const handle=event.target.closest('[data-guide-drag]');if(!handle||!panel?.contains(handle)||(event.button!==undefined&&event.button!==0))return;event.preventDefault();const r=panel.getBoundingClientRect();drag={x:event.clientX,y:event.clientY,left:r.left,top:r.top,id:event.pointerId};handle.setPointerCapture?.(event.pointerId);});
  document.addEventListener('pointermove',event=>{if(!drag||event.pointerId!==drag.id)return;manual={left:drag.left+event.clientX-drag.x,top:drag.top+event.clientY-drag.y};panel.style.left=manual.left+'px';panel.style.top=manual.top+'px';});
  const stopDrag=()=>{if(!drag)return;drag=null;place();};document.addEventListener('pointerup',stopDrag);document.addEventListener('pointercancel',stopDrag);
  document.addEventListener('keydown',event=>{if(!panel?.contains(event.target))return;if(event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();pause();return;}if(!event.target.matches('[data-guide-drag]'))return;const d={ArrowLeft:[-10,0],ArrowRight:[10,0],ArrowUp:[0,-10],ArrowDown:[0,10]}[event.key];if(d){event.preventDefault();const r=panel.getBoundingClientRect();manual={left:r.left+d[0],top:r.top+d[1]};place();}});
  addEventListener('scroll',place,true);addEventListener('resize',place);window.visualViewport?.addEventListener('resize',place);
  new MutationObserver(records=>{if(records.some(r=>!panel?.contains(r.target)))schedule();}).observe(document.body,{childList:true,subtree:true});
 }
 window.AveronCRMGuide={install,toggle,pageChanged};
}());
