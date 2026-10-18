"""No production DB/provider calls: synthetic calls, transactional fake and bot."""
import asyncio
import copy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from tests import test_comparison  # Synthetic env only.
from tools import Actor, Forbidden
from unified import extended_review as review, overview_cache, render, store, ui

FRAGMENT = '\n'.join([
    'Менеджер: Понял Вас. А рассчитать экономику и рассмотреть тарифы?',
    'Клиент: Да, было бы удобнее, если бы вы файлом отправили.',
    'Менеджер: Я вам тогда сейчас напишу, что у нас есть.',
    'Клиент: Да, давайте телеграмм. Удобнее по этому номеру привязан.',
])


def sample(transcript=FRAGMENT, complete=False):
    return {'id': 1, 'call_id': 123, 'client_id': 1, 'extension': '101', 'status': 'ready',
            'transcript': transcript, 'transcript_sha256': 'synthetic', 'expanded': None, 'summary': 'ordinary report',
            'metadata': {'manager': 'Марк Амирханян', 'phone': 'номер не указан', 'duration_seconds': 98,
                         'call_started_at': '2026-10-06T10:38:00+03:00', 'timezone': 'Europe/Moscow'},
            'configuration': {'show_full_phone': True, 'transcript_complete': complete,
                              'verdict': {'critical': ['brush_off_handled','decision_influence','no_early_pitch']}},
            'primary_analysis': {'score': 47, 'level': '✅', 'analysis': {'rows': []}, 'short_report': {}},
            'auxiliary': {}}


def grade(row, key, passed, evidence, source='primary', **extra):
    if source == 'primary':
        item={'key':key,'passed':passed,'applicable':True,'evidence':evidence,**extra}
        row['primary_analysis']['analysis']['rows'].append(item)
    else:
        item={'key':key,'status':'passed' if passed else 'failed','evidence':evidence,**extra}
        row['auxiliary'].setdefault(source, {}).setdefault('rows',[]).append(item)


