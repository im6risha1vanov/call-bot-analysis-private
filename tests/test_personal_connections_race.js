const assert=require('assert'),fs=require('fs'),vm=require('vm');
const handlers={},root={isConnected:true,innerHTML:'',addEventListener:(n,f)=>handlers[n]=f,querySelector:q=>q==='.personal-panel'?{}:null};
let resolveConnections; const pending=new Promise(resolve=>resolveConnections=resolve);
const api=async path=>path.endsWith('/profile')?{profile:{timezone:'Europe/Moscow'}}:path.endsWith('/projects')?{projects:[]}:path.endsWith('/tasks')?{tasks:[]}:pending;
const ctx={VIEWS:{},S:{me:{account_id:1}},esc:String,api,Intl,Date,JSON,Number,String,Array,sessionStorage:{getItem:()=>null},document:{hidden:false},window:{addEventListener:()=>{},removeEventListener:()=>{}},setInterval:()=>1,clearInterval:()=>{}};
vm.runInNewContext(fs.readFileSync('crm/static/personal.js','utf8'),ctx);
(async()=>{await ctx.VIEWS.personal(root);const first=handlers.click({target:{closest:()=>({dataset:{tab:'connections'}})}});await handlers.click({target:{closest:()=>({dataset:{tab:'profile'}})}});const before=root.innerHTML;resolveConnections({connections:{}});await first;assert.equal(root.innerHTML,before);console.log('connections navigation race passed')})().catch(e=>{console.error(e);process.exitCode=1});
