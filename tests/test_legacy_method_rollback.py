"""Synthetic legacy data, in-memory database and fake providers only."""
import asyncio
import copy
import html
import json
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
from tests import test_comparison
from tests.test_extended_review import Pool
from tests.test_combined import ValidHTML
import analysis
from tools import Actor, Forbidden
from unified import legacy_display as legacy, analysis_policy as policy, render, store, ui, overview_cache

def sample(complete=True):
    transcript='\n'.join(['Менеджер: Звоню по поводу привлечения клиентов.',
        'Клиент: Мне удобно получить предложение файлом.',
        'Менеджер: Я пришлю материалы сейчас.',
        'Клиент: Да, отправьте в Telegram.'])
    raw={'criteria':{k:{'applicable':False,'passed':False} for k,_,_ in analysis.CRITERIA},
         'call_cut_short':False,'proposal_sent':True,'signals':{}}
    raw['criteria'].update(call_reason={'applicable':True,'passed':True,'evidence':'Звоню по поводу привлечения клиентов'},
        concrete_commitment={'applicable':True,'passed':False,'evidence':'Время нового контакта не названо'},
        decision_influence={'applicable':True,'passed':False,'evidence':'Участники решения не подтверждены'})
    score, rows=analysis.compute_score(raw)
    return {'id':71,'call_id':123,'client_id':1,'extension':'101','status':'ready',
        'transcript':transcript,'transcript_sha256':'fictional', 'expanded':None,
        'configuration':{**policy.legacy_configuration(),'show_full_phone':True,'transcript_complete':complete},
        'metadata':{'manager':'Учебный менеджер','phone':'+79990000000','duration_seconds':91,
                    'call_started_at':'2026-10-07T10:00:00+03:00','timezone':'Europe/Moscow'},
        'primary_analysis':{'analysis':{**raw,'rows':rows},'score':score,'level':analysis.compute_level(raw,rows),
            'short_report':{'result':'Согласована отправка предложения',
                'good':[{'text':'«Да, отправьте в Telegram» — выбран канал отправки'}],
                'weak':[{'text':'«Я пришлю материалы сейчас» — следующее обсуждение осталось открытым',
                         'should_say':'Когда вам удобно обсудить предложение?'}]},
            'detailed_report':{'for_manager':{'did_well':[], 'moments':[],
                 'practice':'Потренируйте вопрос о времени следующего разговора и дождитесь ответа.'}}},
        'auxiliary':{'combined':{'score':100,'readable_report':{'summary':'Придуман миллионный контракт'}},
                     'course':{'score':100}},'summary':'ordinary report'}

