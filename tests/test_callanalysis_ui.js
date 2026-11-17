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
assert.ok(script.includes("if (arg === ':analysis' || findingRoute || operatorRoute)"));
assert.ok(script.includes('data-arg=":analysis:stats">Статистика и журнал'));
assert.ok(script.includes('seq !== serial') && script.includes('30000'));
const badId = ctx.caFindingCall({phone:'123',call_id:'7" onmouseover="bad',run_id:1});
assert.ok(!badId.includes(' onmouseover="bad'));

assert.equal(ctx.caAdviceRoute('olga'), ':analysis:operator:olga');
assert.equal(ctx.caAdviceRoute('oksana','2026-11-11','2026-11-17'), ':analysis:operator:oksana:2026-11-11:2026-11-17');
const switchHtml = ctx.caAdviceSwitch('oksana');
assert.ok(switchHtml.includes(':analysis:operator:olga') && switchHtml.includes(':analysis:operator:oksana'));
assert.ok(switchHtml.includes('data-advice-switch="oksana" aria-current="page"'));
const personalBrief = {status:'ready',summary:'Личный результат',operator_review:{
  script:{status:'supported', observation:'Наблюдение <script>нет</script>',action:'Совет',test:'Метрика',evidence:[{phone:'9000000001',call_id:7,run_id:4,quote:'Фраза',at:8}]},
  base:{status:'insufficient'}},actions:[{title:'ОБЩЕЕ'}],limitations:[]};
const personal = ctx.caAdviceBody({advisor:{status:'ready',brief:personalBrief},historical_findings:[{evidence:[]}]}, 'olga');
assert.ok(personal.includes('Работа по скрипту') && personal.includes('Качество базы') && personal.includes('Совет'));
assert.ok(personal.includes('Недостаточно подтверждённых данных') && personal.includes(':analysis:lead:9000000001:call:7'));
assert.ok(!personal.includes('ОБЩЕЕ') && !personal.includes('Исторический пример') && !personal.includes('<script>') && !personal.includes('Личный результат'));
const explained = ctx.caPersonalSection('Качество базы', {status:'insufficient', observation:'Источник <не подтверждён>'}, 'base');
assert.ok(explained.includes('Источник &lt;не подтверждён&gt;') && !explained.includes('<не подтверждён>'));
const baseEvidence = ctx.caAdviceSources([
  {attribution:'matched', project:'Упаковка <img>', base:'B1_42', quote:'Нет', phone:'9000000001', call_id:9},
  {attribution:'ambiguous', project:'Не показывать', base:'Скрытая база', quote:'Нет', phone:'9000000002', call_id:10}
], 'base');
assert.ok(baseEvidence.includes('Упаковка &lt;img&gt;') && baseEvidence.includes('B1_42'));
assert.ok(baseEvidence.includes('Источник базы не подтверждён') && !baseEvidence.includes('Скрытая база') && !baseEvidence.includes('Не показывать'));
const scriptEvidence = ctx.caAdviceSources([{project:'Картон',step:'Выяснение потребности',quote:'Пример',call_id:8,run_id:2}], 'script');
assert.ok(scriptEvidence.includes('Картон') && scriptEvidence.includes('Выяснение потребности'));
assert.ok(ctx.caAdviceIntro('olga').includes('Работа Ольги') && ctx.caAdviceIntro('oksana').includes('Работа Оксаны'));
const invalidPersonal = ctx.caAdviceBody({advisor:record,historical_findings:[]},'olga');
assert.ok(invalidPersonal.includes('Персональный разбор пока недоступен') && !invalidPersonal.includes('Первое'));
(async () => {
  const response = (summary, operator) => ({start:'2026-11-11',end:'2026-11-17',filters:{project:'Упаковка',base:'База 1',operator},
    options:{projects:['Упаковка'],bases:['База 1'],operators:[{id:'olga',name:'Ольга'},{id:'oksana',name:'Оксана'}]},
    advisor:{status:'ready',brief:{...personalBrief,summary,operator_review:{...personalBrief.operator_review,script:{...personalBrief.operator_review.script,observation:summary}}}}});
  const filters = {innerHTML:'', isConnected:true}, body = {innerHTML:'', isConnected:true};
  const switchBox = {innerHTML:'', listener:null, addEventListener(_name,fn){this.listener=fn;}};
  const intro = {innerHTML:''};
  const button = {listener:null,addEventListener(_name, fn){this.listener=fn;}};
  const fields = {'[data-advice-start]':{value:'2026-11-11'}, '[data-advice-end]':{value:'2026-11-17'},
                  '[data-advice-project]':{value:'Упаковка'}, '[data-advice-base]':{value:'База 1'}, '[data-advice-apply]':button};
  const pending = [], requests = [], routes = [];
  Object.assign(ctx, {S:{}, setHead:()=>{}, clearTimeout:()=>{}, setTimeout:()=>0,history:{replaceState:(_a,_b,url)=>routes.push(url)},
    qs: obj => '?' + new URLSearchParams(Object.entries(obj).filter(([,v]) => v != null && v !== '')).toString(),
    api: url => {requests.push(url);return new Promise(resolve => pending.push(resolve));},
    $: (selector, root) => root === filters ? fields[selector] : selector === '[data-advice-filters]' ? filters
      : selector === '[data-advice-content]' ? body : selector === '[data-advice-switches]' ? switchBox
      : selector === '[data-advice-intro]' ? intro : null});
  const view = {innerHTML:'', isConnected:true};
  const initial = ctx.caFindingsView(view, '2026-11-11','2026-11-17','olga');
  assert.ok(requests[0].includes('operator=olga'));
  pending.shift()(response('Вывод Ольги','olga'));
  await initial;
  assert.ok(body.innerHTML.includes('Вывод Ольги'));
  const choose = id => switchBox.listener({preventDefault(){},target:{closest:()=>({getAttribute:()=>id})}});
  choose('oksana');
  assert.ok(body.innerHTML.includes('Загружаем данные') && !body.innerHTML.includes('Вывод Ольги'));
  assert.ok(requests[1].includes('operator=oksana') && requests[1].includes('project=') && requests[1].includes('base='));
  assert.ok(requests[1].includes('start=2026-11-11') && requests[1].includes('end=2026-11-17'));
  assert.equal(routes[0], '#callcenter/:analysis:operator:oksana:2026-11-11:2026-11-17');
  choose('olga');
  assert.ok(requests[2].includes('operator=olga'));
  pending[1](response('Новый вывод Ольги','olga'));
  await new Promise(resolve => setImmediate(resolve));
  pending[0](response('Запоздалый вывод Оксаны','oksana'));
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(body.innerHTML.includes('Новый вывод Ольги') && !body.innerHTML.includes('Запоздалый'));
  choose('oksana');
  pending[2](response('Нельзя показывать командный отчёт',''));
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(body.innerHTML.includes('Данные выбранного сотрудника пока недоступны') && !body.innerHTML.includes('Нельзя показывать'));
  process.stdout.write('callanalysis UI assertions passed\n');
})().catch(error => { process.stderr.write(String(error.stack || error)); process.exitCode = 1; });
