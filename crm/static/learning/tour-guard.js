/* Normal CRM account; operational actions stay disabled only while the mode is on.
   No records, progress, snapshots or browser storage are created here. */
(function(){
 'use strict';
 let enabled=false,pending=0;
 const originalFetch=window.fetch.bind(window),authPosts=new Set(['/api/learning/mode','/api/login','/api/logout','/api/test/login']);
 const blockedReads=/^\/(?:webphone\/ws|api\/(?:webphone(?:\/|$)|contracts\/lookup\/|robot\/voices(?:\/|$)))/;
 function notify(element,label){document.dispatchEvent(new CustomEvent('crm-guide-blocked',{detail:{element,label:label||'Это действие'}}));}
 function operational(path,method){return !['GET','HEAD'].includes(method)&&!authPosts.has(path)||blockedReads.test(path);}
 function setMode(value){enabled=!!value;document.dispatchEvent(new CustomEvent('crm-guide-mode'));stripPhoneLinks();}
 window.CRMGuideGuard={active:()=>enabled,pending:()=>pending,setMode,notify};
 window.fetch=async(resource,init={})=>{
  const url=new URL(typeof resource==='string'?resource:resource.url,location.href),method=String(init.method||resource.method||'GET').toUpperCase();
  if(enabled&&(url.origin!==location.origin||operational(url.pathname,method))){notify(null,'Рабочее действие');throw new Error('В режиме обучения действие только объясняется. Данные не изменены.');}
  const mutation=!['GET','HEAD'].includes(method)&&!authPosts.has(url.pathname);let response;if(mutation)pending++;
  try{response=await originalFetch(resource,init);}finally{if(mutation)pending--;}
  if(url.origin===location.origin&&url.pathname==='/api/me'&&response.ok){const data=await response.clone().json();setMode(data.learning_mode);}
  return response;
 };
 function stripPhoneLinks(){if(!enabled)return;document.querySelectorAll('a[href]').forEach(link=>{if(!/^(?:tel:|sip:|callto:)/i.test(link.getAttribute('href')||''))return;link.setAttribute('href','#');link.setAttribute('data-guide-phone','');});}
 const unsafe='[data-act="dial"],[data-guide-phone],[data-out="no_answer"],[data-act="save"],[data-save],[data-done],[data-cancel],#dg-genBtn,#dg-innBtn';
 document.addEventListener('click',event=>{
  if(!enabled||event.target.closest('[data-guide-card],[data-guide-banner],.page-help,.tour,[data-page-help],[data-page-help-close],[data-guide-enable],[data-guide-start],[data-guide-tab],[data-guide-disable]'))return;
  const link=event.target.closest('a[href]');
  if(link&&link.getAttribute('data-guide-phone')===null){
   const url=new URL(link.href,location.href);
   if(url.origin!==location.origin||!['/',location.pathname,'/logout','/learning/exit'].includes(url.pathname)){event.preventDefault();event.stopImmediatePropagation();notify(link,'Переход по ссылке');return;}
  }
  const button=event.target.closest('button'),node=event.target.closest(unsafe);
  const action=button&&!button.closest('#nav,#tabbar,.gpt-chips')&&!button.matches('[data-go],[data-tab],[data-seg] button,[data-out],[data-move],[data-call],[data-ev],[data-contract],[data-skip],[data-act="copy"],[data-theme-btn],[data-logout]')&&/^(?:Сохранить|Отправить|Удалить|Запустить|Позвонить|Выполнено|Отменить перезвон|Сформировать|Выдать|Сменить учётку)/i.test(button.textContent.trim());
  if(node||action){event.preventDefault();event.stopImmediatePropagation();notify(node||button,(node||button).textContent.trim()||'Звонок');}
 },true);
 document.addEventListener('auxclick',event=>{if(enabled&&event.target.closest('a')){event.preventDefault();event.stopImmediatePropagation();notify(event.target.closest('a'),'Переход по ссылке');}},true);
 document.addEventListener('submit',event=>{if(enabled&&!event.target.closest('.login')){event.preventDefault();event.stopImmediatePropagation();notify(event.target,'Отправка формы');}},true);
 new MutationObserver(stripPhoneLinks).observe(document.documentElement,{childList:true,subtree:true});
}());