class DisplayTests(unittest.TestCase):
    def test_enqueue_parameters_keep_score_and_verdict_after_global_changes(self):
        row=sample();scores=copy.deepcopy(row['primary_analysis']['analysis'])
        scores['proposal_sent']=False
        package=policy.legacy_configuration()
        expected=policy.evaluate(scores,package)
        with patch.object(analysis,'VERDICT_MIN_FAILURES',1), \
             patch.object(analysis,'CRITERIA',[]), \
             patch.object(analysis,'LEVEL_CRITICAL_CRITERIA',{'decision_influence'}):
            self.assertEqual(expected,policy.evaluate(scores,package))
    def test_original_package_exact_and_three_prompts(self):
        m=policy.verify_original();self.assertEqual(m['source_commit'],'52e4d575c03788d09194346362b1e06ef4476feb')
        self.assertEqual(len(m['files']),5)
    def test_same_primary_score_no_auxiliary_or_history_write(self):
        row=sample();before=copy.deepcopy(row)
        for text in (render.summary(row),render.expanded(row)):
            self.assertIn(f"{row['primary_analysis']['score']}/100",text)
            self.assertNotIn('100/100',text);self.assertNotIn('миллион',text)
            self.assertNotIn('course_',text);self.assertNotIn('combined_',text)
            self.assertNotIn('{',text)
        self.assertEqual(row,before)
    def test_material_time_is_not_callback_time(self):
        for text in (render.summary(sample()),render.expanded(sample())):
            self.assertIn('срок отправки — сейчас',text)
            self.assertIn('Telegram',text)
            self.assertIn('перезвон',text)
            self.assertNotIn('перезвонить сейчас',text)
    def test_incomplete_record_no_assertion_of_absence(self):
        row=sample(False);text=render.expanded(row)
        self.assertIn('доступном фрагменте',text);self.assertIn('Полнота записи',text)
        self.assertNotIn('не задал',text)
    def test_unapplicable_excluded_and_time_share_not_time(self):
        row=sample();row['primary_analysis']['analysis']['rows'].append(
            {'key':'talk_share','passed':True,'applicable':True,'weight':4,'title':'Говорил меньше половины времени'})
        text=render.expanded(row)
        self.assertNotIn('Извлекающие вопросы:',text)
        self.assertNotIn('Говорил меньше половины времени',text)
        self.assertIn('число реплик не измеряет время',text)
    def test_historical_without_selector_uses_historical_format(self):
        row=sample();row['configuration'].pop('display_version')
        self.assertIn('1. Итог звонка',render.expanded(row))
        self.assertNotIn('1. Итог звонка',render.expanded(sample()))
    def test_cache_identity_includes_run_method_display_transcript(self):
        row=sample();key=legacy.cache_key(row)
        for name,val in [('id',72),('transcript_sha256','different')]:
            other=copy.deepcopy(row);other[name]=val;self.assertNotEqual(key,legacy.cache_key(other))
        row['configuration']['methodology_version']='other'
        with self.assertRaises(ValueError):legacy.cache_key(row)
    def test_safe_html_and_length_and_unverified_quote(self):
        row=sample();row['metadata']['manager']='<>&😀'*1000
        row['primary_analysis']['detailed_report']['for_manager']['did_well']=[{'quote':'Несуществующая цитата','what':'Выдумка'}]
        text=render.summary(row);self.assertLessEqual(render.units(text),4000)
        parsed=ValidHTML();parsed.feed(text);self.assertEqual(parsed.stack,[])
        self.assertNotIn('Выдумка',text);self.assertIn('&lt;',text)
    def test_missing_data_no_score_or_fabricated_success(self):
        row=sample();row.update(transcript='');row['primary_analysis'].update(score=None,short_report={})
        for text in (render.summary(row),render.expanded(row)):
            self.assertNotIn('/100',text);self.assertIn('Данных недостаточно',text)
    def test_button_exact_and_short_payload(self):
        button=render.keyboard(71).inline_keyboard[0][0]
        self.assertEqual(button.text,'🔎 Расширенный обзор');self.assertEqual(button.callback_data,'uv:e:71')
    def test_critical_icon_requires_pinned_critical_criterion_and_negative_verdict(self):
        row=sample();row['configuration']['verdict']={'critical':['decision_influence']}
        row['primary_analysis']['level']='❌'
        row['primary_analysis']['analysis']['rows'][3]['evidence']='Я пришлю материалы сейчас'
        self.assertIn('🚨',render.summary(row))
        row['primary_analysis']['level']='⚠️'
        self.assertNotIn('🚨',render.summary(row))

class CacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pool=Pool(sample());self.actor=Actor(900,1,'owner','')
        self.patch=patch.object(store,'get_call',AsyncMock(return_value={'id':123}))
        self.patch.start();self.addCleanup(self.patch.stop)
    async def test_simultaneous_overview_one_generation_no_paid_calls(self):
        with patch.object(render,'expanded',wraps=render.expanded) as build, patch.object(analysis,'_call',AsyncMock()) as paid:
            texts=await asyncio.gather(*(store.overview(self.pool,self.actor,71) for _ in range(4)))
        build.assert_called_once();paid.assert_not_awaited();self.assertEqual(len(set(texts)),1)
        key=legacy.cache_key(self.pool.row)
        cache=overview_cache.decode(self.pool.row['expanded']);self.assertEqual(cache['selected_version'],key)
    async def test_rights_manager_head_owner_other_tenant(self):
        for actor in (None,Actor(99,2,'owner',''),Actor(99,1,'manager','102')):
            with self.assertRaises(Forbidden):await store.overview(self.pool,actor,71)
        for actor in (self.actor,Actor(901,1,'head',''),Actor(123,1,'manager','101')):
            self.assertIn('📞 Расширенный разбор',await store.overview(self.pool,actor,71))
    async def test_generation_error_keeps_short_and_retry(self):
        with patch.object(render,'expanded',side_effect=ValueError('fictional')):
            with self.assertRaises(ValueError):await store.overview(self.pool,self.actor,71)
        self.assertIsNone(self.pool.row['expanded']);self.assertEqual(self.pool.row['summary'],'ordinary report')
        self.assertTrue(await store.overview(self.pool,self.actor,71))
    async def test_double_click_and_restart_keeps_receipt(self):
        bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=42)),send_document=AsyncMock())
        await asyncio.gather(*(ui.show(bot,self.pool,self.actor,71,detailed=True) for _ in range(2)))
        content=[c for c in bot.send_message.await_args_list if c.args[1].startswith('📞')]
        self.assertEqual(len(content),1)
        await ui.show(bot,self.pool,self.actor,71,detailed=True)
        self.assertEqual(len([c for c in bot.send_message.await_args_list if c.args[1].startswith('📞')]),1)
    async def test_cache_does_not_change_with_global_selector(self):
        text=await store.overview(self.pool,self.actor,71)
        with patch.object(policy,'snapshot',AsyncMock(side_effect=AssertionError('must not consult global selector'))):
            self.assertEqual(text,await store.overview(self.pool,self.actor,71))

class PolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_callback_ack_actor_and_denied_roles_groups_invalid_action(self):
        from aiogram import Bot,Router
        from aiogram.types import CallbackQuery,Message,User,Chat
        from datetime import datetime,timezone
        router=Router();policy.install(router,lambda:'fake-pool')
        telegram=Bot('123456:synthetic_test_token')
        try:
            for role,chat_type,chat_id,data,allowed in [
                ('owner','private',900,'ap:legacy',True),
                ('head','private',900,'ap:saved_current',True),
                ('manager','private',900,'ap:legacy',False),
                ('owner','group',-100,'ap:legacy',False),
                ('owner','private',900,'ap:invalid',False)]:
                actor=Actor(900,1,role,'101');order=[]
                async def ack(*args,**kwargs):order.append('ack')
                async def send(*args,**kwargs):order.append('send')
                message=Message(message_id=2,date=datetime.now(timezone.utc),chat=Chat(id=chat_id,type=chat_type),from_user=User(id=777,is_bot=True,first_name='Bot'))
                cq=CallbackQuery(id='fake',from_user=User(id=900,is_bot=False,first_name='Person'),chat_instance='fake',data=data,message=message).as_(telegram)
                with patch.object(CallbackQuery,'answer',AsyncMock(side_effect=ack)), \
                     patch.object(Bot,'send_message',AsyncMock(side_effect=send)), \
                     patch.object(policy,'resolve_actor',AsyncMock(return_value=actor)) as resolve, \
                     patch.object(policy,'switch',AsyncMock(return_value=policy.legacy_configuration())) as switch:
                    await router.propagate_event('callback_query',cq)
                    self.assertEqual(order[0],'ack')
                    self.assertEqual(switch.await_count,1 if allowed else 0)
                    if allowed:resolve.assert_awaited_once_with('fake-pool',900)
        finally:await telegram.session.close()
    async def test_switch_idempotent_restore_and_queue_before_after(self):
        from tests.test_legacy_mode import Context
        class DB:
            def __init__(self):
                self.rows={};self.tasks=[{'client_id':1,'input':{'call_id':123}},
                                      {'client_id':2,'input':{'call_id':456}}]
            def acquire(self):return Context(self)
            def transaction(self):return Context(self)
            async def fetchval(self,sql,*args):return True
            async def fetchrow(self,sql,*args):return self.rows.get(args[0])
            async def execute(self,sql,*args):
                if 'UPDATE tasks SET input=jsonb_set' in sql:
                    for task in self.tasks:
                        if task['client_id']==args[0] and 'analysis_package' not in task['input']:
                            task['input']['analysis_package']=json.loads(args[1])
                elif 'INSERT INTO call_analysis_policy' in sql:
                    if args[0] not in self.rows:self.rows[args[0]]={'saved_configuration':args[2]}
                    self.rows[args[0]]['active']=args[1]
                elif "INSERT INTO tasks" in sql:self.tasks.append({'client_id':args[0],'input':json.loads(args[1])})
        db=DB();actor=Actor(900,1,'owner','')
        with patch.object(policy,'resolve_actor',AsyncMock(return_value=actor)):
            cfg=await policy.switch(db,actor,'legacy');saved=copy.deepcopy(db.rows[1]['saved_configuration'])
            self.assertEqual(cfg['display_version'],legacy.VERSION)
            self.assertEqual(db.tasks[0]['input']['analysis_package']['display_version'],render.FORMAT_VERSION)
            self.assertNotIn('analysis_package',db.tasks[1]['input'])
            await policy.enqueue(db,1,124,'fictional-new-call')
            self.assertEqual(db.tasks[-1]['input']['analysis_package']['display_version'],legacy.VERSION)
            await policy.switch(db,actor,'legacy')
            self.assertEqual(saved,db.rows[1]['saved_configuration'])
            restored=await policy.switch(db,actor,'saved_current')
            self.assertEqual(restored['display_version'],render.FORMAT_VERSION)
            self.assertEqual(db.tasks[-1]['input']['analysis_package']['display_version'],legacy.VERSION)
    async def test_switch_revoked_or_manager_denied_before_writes(self):
        from tests.test_legacy_mode import Context
        db=AsyncMock();db.acquire=lambda:Context(db);db.transaction=lambda:Context(db)
        for actor in (None,Actor(123,1,'manager','101')):
            with patch.object(policy,'resolve_actor',AsyncMock(return_value=actor)):
                with self.assertRaises(Forbidden):await policy.switch(db,Actor(900,1,'owner',''),'legacy')
        db.execute.assert_not_awaited()

