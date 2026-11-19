const assert = require('assert');
const path = require('path');
const {chromium} = require(process.env.PLAYWRIGHT_CORE || 'playwright-core');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe'});
 for(const width of [1200,390]){
  const page=await browser.newPage({viewport:{width,height:800}});
  await page.setContent('<main id="view"></main>');
  await page.addStyleTag({path:path.resolve('crm/static/app.css')}); await page.addStyleTag({path:path.resolve('crm/static/team-pilot.css')});
  await page.addScriptTag({content:`var VIEWS={}, S={me:{capabilities:{team_pilot:true,team_day:true,team_actor_id:1}}};
   var empty=false, calls=[], task={id:7,title:'Тестовое поручение',description:'Описание',creator_id:1,assignee_id:2,status:'assigned',version:1};
   var meeting={id:4,series_id:'weekly',state:{title:'Планёрка'},protocol_version:0,protocol:{},items:[{id:3,section:'decisions',text:'Решение',evidence_ids:['u-1']}]};
   var esc=x=>String(x).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
   async function go(p,a){ let n=document.createElement('main');n.id='view';document.querySelector('#view').replaceWith(n);if(p==='team-pilot')await VIEWS[p](n,a); }
   async function api(p,o={}) { calls.push({p,o}); if(p==='/api/team/meetings')return {meetings:empty?[]:[{id:4,state:{title:'Планёрка'},source_updated_at:'2026-11-19T00:00:00Z'}]};
    if(p==='/api/team/meetings/4')return meeting;if(p.includes('/snippets'))return {utterances:[{text:'Фрагмент'}]};
    if(p.endsWith('/people'))return {people:[{id:2,display_name:'Евгений'}]};
    if(p.includes('/protocol')) {meeting.protocol=o.body.protocol;meeting.protocol_version++;return {version:meeting.protocol_version};}
    if(p.includes('/agenda?'))return {assignments:[task],accepted:[],open_questions:[]};
    if(p.includes('/history'))return {history:[{version:1,action:'create',snapshot:task}]};
    if(p==='/api/team/assignments/7' && o.method==='PATCH'){task={...task,...o.body,version:2};return {assignment:task};}
    if(p==='/api/team/assignments/7')return {assignment:task};if(p.startsWith('/api/team/assignments?'))return {assignments:[task]};
    if(p==='/api/personal/day')return {date:'2026-11-19',timezone:'Europe/Moscow',today:[{...task,kind:'team'}],overdue:[],blocked:[],unscheduled:[]};
    if(p==='/api/personal/day/settings')return {settings:{version:0,work_start:'09:00',work_end:'18:00',priorities:[],morning_time:null,evening_time:null}};
    throw Error('Unexpected '+p); }`});
  await page.addScriptTag({path:path.resolve('crm/static/team-pilot.js')});
  await page.evaluate(()=>{empty=true;return go('team-pilot','meetings')}); await page.getByText('Встреч пока нет.').waitFor();
  await page.evaluate(()=>{empty=false;return go('team-pilot','meetings')}); await page.getByText('Планёрка').click();
  await page.locator('[name=text]').fill('Изменённый протокол'); await page.locator('[data-form=protocol] button').click(); await page.getByText('Протокол сохранён.').waitFor();
  await page.getByText('Фрагменты источника').click(); await page.getByText('Фрагмент',{exact:true}).waitFor();
  await page.getByText('Повестка следующей встречи').click(); await page.getByText('Тестовое поручение').click();
  await page.locator('[name=title]').fill('Обновлённое поручение');await page.locator('[data-form=assignment] button').click();await page.getByRole('heading',{name:'Обновлённое поручение'}).waitFor();
  await page.getByText('Мой день',{exact:true}).click(); await page.getByText('Календарь не подключён.',{exact:false}).waitFor();
  const info=await page.evaluate(()=>({calls,overflow:document.documentElement.scrollWidth>innerWidth}));assert(!info.overflow,`horizontal overflow at ${width}`);assert(info.calls.some(c=>c.p.endsWith('/protocol')&&c.o.body.version===0));assert(info.calls.some(c=>c.o.method==='PATCH'&&c.o.body.version===1&&c.o.idempotencyKey));
  await page.close();console.log(`pilot smoke ${width}px passed`);
 }
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
