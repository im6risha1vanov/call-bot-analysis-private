/* Local API for the SAME CRM views. No fetch, DB, provider, or production fallback.
   A workspace entry is an opaque, non-authorizing ID supplied with the HTML.
   Only sessionStorage is used; an explicit new entry starts a clean classroom. */
(function (root) {
  'use strict';
  const E = root.AveronLearningEngine;
  const copy = x => JSON.parse(JSON.stringify(x));
  const number = id => '0000000000' + id.slice(-1);
  const ext = id => id === 'boris' ? '102' : '101';
  const manager = value => value === '102' ? 'boris' : value === '101' ? 'anna' : '';
  const labels = {no_answer:'Не взял трубку',callback:'Перезвонить',not_interested:'Отказ',meeting:'Назначил встречу',proposal:'Отправил КП',interested:'Интерес, думает',bad_number:'Неверный номер'};
  const ordinal=['один','два','три','четыре','пять','шесть','семь','восемь','девять'];
  function programme(context) {
    const tabs=copy(context.programme||[]).filter(t=>!t.module||context.modules.includes(t.module));
    if(!tabs.length)return null;
    const steps=[];
    tabs.forEach((t,i)=>{
      t.start=steps.length;t.label='Урок '+(ordinal[i]||String(i+1))+'. '+t.title;
      steps.push(...t.steps);
      const next=tabs[i+1],page=next?.page||'learning',arg=next?.arg||null;
      const compose=t.compose&&next?.id==='contracts';
      if(t.compose&&!compose)steps.push({action:'close_deal',title:'Закройте карточку сделки крестиком.',hint:'Закройте карточку сделки крестиком.',explain:'Следующий шаг сохранён в истории сделки.',expected:{deal:'targetDeal'},screen:{selector:'.rvm-x'}});
      steps.push({action:'visit_page',title:'Откройте «'+(next?.title||'Обучение')+'».',hint:compose?'В карточке нажмите «Составить договор».':'Откройте «'+(next?.title||'Обучение')+'».',explain:(!t.steps.length?t.description: t.id==='summary'?'Перед переходом посмотрите «Разговоров с результатом»: это цифра Бориса за 7 дней.':t.id==='reports'?'Прочитайте открытый отчёт: рекомендации связаны с выбранным периодом.':t.id==='settings'?'Здесь управляют учётными записями. Для знакомства не создавайте доступ и не меняйте пароль.':'Действие выполнено. '+(next?'Продолжим в следующей вкладке.':'Вернитесь к программе — теперь можно выбирать отдельные уроки.')),expected:{page,...(arg?{arg}:{})},screen:{selector:compose?'[data-contract]':'#nav [data-go="'+page+'"]'}});
      t.end=steps.length;
    });
    return {id:'programme_'+context.role,role:context.role,kind:'practice',version:'tabs-1:'+tabs.map(t=>t.id).join(','),available:true,title:'Обучение по вкладкам',result:'Обучение пройдено. Теперь доступны отдельные уроки по вкладкам.',guide:[{page:tabs[0].page,arg:tabs[0].arg||null}],tabs,steps};
  }
  class Classroom {
    constructor(context, storage) {
      if (!context || !context.entry || !['manager','director'].includes(context.role) || !context.scope) throw new Error('Откройте учебную учётку из раздела «Обучение».');
      this.context = copy(context); this.programme=programme(this.context); this.storage = storage; this.page = 'learning'; this.arg = null; this.changed = () => {};
      this.prefix = 'averon-classroom:v2:' + context.scope + ':' + context.role + ':';
      this.key = this.prefix + context.entry;
      // Delete only this account/company/role's older entries, never CRM preferences.
      for (let i = storage.length - 1; i >= 0; i--) { const key = storage.key(i); if (key.startsWith(this.prefix) && key !== this.key) storage.removeItem(key); }
      const raw = storage.getItem(this.key);
      this.state = raw ? JSON.parse(raw) : {version:2,entry:context.entry,scope:context.scope,role:context.role,active:null,paused:false,attempts:{},guides:{},prefs:{},drafts:{}};
      if (this.state.version !== 2 || this.state.entry !== context.entry || this.state.scope !== context.scope || this.state.role !== context.role) throw new Error('Учебный вход изменился. Войдите в учебную учётку заново.');
      for (const [id, attempt] of Object.entries(this.state.attempts)) {
        const lesson = this.lesson(id);
        if (!lesson?.available || !E.restore(attempt, lesson, context)) delete this.state.attempts[id];
      }
      if (this.state.active && !this.lesson(this.state.active)?.available) this.state.active = null;
      this.state.course ||= {completed:false,snapshots:{}};
      if(this.programme&&!this.state.attempts[this.programme.id])this.state.course={completed:false,snapshots:{}};
      if(!this.state.attempts[this.programme?.id]?.completed)this.state.course.completed=false;
      this.state.presenters ||= {}; this.state.previews ||= {}; this.state.showDrafts ||= {};
      for(const [id,p] of Object.entries(this.state.presenters)) {
        const a=this.state.attempts[id],l=this.lesson(id);
        if(!a||p.attemptId!==a.id){delete this.state.presenters[id];continue;}
        if(!Number.isInteger(p.cursor)||p.cursor<0||p.cursor>a.step){p.cursor=a.step;p.pending=null;}
        if(p.pending&&(!E.restore(p.pending.before,l,context)||p.pending.before.id!==a.id||p.pending.before.step!==p.cursor||a.step!==p.cursor+1)){p.pending=null;p.cursor=a.step;}
      }
      if(this.state.active&&this.state.mode!=='show'&&this.lesson(this.state.active)?.kind==='practice'&&!this.state.attempts[this.state.active])this.state.active=null;
      this.base = E.fixtures(7, context.role, context.clock);
      this.save();
    }
    lesson(id) {
      if(this.programme&&id===this.programme.id)return this.programme;
      if(this.programme&&id==='replay_'+this.context.role){
        const p={...this.programme,steps:[...this.programme.steps]},limit=this.state?.course?.limit;
        if(limit){const st=p.steps[limit-1];p.steps[limit-1]={...st,hint:'Вернитесь во вкладку «Обучение».',title:'Вернитесь во вкладку «Обучение».',expected:{page:'learning'},screen:{selector:'#nav [data-go="learning"]'},explain:'Урок пройден. В программе можно повторить любую другую вкладку.'};
          const tab=this.programme.tabs.find(t=>t.end===limit);
          if(tab?.compose&&this.context.modules.includes('contracts'))p.steps[limit-1]={action:'close_deal',hint:'Закройте карточку сделки крестиком.',title:'Закройте карточку сделки крестиком.',expected:{deal:'targetDeal'},screen:{selector:'.rvm-x'},explain:'Заметка сохранена. Вернёмся к программе.'};
        }
        return p;
      }
      return this.context.lessons.find(l=>l.id===id&&l.role===this.context.role);
    }
    inProgramme() { return !!this.programme&&[this.programme.id,'replay_'+this.context.role].includes(this.state.active); }
    courseDone() { return !!this.state.attempts[this.programme?.id]?.completed; }
    tab() { const s=this.current();return this.programme?.tabs.find(t=>s&&s.step>=t.start&&s.step<t.end)||this.programme?.tabs.at(-1); }
    finished() { return !!this.current()&&(this.current().completed || (this.inProgramme()&&this.state.active.startsWith('replay_')&&this.current().step>=this.state.course.limit)); }
    startCourse() {
      if(!this.programme)throw new Error('Программа недоступна. Обновите тестовый стенд.');
      this.start(this.programme.id,false,{manual:false});delete this.state.presenters[this.state.active];
      this.state.course.limit=null;this.checkpoint();this.save();return this.tab();
    }
    startTab(id) {
      if(!this.courseDone())throw new Error('Сначала пройдите всю программу.');
      const t=this.programme.tabs.find(t=>t.id===id),base=t&&this.state.course.snapshots[id];
      if(!t||!E.restore(base,this.programme,this.context))throw new Error('Урок недоступен или сохранённая ситуация отсутствует.');
      const key='replay_'+this.context.role,s=copy(base),seed=root.crypto.getRandomValues(new Uint32Array(1))[0];
      s.id=contextId(this.context.entry,seed);s.feedback='';
      if(['summary','calls','callbacks'].includes(t.id)){s.ui.manager='';s.ui.period='today';}
      if(t.id==='calls')s.ui.selectedCall=null;
      if(t.id==='callbacks')s.ui.selectedTask=null;
      if(t.id==='deals'){s.ui.selectedDeal=null;s.ui.dealFilter=null;}
      if(t.id==='reports')s.ui.selectedReport=null;
      if(t.id==='staff'){s.ui.selectedPerson=null;s.ui.selectedCall=null;}this.state.attempts[key]=s;this.state.drafts[key]={};delete this.state.presenters[key];
      this.touchedStep=null;this.state.active=key;this.state.paused=false;this.state.mode='practice';this.state.course.limit=t.end;this.save();this.changed();return t;
    }
    checkpoint() {
      if(!this.inProgramme()||this.state.active!==this.programme.id)return;
      const a=this.current(),t=this.programme.tabs.find(t=>t.start===a.step);
      if(t&&!this.state.course.snapshots[t.id])this.state.course.snapshots[t.id]=copy(a);
      this.state.course.completed=a.completed;
    }
    current() { return this.state.attempts[this.state.active] || null; }
    data() { return this.showing() ? this.base : this.current()?.data || this.base; }
    showing() { return this.state.mode === 'show' || this.lesson(this.state.active)?.kind === 'guide'; }
    guide() { return this.lesson(this.state.active)?.kind === 'guide' ? this.state.guides[this.state.active] : this.state.previews[this.state.active]; }
    presenter() { return this.state.presenters[this.state.active] || null; }
    visibleStep() { return this.presenter()?.manual ? this.presenter().cursor : this.current()?.step; }
    awaiting() { return !this.showing()&&!!this.presenter()?.pending; }
    touch() { if(this.showing())return;const p=this.presenter();if(p)p.touched=true;this.touchedStep=this.current()?.step;this.save(); }
    confirm(id, cursor, attemptId) {
      const p=this.presenter(),a=this.current();
      if(this.state.paused || this.showing() || this.state.active!==id || !p?.pending || p.cursor!==cursor || a.id!==attemptId)return false;
      p.cursor=a.step;p.pending=null;p.touched=false;a.feedback='';this.save();this.changed();return true;
    }
    editAnswer() {
      if(this.showing())return;
      const p=this.presenter();
      if(p?.pending && this.lesson(this.state.active).steps[p.cursor].quiz){this.state.attempts[this.state.active]=p.pending.before;p.pending=null;p.touched=true;this.save();this.changed();}
    }
    save() {
      try { this.storage.setItem(this.key, JSON.stringify(this.state)); }
      catch (e) { throw new Error('Не удалось сохранить учебные данные в браузере. Разрешите хранилище; рабочая CRM не используется.'); }
    }
    status(id) { const l=this.lesson(id),s=this.state.attempts[id]; return l?.kind==='guide' ? this.state.guides[id] ? this.state.guides[id].complete ? 'Пройден':'В процессе' : 'Не начат' : s ? s.completed&&!this.state.presenters[id]?.pending?'Пройден':'В процессе':'Не начат'; }
    start(id, repeat=false, options={}) {
      const lesson = this.lesson(id);
      if (!lesson?.available) throw new Error('Урок недоступен вашей роли или модулю компании.');
      const mode=lesson.kind==='guide'||options.mode==='show'?'show':'practice';
      if (mode==='practice' && (!this.state.attempts[id] || repeat)) {
        const seed = root.crypto.getRandomValues(new Uint32Array(1))[0];
        this.state.attempts[id] = E.start(lesson,this.context,this.context.clock,contextId(this.context.entry,seed),seed);
        this.state.drafts[id] = {};
      }
      if (lesson.kind === 'guide' && (!this.state.guides[id] || repeat)) this.state.guides[id]={index:0,seen:[],complete:false};
      if (lesson.kind==='practice' && mode==='show' && (!this.state.previews[id]||repeat))this.state.previews[id]={index:0,seen:[],complete:false};
      if(mode==='practice' && options.manual) {
        const a=this.state.attempts[id],p=this.state.presenters[id];
        if(repeat||!p||p.attemptId!==a.id){this.state.presenters[id]={manual:true,attemptId:a.id,cursor:a.step,pending:null,touched:false};a.feedback='';}
      }
      this.state.active=id; this.state.mode=mode; this.state.paused=false; this.save(); this.changed(); return lesson;
    }
    pause() { this.state.paused=true; this.save(); this.changed(); }
    action(action, strict=false) {
      const lesson=this.lesson(this.state.active),p=this.presenter();
      let s=this.current();
      if(this.showing()) {if(strict)throw new Error('В режиме показа сохранение отключено. Выберите «Попробовать самому» в программе.');return false;}
      if(this.state.paused || !s) {if(strict)throw new Error('Продолжите урок через «Обучение».');return false;}
      if(p?.manual && p.pending) {
        const reversible=['set_manager','set_period','filter_deals','select_client','select_deal','select_task','select_call','select_person','select_report','answer_review','answer_metric','answer_task','answer_stalled'];
        if(action.type===lesson.steps[p.cursor].action && reversible.includes(action.type)) {
          if(JSON.stringify(action)===JSON.stringify(p.pending.action))return true;
          s=p.pending.before;
        } else {if(strict)throw new Error('Этот шаг выполнен. Нажмите «Далее →».');return false;}
      }
      if(this.finished()){if(strict)throw new Error('Урок уже пройден. Для новой попытки выберите «Пройти заново».');return false;}
      const result=E.apply(lesson,this.context,s,action);
      // Initial/background reads must not show errors before any user choice.
      if(!result.ok&&!strict&&((p?.manual&&!p.touched)||(this.inProgramme()&&this.touchedStep!==s.step)))return false;
      this.state.attempts[this.state.active]=result.state;this.checkpoint();
      if(p?.manual) {
        if(result.advanced)p.pending={from:s.step,before:copy(s),action:copy(action)};
        else if(s.step===p.cursor)p.pending=null;
      }
      this.save();this.changed();
      if(strict && !result.ok)throw new Error(result.feedback);
      return result.ok;
    }
    observe(type, values={}) {
      const l=this.lesson(this.state.active),s=this.current();
      if (s && !this.finished() && !this.showing() && ((!s.completed && l.steps[s.step].action===type) || (this.presenter()?.pending && l.steps[this.visibleStep()].action===type))) return this.action(type==='open_section'?{type:'navigate',...values}:{type,...values});
      return false;
    }
    navigation(page,arg=null) {
      this.page=page;this.arg=arg;const a=this.current();
      if(!a||this.state.paused||this.finished()||this.showing()||this.awaiting())return;
      if(this.inProgramme()){
        const st=this.lesson(this.state.active).steps[a.step];
        if(page==='learning'&&!(st.action==='visit_page'&&st.expected.page==='learning')){this.pause();return;}
        this.observe('visit_page',{page,arg});return;
      }
      const section=page==='deals'&&arg==='contracts'?'contracts':page;
      if(['queue','callbacks','deals','contracts','calls','reports','summary','staff'].includes(section))this.action({type:'navigate',section});
    }
    draft(key,value) { if(!this.state.active)return; ((this.showing()?this.state.showDrafts:this.state.drafts)[this.state.active] ||= {})[key]=String(value).slice(0,2000);this.save(); }
    drafts() { return (this.showing()?this.state.showDrafts:this.state.drafts)[this.state.active] || {}; }
    pref(key, value, write=false) { if(write){this.state.prefs[key]=value;this.save();}return this.state.prefs[key]; }
    readonly(page) { return !['queue','callbacks','deals'].includes(page); }
    name(id) { return this.data().managers.find(m=>m.id===id)?.name || 'Учебный сотрудник'; }
    client(value) { return this.data().clients.find(c=>number(c.id)===value || c.id===value); }
    person(value) { const id=manager(value)||value;return this.data().managers.find(m=>m.id===id); }
    owned(m) { return this.context.role==='director' || m==='anna'; }
    requireDirector() { if(this.context.role!=='director')throw new Error('Раздел доступен директору.'); }
    requireModule(m) { if(!this.context.modules.includes(m))throw new Error('Модуль недоступен компании.'); }
    task(t) { return {...t,number:number(t.client),title:this.client(t.client).company,ext:ext(t.manager),manager:this.name(t.manager),label:'Перезвон',source:'crm',who:this.name(t.manager),late:t.due_date<this.context.clock.today}; }
    deal(d) { const notes=d.history.filter(h=>h.kind==='note');return {...d,phone:number(d.client),number:number(d.client),ext:ext(d.manager),manager:this.name(d.manager),stage_no:d.stage==='contract'?3:2,stage_name:d.stageName,status:'active',stalled_days:d.stalled?10:null,last_at:d.history.at(-1).at+'T12:00:00+03:00',waiting:null,advice:notes.at(-1)?.text || '',missing:[],can_pay:false,reasons:{},contracts:this.data().documents.filter(doc=>doc.deal===d.id).map(doc=>({type_title:'Учебный договор',num:doc.number,date:this.context.clock.today})),timeline:d.history.map(h=>({type:'event',at:h.at+'T12:00:00+03:00',title:h.kind==='note'?'Заметка':'Отправлены материалы',text:h.text}))}; }
    call(c) { return {...c,number:number(c.client),phone:number(c.client),ext:ext(c.manager),manager:this.name(c.manager),at:c.at+'T12:00:00+03:00',incoming:false,reviewed:true,rec:false,kind:'Исходящий',result:c.answer==='meeting_agreed'?'result':'lost',text:c.conclusion}; }
    metric(m,period) {const v=this.data().metrics[period]?.[m];if(!v)throw new Error('Выберите доступный период.');return {ext:ext(m),name:this.name(m),dials:v.dials,live:v.result+15,result:v.result,meet:3,seconds:2400,lost:4,mistake:null};}
    review(id) {
      const c=this.data().calls.find(c=>c.id===id&&this.owned(c.manager));if(!c)throw new Error('Учебный разговор недоступен.');
      this.observe('select_call',{call:c.id});
      return {...this.call(c),at:c.at,success:c.answer==='meeting_agreed',outcomes:c.answer==='meeting_agreed'?['Встреча']:[],chance:'normal',misses:[],evidence:[],positives:[],facts:[],next_step:'',summary:c.conclusion,details:'Учебный пример. Дата следующего разговора не согласована.',can_train:false,transcript:c.transcript.map((text,i)=>({at:null,timecode:'',who:i%2?'Клиент':'Менеджер',text:text.replace(/^[^:]+:\s*/, '')}))};
    }
    report(r) {return {...r,title:r.title+' · '+r.from+' — '+r.to,who:this.name(r.manager),at:r.to+'T18:00:00+03:00',note:'Учебный пример',html:'<p>'+safe(r.text)+'</p>',calls:r.calls.map(id=>({id,label:'Учебный разбор'}))};}
    restoreContractDeal() {
      const a=this.current(),l=this.lesson(this.state.active);
      if(!a||a.completed||l.steps[a.step].action!=='prepare_contract'||(!this.inProgramme()&&this.drafts().$contractOpened!=='true'))return null;
      const d=a.data.deals.find(d=>d.id===a.ui.selectedDeal);
      return d ? {phone:number(d.client),number:number(d.client),title:d.company,missing:[]} : null;
    }
    async request(path,opts={}) {
      const u=new URL(path,'https://classroom.invalid');
      if(u.origin!=='https://classroom.invalid')throw new Error('Внешние запросы в учебной учётке отключены.');
      const p=u.pathname,q=u.searchParams,b=opts.body||{},method=(opts.method||'GET').toUpperCase();
      const db=this.data(),ctx=this.context,s=this.current();
      if(method!=='GET') {
        if(method!=='POST')throw new Error('Эта операция не входит в учебное задание.');
        if(p==='/api/outcome') {
          const c=this.client(b.number);if(!c)throw new Error('Нужен учебный клиент.');
          if(b.ext&&b.ext!==ext(ctx.role==='manager'?'anna':'boris'))throw new Error('Другой менеджер.');
          this.action({type:'save_outcome',client:c.id,outcome:b.outcome,comment:b.comment,due_date:b.due_date,due_time:b.due_time},true);return {ok:true};
        }
        if(p.startsWith('/api/tasks/')) {
          const id=decodeURIComponent(p.slice(11));if(b.action!=='move')throw new Error('В этом уроке задачу не нужно закрывать или отменять.');
          const t=db.tasks.find(t=>t.id===id&&this.owned(t.manager));if(!t)throw new Error('Задача недоступна.');
          this.action({type:'move_task',task:id,due_date:b.due_date,due_time:b.due_time},true);return {ok:true};
        }
        const event=/^\/api\/deals\/([^/]+)\/event$/.exec(p);
        if(event){const d=db.deals.find(d=>number(d.client)===decodeURIComponent(event[1])&&this.owned(d.manager));if(!d||b.kind!=='note')throw new Error('В уроке нужно сохранить заметку в нужной сделке.');this.action({type:'save_note',deal:d.id,note:b.note},true);return {ok:true};}
        if(p==='/api/contracts/register') {
          this.requireModule('contracts');const d=db.deals.find(d=>number(d.client)===b.deal&&this.owned(d.manager));
          if(!d)throw new Error('Откройте договор из нужной сделки.');
          this.action({type:'prepare_contract',deal:d.id,company:b.client,inn:b.inn,confirmed:true},true);
          const doc=this.data().documents.find(d=>d.id===this.current().createdDocument);return {num:doc.number,learning:true};
        }
        throw new Error('В учебной учётке отключены звонки, отправки, поиск, права, настройки и внешние сервисы.');
      }
      if(p==='/api/me')return {name:ctx.role==='manager'?'Анна Учебная':'Учебный директор',company:'Учебная компания',role:ctx.role,ext:ctx.role==='manager'?'101':'',test:true,learning:true,readonly:false,modules:ctx.modules,can_dial:false,privileged:ctx.role==='director',team:db.managers.map(m=>({ext:ext(m.id),name:m.name})),outcomes:labels,needs_date:['callback','meeting'],lead_status:{},platform:false,switcher:false};
      if(p==='/api/learning/context') {
        if(q.has('lesson')&&!this.lesson(q.get('lesson'))?.available)throw new Error('Урок недоступен.');return copy(ctx);
      }
      if(p==='/api/queue')return {today:(s?.clock||ctx.clock).today,later:0,items:[db.clients[1],db.clients[0],db.clients[2]].map((c,i)=>({kind:'lead',id:i+1,number:number(c.id),title:c.company+' · '+c.code,note:'Вымышленный клиент. Звонить не требуется.',status:'new',attempts:0}))};
      if(p==='/api/card'){
        const c=this.client(q.get('number'));if(!c)throw new Error('Нужна учебная карточка.');
        if(c.id==='client-1')this.observe('select_client',{client:c.id});
        const outcomes=db.outcomes.filter(o=>o.client===c.id).map(o=>({...o,label:labels[o.outcome],at:ctx.clock.today+'T12:05:00+03:00',who:this.name(ctx.role==='manager'?'anna':'boris')}));
        return {calls:db.calls.filter(v=>v.client===c.id&&this.owned(v.manager)).map(v=>({...this.call(v),who:this.name(v.manager),review:{manager_text:v.conclusion}})),outcomes,tasks:db.tasks.filter(t=>t.client===c.id&&this.owned(t.manager)).map(t=>this.task(t))};
      }
      if(p==='/api/callbacks'&&this.page==='callbacks'&&q.get('ext'))this.observe('set_manager',{manager:manager(q.get('ext'))});
      if(p==='/api/callbacks')return {today:(s?.clock||ctx.clock).today,items:db.tasks.filter(t=>this.owned(t.manager)&&(!q.get('ext')||ext(t.manager)===q.get('ext'))).map(t=>this.task(t))};
      if(p==='/api/journal'){
        const per=q.get('period')||'today',m=manager(q.get('ext'));
        if(this.page==='calls'){this.observe('set_manager',{manager:m});this.observe('set_period',{period:per});}
        const items=db.calls.filter(c=>this.owned(c.manager)&&(!m||c.manager===m)&&(per!=='today'||c.at===(s?.clock||ctx.clock).today)&&(per!=='7'||c.id!=='call-3')&&(!q.get('q')||c.company.includes(q.get('q'))||number(c.client).includes(q.get('q')))).map(c=>this.call(c));
        return {items,more:false,offset:items.length,results:{result:'Результат',lost:'Потерян, шанс был'},kinds:{},today:ctx.clock.today};
      }
      if(p.startsWith('/api/review/'))return this.review(decodeURIComponent(p.slice(12)));
      if(p==='/api/summary'){
        this.requireDirector();const per=q.get('period')||'today',m=manager(q.get('ext'));this.observe('set_period',{period:per});if(m)this.observe('set_manager',{manager:m});
        const rows=(m?[m]:['anna','boris']).map(m=>this.metric(m,per));const total=rows.reduce((a,r)=>{for(const k of ['dials','live','result','meet','seconds','lost'])a[k]=(a[k]||0)+r[k];return a;},{});
        return {today:ctx.clock.today,now:'12:00',period:per,team:db.managers.map(m=>({ext:ext(m.id),name:m.name})),cur:total,prev:{dials:0},vs:'',hourly:false,series:[],heat:[],heat_days:30,missed:[],managers:rows,live_seconds:10};
      }
      if(p==='/api/deals') {
        const stages=[{name:'Контакт',reasons:[],reached:2,lost:0},{name:'Telegram или почта',reasons:[],reached:1,lost:0},{name:'Договор',reasons:[],reached:1,lost:0}];
        return {stages,reasons:{},can_pay:false,managers:db.managers.map(m=>({ext:ext(m.id),name:m.name})),deals:db.deals.filter(d=>this.owned(d.manager)&&(!q.get('person')||ext(d.manager)===q.get('person'))).map(d=>this.deal(d)),funnel:{stages,paid:0,paid_sum:0},unlinked:[],unlinked_chats:[]};
      }
      if(p.startsWith('/api/deals/')){const d=db.deals.find(d=>number(d.client)===decodeURIComponent(p.slice(11))&&this.owned(d.manager));if(!d)throw new Error('Учебная сделка недоступна.');this.observe('select_deal',{deal:d.id});return this.deal(d);}
      if(p==='/api/contracts'){this.requireModule('contracts');this.observe('open_section',{section:'contracts'});return {items:db.documents.map(d=>({...d,num:d.number,phone:number(db.deals.find(x=>x.id===d.deal).client),type_title:'Учебный договор',date:ctx.clock.today,by:'Учебный сотрудник',created_at:ctx.clock.started_at}))};}
      if(p==='/api/contracts/config'){this.requireModule('contracts');return {ourEmail:'training@example.invalid',dsTariffs:[],innLookup:false,templates:{}};}
      if(p==='/api/contracts/next-number'){this.requireModule('contracts');return {num:'УЧ-'+String(db.documents.length+1)};}
      if(p==='/api/reports')return {items:db.reports.filter(r=>this.owned(r.manager)).map(r=>this.report(r))};
      if(p==='/api/staff'){this.requireDirector();return {items:db.managers.map(m=>({ext:ext(m.id),name:m.name,role:'manager',role_title:'Менеджер',linked:false,crm_links:0,kitchen:false}))};}
      if(p==='/api/person'){
        const m=this.person(q.get('person')||'101');if(!m||!this.owned(m.id))throw new Error('Сотрудник недоступен.');
        // Only opening the full employee page counts; background loss hints don't.
        if(this.page==='staff'&&this.arg===ext(m.id))this.observe('select_person',{manager:m.id});
        const stats={talks:17,success:4,hot:2};return {ext:ext(m.id),name:m.name,role_title:'Менеджер',manager:true,kitchen:false,access:null,dials:{week:{dials:80},prev:{dials:60},month:{dials:180}},periods:{week:stats,prev:{talks:10,success:2}},memory:[],memory_on:false,mistakes:[{label:'Дата продолжения не согласована',month:4,examples:[{id:(this.state.active==='d5'||this.current()?.ui.selectedReport==='report-weekly')?'call-weekly':'call-1',at:ctx.clock.week_end}]}],positives:[]};
      }
      if(p==='/api/access'){this.requireDirector();return {invites:[],directors:[]};}
      if(p==='/api/prompts'){this.requireDirector();return {kinds:[{kind:'review',title:'Разбор звонков'}]};}
      if(p==='/api/prompts/review'){this.requireDirector();return {title:'Разбор звонков · учебный пример',body:'Учебный текст. Настройки здесь не изменяются.',source:'учебный пример',versions:[],history:[]};}
      if(p==='/api/automations'){this.requireDirector();this.requireModule('automations');return {automations:[],accounts:[],usage:{runs:0,active:0},catalog:{templates:[],labels:{},email_ready:false}};}
      if(p==='/api/robot/cab'){this.requireDirector();this.requireModule('robot');return {home:true,role:'director',owner:'',owners:[],enabled:true,readonly:true,operators:[],can_edit:false};}
      if(p==='/api/robot/cab/campaigns')return {items:[]};
      if(p==='/api/gpt')return {items:[],models:[]};
      throw new Error('Эта операция недоступна в учебной учётке. Рабочий сервер не вызывается.');
    }
  }
  function safe(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
  function contextId(entry,seed){return entry.slice(0,12)+'-'+seed.toString(16);}
  root.AveronClassroom={Classroom,number,ext,manager};
}(this));
