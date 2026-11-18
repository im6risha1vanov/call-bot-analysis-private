const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const actions = [];
const elements = {};
let html = '';
function element(id) { return elements[id] ||= {disabled:false, textContent:'', events:{}, addEventListener(k, f) {this.events[k]=f;}}; }
const ctx = {window:{}, VIEWS:{}, localStorage:{getItem:()=>null}, store:{set(){}},
  esc:v => String(v ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),
  qs:o => '?' + new URLSearchParams(o), api: async (url, options) => {actions.push([url,options]); return {ok:true};},
  modal:s => {html=s; return {close(){}};}, modalHead:(a,b='') => a+b,
  $:id => element(id), $$:()=>[], toast:()=>{}, reload:()=>{}, prompt:()=>null,
  console, URLSearchParams};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('crm/static/robot.js','utf8') + '\nthis.setup = () => {RB.st={readonly:false}; RB.owner="owner";}; this.review=replyReview; this.edit=replyEditForm;', ctx);
ctx.setup();
ctx.review({id:7, version:3, question:'<script>', answer:'ответ', has_audio:true});
assert(html.includes('&lt;script>'));
assert(html.includes('id="rp-approve" disabled'));
assert(html.includes('preload="none"'));
(async () => {
 await element('#rp-audio').events.ended();
 assert.equal(actions[0][0], '/api/robot/cab/replies/7/preview');
 assert.equal(actions[0][1].body.version, 3);
 assert.equal(element('#rp-approve').disabled, false);
 await element('#rp-approve').onclick();
 assert.equal(actions[1][0], '/api/robot/cab/replies/7/approve');
 assert.equal(actions[1][1].body.owner, 'owner');
 ctx.edit(9, {id:7, version:3, question:'Сколько стоит?', answer:'Текст', triggers:['Сколько стоит?'], context_terms:['коробки'], contact_terms:[]});
 assert(html.includes('rp-triggers'));
 assert(html.includes('rp-context'));
 element('#rp-question').value='Сколько стоит?'; element('#rp-answer').value='Текст';
 element('#rp-triggers').value='Сколько стоит?\nКакая цена?'; element('#rp-context').value='коробки';
 element('#rp-contact').value=''; element('#rp-action').value='reply';
 await element('#rp-save').onclick();
 assert.equal(actions[2][0], '/api/robot/cab/replies/7/edit');
 assert.equal(actions[2][1].body.triggers.length, 2);
 assert.equal(actions[2][1].body.context_terms[0], 'коробки');
 assert.equal(actions[2][1].body.next_action, 'reply');
 ctx.edit(9, {id:8, version:1, question:'Где?', answer:'Уточнить', fact_needed:true, fact_note:'Адрес'});
 assert(html.includes('rp-fact-checked'));
 element('#rp-fact-checked').checked=true;
 await element('#rp-save').onclick();
 assert.equal(actions[3][1].body.fact_checked, true);
 console.log('reply UI review workflow OK');
})().catch(e => {console.error(e); process.exit(1)});
