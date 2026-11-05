/* Fictional working-mode API. No production database, providers or paid calls. */
'use strict';
const {model,clock}=require('./learning_workspace_test.cjs');
function working(role='manager'){
 const base=model(role),original=base.request.bind(base),events=[],reads=[],failures=[];
 base.context.modules.push('sources','callcenter');
 const request=async(path,opts={})=>{
  const u=new URL(path,'https://fake.invalid'),p=u.pathname,b=opts.body||{},method=opts.method||'GET';
  if(method!=='GET'){
   events.push({path:p,method,body:JSON.parse(JSON.stringify(b))});
   if(p==='/api/outcome'){
    base.base.outcomes.push({id:'working-outcome',client:'client-1',outcome:b.outcome,comment:b.comment,due_date:b.due_date,due_time:b.due_time});
    if(b.outcome==='callback')base.base.tasks.push({id:'working-task',client:'client-1',manager:role==='manager'?'anna':'boris',due_date:b.due_date,due_time:b.due_time,original_date:b.due_date,original_time:b.due_time,note:b.comment});
    return {ok:true};
   }
   if(p==='/api/tasks/working-task'){Object.assign(base.base.tasks.find(t=>t.id==='working-task'),{due_date:b.due_date,due_time:b.due_time});return {ok:true};}
   throw Error('A test attempted an unrelated write: '+p);
  }
  reads.push(p);
  if(p==='/api/me')return {...await original(path),learning:false,test:false,readonly:false,callcenter_mode:'db',timezone:'Europe/Moscow'};
  if(p==='/api/callcenter')return {db:true,total:{called:0,total:0,reached:0,interested:0,sum:0},days:[],bases:[],sources:[],cur:{first:0,repeat:0,dials:0,live:0,interested:0,callback:0,no_answer:0,sum:0},prev:{},clients:[],callbacks:[],series:[],today:clock.today};
  if(p==='/api/callcenter/work')return {items:[],total:0,clients:[],prices:{first:0,repeat:0,interested:0}};
  if(p==='/api/cc/queue')return {items:[],today:clock.today,clients:[]};
  if(p==='/api/donors')return {ready:{ai:false,search:false},limit:{spent_rub:0,day_rub:100,run_rub:10},targets:[20],sites_models:[],runs:[],can_settings:false};
  if(p==='/api/leads')return {items:[],total:0};
  if(p==='/api/sources/selections')return {items:[]};
  if(p==='/api/learning/context')return base.context;
  try{return await original(path,opts);}catch(error){failures.push({path:p,message:error.message});throw error;}
 };
 return {request,base,events,reads,failures};
}
module.exports={working};