class FactTests(unittest.TestCase):
    def test_rollback_retains_legacy_text_and_does_not_touch_raw_historical_cache(self):
        raw='Исторический обзор'
        self.assertEqual(overview_cache.rollback_text(raw),(False,raw))
        cache=overview_cache.decode(raw);cache['entries'][review.VERSION]={'text':'Новый обзор'}
        self.assertEqual(overview_cache.rollback_text(overview_cache.encode(cache)),(True,raw))
        empty=overview_cache.decode(None);empty['entries'][review.VERSION]={'text':'Новый обзор'}
        self.assertEqual(overview_cache.rollback_text(overview_cache.encode(empty)),(True,None))
    def test_send_now_and_telegram_are_proved_by_different_turns(self):
        model=review.build(sample()); facts=model['facts_by_kind']
        self.assertEqual(facts['materials_deadline'].value,'сейчас')
        self.assertEqual(facts['materials_deadline'].turn_ids,(3,))
        self.assertEqual(facts['channel'].turn_ids,(4,))
        self.assertNotIn('agreed_callback',facts)
        self.assertEqual(facts['materials_agreed'].confirmation,'linked_turns')
        text=render.expanded(sample())
        self.assertIn('срок отправки — сейчас',text)
        self.assertNotIn('перезвонить сейчас',text)
        self.assertIn('не подтверждает намерение купить',text)

    def test_normalization_is_contiguous_not_fuzzy_or_number_changing(self):
        e=review.Evidence(sample('Менеджер: Понял Вас — отправлю файл. Клиент: Да, давайте Telegram.'))
        self.assertTrue(e.locate('понял вас, отправлю файл!'))
        self.assertFalse(e.locate('Понял вас, отправлю договор'))
        self.assertFalse(e.locate('Понял Вас давайте Telegram'))
        self.assertFalse(review.Evidence(sample('Клиент: Получил 50% скидки.')).locate('Получил 50 скидки'))
        self.assertFalse(review.Evidence(sample('Клиент: Цена -100 рублей.')).locate('Цена 100 рублей'))

    def test_three_speakers_do_not_turn_unknown_or_secretary_into_customer(self):
        row=sample('[00:00 Спикер 0] Здравствуйте. [00:03 Спикер 1] Пришлите материалы. [00:06 Спикер 2] Не нужно.')
        row['primary_analysis']['analysis']['roles']='Спикер 0 — менеджер; Спикер 1 — секретарь; Спикер 2 — клиент'
        model=review.build(row)
        self.assertEqual([t['speaker'] for t in model['turns']],['Менеджер','Секретарь','Клиент'])
        self.assertNotIn('materials_request',model['facts_by_kind'])

    def test_production_difficulty_does_not_turn_into_budget(self):
        transcript='Клиент: Проект пока не вышел на стадию реализации, есть сложности с производством.\n'+FRAGMENT
        row=sample(transcript); grade(row,'clarity',True,'Понял Вас')
        row['auxiliary']['combined']={'readable_report':{'summary':'Клиенту не хватает бюджета', 'moments':[]}}
        text=render.expanded(row)
        self.assertIn('производственными трудностями',text)
        self.assertNotIn('не хватает бюджета',text)
        self.assertIn('Причина отсрочки',text)
        self.assertFalse(review.build(sample('Клиент: Проблем с производством нет.'))['facts_by_kind'].get('postponement'))

    def test_channel_quote_does_not_prove_a_deadline(self):
        row=sample(FRAGMENT.replace('сейчас ',''))
        row['auxiliary']['combined']={'next_step':{'deadline':'завтра','evidence':'Да, давайте телеграмм'}}
        text=render.expanded(row)
        self.assertIn('срок отправки не установлен',text)
        self.assertNotIn('срок отправки — завтра',text)

    def test_send_time_and_callback_time_separate_clauses(self):
        transcript='Клиент: Пришлите материалы. Менеджер: Отправлю материалы, перезвоню завтра в 12:00. Клиент: Да, согласен.'
        facts=review.build(sample(transcript))['facts_by_kind']
        self.assertNotIn('materials_deadline',facts)
        self.assertIn('agreed_callback',facts)

    def test_negated_time_and_negated_callback_are_not_agreements(self):
        facts=review.build(sample('Клиент: Пришлите материалы. Менеджер: Отправлю не сейчас, а завтра. Клиент: Хорошо.'))['facts_by_kind']
        self.assertEqual(facts['materials_deadline'].value,'завтра')
        facts=review.build(sample('Менеджер: Не перезвоню завтра. Клиент: Хорошо.'))['facts_by_kind']
        self.assertNotIn('agreed_callback',facts)

    def test_proposal_is_not_consent_and_unknown_roles_remain_unknown(self):
        facts=review.build(sample('Менеджер: Отправлю материалы завтра. Клиент: Мне это не нужно.'))['facts_by_kind']
        self.assertNotIn('materials_agreed',facts)
        facts=review.build(sample('Менеджер: Перезвоню завтра. Клиент: Не надо.'))['facts_by_kind']
        self.assertNotIn('agreed_callback',facts)
        facts=review.build(sample('[00:00 Спикер 0] Отправлю материалы сейчас. [00:10 Спикер 1] Да.'))['facts_by_kind']
        self.assertNotIn('materials_promise',facts)

    def test_counterproposal_does_not_confirm_original_callback_time(self):
        facts=review.build(sample('Менеджер: Перезвоню завтра в 12:00. Клиент: Да, лучше в пятницу.'))['facts_by_kind']
        self.assertIn('callback_proposal',facts)
        self.assertNotIn('agreed_callback',facts)

    def test_absence_not_asserted_when_question_is_in_full_transcript(self):
        row=sample('Менеджер: Кто ещё участвует в решении? Клиент: Директор. '+FRAGMENT,complete=True)
        grade(row,'decision_influence',False,'Понял Вас')
        self.assertNotIn('Не выяснены участники',render.expanded(row))

    def test_deduplication_and_no_artificial_criticality_or_time_share(self):
        row=sample(); grade(row,'concrete_commitment',False,'Понял Вас')
        grade(row,'next_step',False,'Да, давайте телеграмм',source='combined')
        grade(row,'next_step',False,'Да, было бы удобнее',source='course')
        self.assertEqual([i['key'] for i in review.build(row)['improvements']],['next_step'])
        self.assertNotIn('Критичный недостаток',render.expanded(row))

    def test_full_and_fragment_absence_do_not_use_random_quotes(self):
        for complete in (False,True):
            model=review.build(sample(complete=complete)); item=model['improvements'][0]
            self.assertEqual(item['confirmation'],'full_dialogue_absence' if complete else 'fragment_absence')
            self.assertEqual(item['turn_ids'],[1,2,3,4])
            text=render.expanded(sample(complete=complete))
            self.assertIn('Проверен весь' if complete else 'В доступном фрагменте',text)
            self.assertNotIn('менеджер не спросил',text.lower())

    def test_disagreement_about_followup_resolved_from_dialogue_not_majority(self):
        row=sample(FRAGMENT+' Менеджер: Перезвоню в пятницу в 12:00. Клиент: Да, согласен.',complete=True)
        grade(row,'concrete_commitment',False,'Понял Вас')
        grade(row,'next_step',False,'Да, давайте телеграмм',source='course')
        grade(row,'next_step',True,'Да, согласен',source='combined')
        text=render.expanded(row)
        self.assertIn('Следующий разговор согласован',text)
        self.assertNotIn('Не согласован контакт',text)
        self.assertNotIn('Методика курса',text)
        self.assertEqual(text.count('47/100'),1)

    def test_unresolvable_disagreement_is_one_uncertainty(self):
        row=sample(); grade(row,'clarity',True,'Понял Вас')
        grade(row,'clarity',False,'Понял Вас',source='course')
        text=render.expanded(row)
        self.assertIn('проверки расходятся',text)
        self.assertNotIn('Понятное общение: выполнено',text)
        self.assertEqual(text.count('проверки расходятся'),1)

    def test_unapplicable_and_stage_inappropriate_failures_not_dumped(self):
        row=sample(); grade(row,'gatekeeper_passed',False,'Понял Вас',applicable=False)
        grade(row,'sold_meeting',False,'Понял Вас')
        grade(row,'economics',False,'Понял Вас',source='course',status='not_applicable')
        row['auxiliary']['combined']={'classification':{'sales_role':'averon_sales','product':'gck','stage':'support','confidence':.9,'evidence':['Понял Вас']}}
        text=render.expanded(row)
        self.assertIn('Запуск и сопровождение',text)
        self.assertNotIn('пройти секретаря',text.lower())
        self.assertNotIn('назначить встречу',text.lower())

    def test_talk_counts_never_time_and_preserved_score_flagged(self):
        row=sample(); grade(row,'talk_share',True,'Понял Вас')
        row['primary_analysis']['analysis']['manager_talk_share']=46.67
        original=copy.deepcopy(row)
        text=render.expanded(row)
        self.assertIn('Требует проверки',text)
        self.assertIn('число реплик не измеряет длительность',text)
        self.assertNotIn('46.67',text)
        self.assertEqual(row,original)

    def test_empty_catalog_is_not_lie_and_incomplete_not_no_errors(self):
        row=sample(); row['auxiliary']['course']={'commercial_claims':[{'verdict':'unverified','evidence':'Понял Вас'}]}
        text=render.expanded(row)
        self.assertNotIn('соврал',text)
        self.assertNotIn('критичных ошибок',text)

    def test_empty_old_data_no_json_no_fabrication(self):
        row=sample(''); row['primary_analysis']={'score':None,'analysis':{}}
        text=render.expanded(row)
        self.assertIn('1. Итог звонка',text);self.assertIn('5. Договорённости',text)
        self.assertNotIn('/100',text);self.assertNotIn('…',text)

    def test_description_of_example_never_becomes_a_client_quote(self):
        from scripts.build_extended_review_demo import control_row
        row=control_row();text=render.expanded(row);model=review.build(row)
        self.assertIn('В описании примера названы производственные трудности',text)
        self.assertNotIn('Клиент: «Проект',text)
        self.assertEqual(model['facts_by_kind']['reported_production'].confidence,'context_only')
        self.assertNotIn('покупка подтверждена',text)

    def test_sections_atomic_utf16_and_file_fallback(self):
        text='📞 Тест\n\n1. Итог звонка\n'+'😀'*1300+'\n\n5. Договорённости\n'+'Я'*2000
        parts=review.sections(text)
        self.assertGreater(len(parts),1)
        self.assertEqual('\n\n'.join(parts),text)
        self.assertTrue(all(render.units(p)<=3700 for p in parts))
        self.assertIsNone(review.sections('1. Итог звонка\n'+'😀'*2500))