class DeploymentTests(unittest.IsolatedAsyncioTestCase):
    def test_original_backup_verified_without_db(self):
        from scripts.apply_legacy_method import original_backup
        self.assertEqual(str(original_backup()),policy.BACKUP)
    def test_restart_three_required_services_only_and_check_trainer(self):
        from scripts import apply_legacy_method as deploy
        with patch.object(deploy,'state',return_value='active'), \
             patch.object(deploy.subprocess,'run') as run, \
             patch.object(deploy.subprocess,'check_output',return_value=''), \
             patch.object(deploy.time,'sleep'):
            deploy.restart()
        self.assertEqual([c.args[0] for c in run.call_args_list],
            [['systemctl','restart',name] for name in deploy.UNITS])
        self.assertNotIn(deploy.TRAINER,[c.args[0][-1] for c in run.call_args_list])
    async def test_uninstalled_policy_retains_current_not_random_rollback(self):
        db=AsyncMock();db.fetchval.return_value=False
        self.assertEqual((await policy.snapshot(db,1))['display_version'],render.FORMAT_VERSION)
    async def test_pin_existing_task_never_reads_current_selector(self):
        frozen=policy.legacy_configuration()
        with patch.object(policy,'snapshot',AsyncMock(side_effect=AssertionError('must stay pinned'))):
            self.assertEqual(await policy.pin_task(AsyncMock(),{'id':3},{'analysis_package':frozen}),frozen)
    async def test_legacy_does_not_change_trainer_or_global_flags(self):
        from methodology import runtime
        from automatic import jobs
        before=(runtime.settings(),jobs.enabled());policy.legacy_configuration()
        self.assertEqual(before,(runtime.settings(),jobs.enabled()))
