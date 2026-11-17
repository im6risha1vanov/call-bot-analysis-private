'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const script = fs.readFileSync('crm/static/callanalysis.js', 'utf8');
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ctx = vm.createContext({esc, fmt: v => String(v), encodeURIComponent});
vm.runInContext(script, ctx);
const render = data => ctx.caAdviceBody(data);
const record = {status:'ready', brief:{status:'ready', summary:'Итог <img src=x onerror=1>', actions:[
  {title:'Первое', diagnosis:'Основание', action:'Изменить', test:'Проверить', evidence:[{phone:'9000000001',call_id:7,run_id:4,quote:'<script>не запускать</script>',at:8}]},
  {title:'Второе'}, {title:'Третье'}, {title:'Лишнее'}], limitations:['Небольшая выборка']}};
const html = render({advisor:record});
assert.ok(html.includes('Главный вывод') && html.includes('Что изменить') && html.includes('Как проверить'));
assert.ok(!html.includes('<script>') && !html.includes('<img src=x'));
assert.ok(html.includes('&lt;script&gt;') && html.includes(':analysis:lead:9000000001:call:7'));
assert.ok(html.includes('Звонки и подтверждения') && !html.includes('Лишнее'));
assert.ok(render({advisor:{status:'ready',brief:{status:'insufficient',summary:'Мало данных',limitations:[]}}}).includes('Пока недостаточно данных'));
assert.ok(render({advisor:{status:'error',message:'<ошибка>'}}).includes('&lt;ошибка&gt;'));
assert.ok(render({advisor:{status:'running'}}).includes('Анализируем разговоры'));
assert.ok(render({}).includes('Вывод пока недоступен'));
const historic = render({advisor:record,historical_findings:[{start:'2026-11-11',end:'2026-11-17',project_name:'Упаковка',leads:3,evidence:[]}]});
assert.ok(historic.includes('<details class="ca-advice-archive">') && historic.includes(':analysis:findings:2026-11-11:2026-11-17'));
assert.ok(script.includes("if (arg === ':analysis' || findingRoute) return caFindingsView"));
assert.ok(script.includes('data-arg=":analysis:stats">Статистика и журнал'));
assert.ok(script.includes('seq !== serial') && script.includes('30000'));
const badId = ctx.caFindingCall({phone:'123',call_id:'7" onmouseover="bad',run_id:1});
assert.ok(!badId.includes(' onmouseover="bad'));
(async () => {
  const response = summary => ({start:'2026-11-11',end:'2026-11-17',filters:{project:'',base:'',operator:''},
    options:{projects:[],bases:[],operators:[]},advisor:{status:'ready',brief:{status:'ready',summary,actions:[],limitations:[]}}});
  const filters = {innerHTML:'', isConnected:true}, body = {innerHTML:'', isConnected:true};
  const button = {listener:null,addEventListener(_name, fn){this.listener=fn;}};
  const fields = {'[data-advice-start]':{value:'2026-11-11'}, '[data-advice-end]':{value:'2026-11-17'},
                  '[data-advice-project]':{value:''}, '[data-advice-base]':{value:''}, '[data-advice-operator]':{value:''},
                  '[data-advice-apply]':button};
  const pending = [];
  Object.assign(ctx, {S:{}, setHead:()=>{}, clearTimeout:()=>{}, setTimeout:()=>0,
    qs: obj => '?' + new URLSearchParams(Object.entries(obj).filter(([,v]) => v != null && v !== '')).toString(),
    api: () => new Promise(resolve => pending.push(resolve)),
    $: (selector, root) => root === filters ? fields[selector] : selector === '[data-advice-filters]' ? filters
      : selector === '[data-advice-content]' ? body : null});
  const view = {innerHTML:'', isConnected:true};
  const initial = ctx.caFindingsView(view);
  pending.shift()(response('Начальный вывод'));
  await initial;
  assert.ok(body.innerHTML.includes('Начальный вывод'));
  button.listener();
  fields['[data-advice-project]'].value = 'Другая база';
  button.listener();
  assert.equal(pending.length, 2);
  pending[1](response('Новый вывод'));
  await new Promise(resolve => setImmediate(resolve));
  pending[0](response('Старый ответ'));
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(body.innerHTML.includes('Новый вывод') && !body.innerHTML.includes('Старый ответ'));
  process.stdout.write('callanalysis UI assertions passed\n');
})().catch(error => { process.stderr.write(String(error.stack || error)); process.exitCode = 1; });