class Transaction:
    def __init__(self,lock): self.lock=lock
    async def __aenter__(self): await self.lock.acquire();return self
    async def __aexit__(self,*args): self.lock.release()


class Connection:
    def __init__(self,pool): self.pool=pool
    async def __aenter__(self): return self
    async def __aexit__(self,*args): pass
    def transaction(self): return Transaction(self.pool.lock)
    async def fetchrow(self,sql,*args): return await self.pool.fetchrow(sql,*args)
    async def fetchval(self,sql,*args): return self.pool.row['expanded']
    async def execute(self,sql,*args):
        if 'SET expanded=$2' not in sql: raise AssertionError('Unexpected write')
        self.pool.row['expanded']=args[1]


class Pool:
    def __init__(self,row): self.row=row;self.lock=asyncio.Lock()
    def acquire(self): return Connection(self)
    async def fetchrow(self,sql,*args):
        if args[0]!=self.row['id'] or len(args)>1 and args[1]!=self.row['client_id']: return None
        return copy.deepcopy(self.row)


class CacheAndUiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pool=Pool(sample());self.actor=Actor(900,1,'owner','')
        self.guard=patch.object(store,'get_call',AsyncMock(return_value={'id':123}))
        self.guard.start();self.addCleanup(self.guard.stop)
        self.bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=42)),
                    send_document=AsyncMock(return_value=SimpleNamespace(message_id=43)),delete_message=AsyncMock())

    async def test_old_cache_new_format_double_click_immutable_no_paid_request(self):
        self.pool.row['expanded']='Основная шкала\nOld dump'
        before=copy.deepcopy(self.pool.row)
        with patch.object(render,'expanded',wraps=render.expanded) as generate:
            texts=await asyncio.gather(*(store.overview(self.pool,self.actor,1) for _ in range(5)))
            generate.assert_called_once()
        self.assertEqual(len(set(texts)),1)
        cache=overview_cache.decode(self.pool.row['expanded'])
        self.assertEqual(cache['legacy_text'],before['expanded'])
        for key in ('summary','primary_analysis','configuration','transcript','auxiliary'):
            self.assertEqual(self.pool.row[key],before[key])
        self.assertIn('facts',overview_cache.entry(cache))

    async def test_real_access_guards_manager_head_owner_tenant(self):
        for actor in (None,Actor(900,2,'owner',''),Actor(124,1,'manager','102')):
            with self.assertRaises(Forbidden): await store.overview(self.pool,actor,1)
        for actor in (Actor(123,1,'manager','101'),Actor(901,1,'head',''),self.actor):
            self.assertIn('Расширенный разбор',await store.overview(self.pool,actor,1))

    async def test_generation_error_summary_preserved_retry(self):
        with patch.object(render,'expanded',side_effect=ValueError('synthetic')):
            with self.assertRaises(ValueError): await store.overview(self.pool,self.actor,1)
        self.assertIsNone(self.pool.row['expanded']);self.assertEqual(self.pool.row['summary'],'ordinary report')
        self.assertTrue(await store.overview(self.pool,self.actor,1))

    async def test_double_click_sends_one_analysis_plain_text(self):
        await asyncio.gather(*(ui.show(self.bot,self.pool,self.actor,1,detailed=True) for _ in range(2)))
        calls=[c for c in self.bot.send_message.await_args_list if c.args[1].startswith('📞')]
        self.assertEqual(len(calls),1)
        self.assertIsNone(calls[0].kwargs['parse_mode'])

    async def test_completed_view_offers_reply_and_reopen_without_forcing_deletion(self):
        await ui.show(self.bot,self.pool,self.actor,1,detailed=True)
        await ui.show(self.bot,self.pool,self.actor,1,detailed=True)
        sent = self.bot.send_message.await_args
        self.assertIn('Показать обзор здесь',sent.args[1])
        self.assertNotIn('Закройте',sent.args[1])
        self.assertEqual(sent.kwargs['reply_parameters'].message_id,42)
        self.assertTrue(sent.kwargs['reply_parameters'].allow_sending_without_reply)
        button = sent.kwargs['reply_markup'].inline_keyboard[0][0]
        self.assertEqual(button.text,'Показать обзор здесь')
        self.assertEqual(button.callback_data,'uv:r:1:0')
        self.assertLessEqual(len(button.callback_data.encode()),64)

    async def test_explicit_reopen_is_once_preserves_cache_and_old_delivery(self):
        self.bot.send_message.side_effect = [SimpleNamespace(message_id=10),SimpleNamespace(message_id=11)]
        with patch.object(render,'expanded',wraps=render.expanded) as generate:
            await ui.show(self.bot,self.pool,self.actor,1,detailed=True)
            snapshot = copy.deepcopy(self.pool.row)
            results = await asyncio.gather(*(overview_cache.reopen(self.pool,self.actor,1,0) for _ in range(4)))
            self.assertEqual(results.count(True),1)
            await ui.show(self.bot,self.pool,self.actor,1,detailed=True)
            self.assertFalse(await overview_cache.reopen(self.pool,self.actor,1,0))
            generate.assert_called_once()
        self.bot.delete_message.assert_not_awaited()
        cache=overview_cache.decode(self.pool.row['expanded']);item=overview_cache.entry(cache)
        self.assertEqual(item['delivery_history']['900'][0]['parts'][0]['message_id'],10)
        self.assertEqual(item['delivery']['900']['parts'][0]['message_id'],11)
        self.assertEqual(item['delivery']['900']['generation'],1)
        for name in ('summary','transcript','primary_analysis','configuration','auxiliary'):
            self.assertEqual(self.pool.row[name],snapshot[name])

    async def test_uncertain_or_partial_delivery_cannot_be_reset_by_reopen(self):
        await ui.show(self.bot,self.pool,self.actor,1,detailed=True)
        for status in ('sending','uncertain','pending','forbidden'):
            cache=overview_cache.decode(self.pool.row['expanded'])
            overview_cache.entry(cache)['delivery']['900']['parts'][0]['status']=status
            self.pool.row['expanded']=overview_cache.encode(cache)
            before=self.pool.row['expanded']
            with self.assertRaises(ValueError):await overview_cache.reopen(self.pool,self.actor,1,0)
            self.assertEqual(self.pool.row['expanded'],before)

    async def test_close_does_not_make_old_reopen_button_valid_again(self):
        await ui.show(self.bot,self.pool,self.actor,1,detailed=True)
        await overview_cache.close(self.pool,self.bot,self.actor,1,42)
        await ui.show(self.bot,self.pool,self.actor,1,detailed=True)
        self.assertFalse(await overview_cache.reopen(self.pool,self.actor,1,0))
        receipt=await overview_cache.delivered_view(self.pool,self.actor,1)
        self.assertEqual(receipt['generation'],1)

    async def test_reopen_enforces_tenant_manager_and_recipient_isolation(self):
        await ui.show(self.bot,self.pool,self.actor,1,detailed=True)
        for actor in (None,Actor(900,2,'owner',''),Actor(124,1,'manager','102')):
            with self.assertRaises(Forbidden):await overview_cache.reopen(self.pool,actor,1,0)
        # Another permitted head has access to the call but no delivery to reopen.
        with self.assertRaises(ValueError):
            await overview_cache.reopen(self.pool,Actor(901,1,'head',''),1,0)
        receipt=await overview_cache.delivered_view(self.pool,self.actor,1)
        self.assertEqual(receipt.get('generation',0),0)

    async def test_known_error_second_part_retry_and_restart_do_not_resend_first(self):
        await store.overview(self.pool,self.actor,1)
        cache=overview_cache.decode(self.pool.row['expanded'])
        text='📞 Тест\n\n1. Итог звонка\n'+'а'*2600+'\n\n5. Действия\n'+'б'*2600
        overview_cache.entry(cache)['text']=text
        self.pool.row['expanded']=overview_cache.encode(cache)
        from aiogram.exceptions import TelegramBadRequest
        self.bot.send_message.side_effect=[SimpleNamespace(message_id=10),TelegramBadRequest(method=None,message='synthetic')]
        with self.assertRaises(TelegramBadRequest): await ui.show(self.bot,self.pool,self.actor,1,detailed=True)
        self.bot.send_message.side_effect=None
        self.pool=Pool(self.pool.row)  # A fresh worker sees the committed receipts.
        await ui.show(self.bot,self.pool,self.actor,1,detailed=True)
        self.assertEqual(self.bot.send_message.await_count,3)
        self.assertTrue(self.bot.send_message.await_args.args[1].startswith('Часть 2/2'))

    async def test_unknown_error_not_blindly_resent(self):
        self.bot.send_message.side_effect=TimeoutError('synthetic')
        with self.assertRaises(TimeoutError): await ui.show(self.bot,self.pool,self.actor,1,detailed=True)
        self.bot.send_message.side_effect=None
        await ui.show(self.bot,self.pool,self.actor,1,detailed=True)
        self.assertIn('результат неизвестен',self.bot.send_message.await_args.args[1])

    async def test_close_only_owned_overview_then_deliberate_reopen(self):
        await ui.show(self.bot,self.pool,self.actor,1,detailed=True)
        with self.assertRaises(ValueError): await overview_cache.close(self.pool,self.bot,self.actor,1,999)
        self.bot.delete_message.assert_not_awaited()
        await overview_cache.close(self.pool,self.bot,self.actor,1,42)
        self.bot.delete_message.assert_awaited_once_with(900,42)
        await ui.show(self.bot,self.pool,self.actor,1,detailed=True)
        self.assertEqual(self.bot.send_message.await_count,2)

    async def test_long_atomic_section_goes_to_document(self):
        await store.overview(self.pool,self.actor,1)
        cache=overview_cache.decode(self.pool.row['expanded'])
        overview_cache.entry(cache)['text']='📞 Обзор\n\n1. Итог\n'+'😀'*3000
        self.pool.row['expanded']=overview_cache.encode(cache)
        await ui.show(self.bot,self.pool,self.actor,1,detailed=True)
        self.bot.send_document.assert_awaited_once()
        self.bot.send_message.assert_not_awaited()

    async def test_callback_ack_first_clicker_not_bot_author_and_denied_actions(self):
        from datetime import datetime,timezone
        from aiogram import Bot,Router
        from aiogram.types import CallbackQuery,Message,User,Chat
        router=Router();ui.install(router,lambda:self.pool)
        telegram=Bot('123456:synthetic_test_token')
        try:
            for user_id,data,chat_id,allowed in [(900,'uv:e:1',900,True),(124,'uv:e:1',124,False),
                        (900,'uv:r:1:0',900,True),(900,'uv:r:1:0',900,False),
                        (124,'uv:r:1:0',124,False),(902,'uv:r:1:0',902,False),
                        (900,'uv:r:1:x',900,False),(900,'uv:r:1:-1',900,False),
                        (902,'uv:e:1',902,False),(900,'uv:e:1',123,False),(900,'uv:e:999',900,False),
                        (900,'uv:bad:1',900,False)]:
                actor=Actor(user_id,2 if user_id==902 else 1,'manager' if user_id==124 else 'owner','102' if user_id==124 else '')
                message=Message(message_id=11,date=datetime.now(timezone.utc),chat=Chat(id=chat_id,type='private'),
                            from_user=User(id=777,is_bot=True,first_name='Bot'),text='Saved report')
                cq=CallbackQuery(id='synthetic',from_user=User(id=user_id,is_bot=False,first_name='Person'),
                            chat_instance='synthetic',data=data,message=message).as_(telegram)
                order=[]
                async def ack(*args,**kwargs): order.append('ack')
                async def send(*args,**kwargs): order.append('send');return SimpleNamespace(message_id=42)
                with patch.object(CallbackQuery,'answer',AsyncMock(side_effect=ack)), patch.object(Bot,'send_message',AsyncMock(side_effect=send)) as sent, patch.object(ui,'resolve_actor',AsyncMock(return_value=actor)) as resolver:
                    await router.propagate_event('callback_query',cq)
                    self.assertEqual(order[0],'ack')
                    if chat_id==user_id and data not in {'uv:bad:1','uv:r:1:x','uv:r:1:-1'}:
                        resolver.assert_awaited_once_with(self.pool,user_id)
                    self.assertEqual(any('📞 Расширенный разбор' in c.args[1] for c in sent.await_args_list),allowed)
        finally:
            await telegram.session.close()


if __name__=='__main__': unittest.main()
