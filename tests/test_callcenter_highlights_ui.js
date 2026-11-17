'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const script = fs.readFileSync('crm/static/callcenter.js', 'utf8');
const esc = v => String(v ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const context = vm.createContext({esc,fmt:v=>String(v),VIEWS:{}}); vm.runInContext(script,context);
const item = (observation, action, baseStatus='insufficient') => ({status:'ready',brief:{operator_review:{script:{status:'supported',observation,action},base:{status:baseStatus,observation:'База не подтверждена'}}}});
const data = {start:'2026-11-11',end:'2026-11-17',operators:{olga:item('Вывод <img>','Исправить'),oksana:item('Вывод Оксаны','Действие')}};
let html = context.ccHighlights(data,'');
assert.ok(html.includes('Ольга') && html.includes('Оксана') && html.includes('2026-11-11 — 2026-11-17'));
assert.ok(html.includes('Вывод &lt;img&gt;') && !html.includes('<img>') && html.includes('Что исправить:'));
assert.ok(html.includes(':analysis:operator:olga:2026-11-11:2026-11-17') && html.includes(':analysis:operator:oksana:2026-11-11:2026-11-17'));
assert.ok(context.ccHighlights(data,'olga').includes('Ольга') && !context.ccHighlights(data,'olga').includes('Оксана'));
assert.ok(context.ccHighlights(data,'oksana').includes('Оксана') && !context.ccHighlights(data,'oksana').includes('Ольга'));
assert.equal(context.ccHighlights(data,'robot'),'');
assert.ok(context.ccHighlightCard('olga',{status:'stale',brief:item('СТАРЫЙ','действие').brief},data.start,data.end).includes('Обновляем'));
assert.ok(!context.ccHighlightCard('olga',{status:'stale',brief:item('СТАРЫЙ','действие').brief},data.start,data.end).includes('СТАРЫЙ'));
(async()=>{
  const high = {innerHTML:''}, stats = {innerHTML:''}, requests = [], calls = [];
  let binds;
  const view = {isConnected:true, innerHTML:''};
  const opts = {VIEWS:context.VIEWS,S:{me:{role:'director',privileged:true,readonly:false,test:false,support:false,callcenter_mode:'sheet',callcenter_filters:true},cc:{period:'',operator:''}},
    $:(selector,root)=>selector==='[data-cc-highlights]'?high:selector==='[data-cc-stats]'?stats:selector==='#top'?{}:null,
    api:url=>{calls.push(url);return new Promise((resolve,reject)=>requests.push({url,resolve,reject}));},
    qs:o=>{const x=new URLSearchParams(Object.entries(o).filter(([,v])=>v)).toString();return x?'?'+x:'';},
    clearTimeout:()=>{},setTimeout:()=>1,setHead:()=>{},seg:()=>'',bindSeg:(_x,v)=>{binds=v},PERIOD_OPTS:[],shortName:x=>x,
    kpiHtml:()=>'',countUp:()=>{},moreBlock:()=>'',pctOf:()=>'',plainItems:()=>'',help:()=>'',plural:()=>'',svg:()=>''};
  Object.assign(context,opts);
  const first = context.VIEWS.callcenter(view,null);
  assert.equal(requests.length,2); assert.ok(requests[0].url.includes('operator-highlights'));
  requests[0].resolve(data); await new Promise(r=>setImmediate(r));
  assert.ok(high.innerHTML.includes('Вывод Оксаны') && stats.innerHTML.includes('Загружаем'));
  requests[1].reject(new Error('Источник недоступен')); await first;
  assert.ok(high.innerHTML.includes('Вывод Оксаны') && stats.innerHTML.includes('Не удалось загрузить статистику'));
  binds.ccop('olga'); binds.ccop('oksana');
  const oh=requests.filter((r,i)=>i>=2 && r.url.includes('operator-highlights'));
  assert.equal(oh.length,2);
  oh[1].resolve(data); await new Promise(r=>setImmediate(r));
  oh[0].resolve(data); await new Promise(r=>setImmediate(r));
  assert.ok(high.innerHTML.includes('Оксана') && !high.innerHTML.includes('Ольга'));
  process.stdout.write('callcenter highlights UI assertions passed\n');
})().catch(e=>{process.stderr.write(String(e.stack||e));process.exitCode=1});
