import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from tests.test_crm_personal import PersonalHTTPTests, DSN
from tools import resolve_actor
import personal_executor as engine

@unittest.skipUnless(DSN, 'METHODOLOGY_TEST_DATABASE_URL не задан')
class ExecutorTests(PersonalHTTPTests):
    test_owner_isolation_versions_and_history=None
    test_parallel_updates_and_support_are_denied=None
    test_same_person_second_invite_has_same_profile_and_pilot_closed=None
    async def asyncSetUp(self):
        await super().asyncSetUp()
        root=Path(__file__).resolve().parents[1]
        for name in ('046_crm_personal_telegram.sql','047_crm_personal_voice_reminders.sql','048_crm_personal_executor.sql'):
            await self.pool.execute((root/'migrations'/name).read_text(encoding='utf-8-sig'))
        await self.pool.execute('TRUNCATE crm_personal_agent_steps, crm_personal_agent_actions, crm_personal_agent_jobs, crm_personal_telegram_events, crm_personal_telegram_state')
        self.login(123)
        await self.request_json('PUT','/api/personal/profile',{'display_name':'Первый','timezone':'Europe/Moscow'})
        self.actor=await resolve_actor(self.pool,123)

    async def test_task_creation_deduplicates_and_isolated(self):
        job=await engine.submit(self.pool,self.actor,'Создай задачу','test:1')
        self.assertIsNone(await engine.submit(self.pool,self.actor,'Создай задачу','test:1'))
        result=await engine.invoke(self.pool,job,'create_task',{'title':'Сделать','planned_day':'2026-11-18'},1)
        self.assertEqual(result['title'],'Сделать')
        other=await resolve_actor(self.pool,124)
        other_job=await engine.submit(self.pool,other,'Открыть')
        self.login(124); await self.request_json('PUT','/api/personal/profile',{'display_name':'Второй','timezone':'Europe/Moscow'})
        with self.assertRaises(ValueError): await engine.invoke(self.pool,other_job,'get_task',{'id':result['id']},1)

    async def test_model_tool_evidence_and_completed(self):
        job=await engine.submit(self.pool,self.actor,'Создай задачу')
        use=SimpleNamespace(type='tool_use',name='create_task',input={'title':'Результат'},id='u1')
        text=SimpleNamespace(type='text',text='Готово')
        responses=[SimpleNamespace(content=[use],usage=SimpleNamespace(input_tokens=1,output_tokens=1)),SimpleNamespace(content=[text],usage=SimpleNamespace(input_tokens=1,output_tokens=1))]
        with patch.object(engine.rop_agent.claude.messages,'create',new=AsyncMock(side_effect=responses)), patch.object(engine.rop_agent,'_over_daily_limit',new=AsyncMock(return_value=False)), patch.object(engine.rop_agent,'_add_spend',new=AsyncMock()):
            status,result=await engine.execute(self.pool,job)
        self.assertEqual(status,'completed')
        step=await self.pool.fetchrow('SELECT * FROM crm_personal_agent_steps WHERE job_id=$1',job['id'])
        self.assertEqual((step['tool'],step['status']),('create_task','ok'))

    async def test_external_action_requires_owner_confirmation_and_is_once(self):
        job=await engine.submit(self.pool,self.actor,'Передать сообщение')
        result=await engine.invoke(self.pool,job,'prepare_telegram_message',{'recipient':124,'text':'Привет'},1)
        action=await self.pool.fetchrow('SELECT * FROM crm_personal_agent_actions WHERE job_id=$1',job['id'])
        self.assertTrue(result['confirmation_required'])
        await self.pool.execute("UPDATE crm_personal_agent_jobs SET status='needs_confirmation' WHERE id=$1",job['id'])
        other=await resolve_actor(self.pool,124)
        with self.assertRaises(Exception): await engine.approve(self.pool,other,job['id'],action['token'])
        await engine.approve(self.pool,self.actor,job['id'],action['token'])
        with self.assertRaises(Exception): await engine.approve(self.pool,self.actor,job['id'],action['token'])
        bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=17)))
        await engine.deliver_action(self.pool,bot); await engine.deliver_action(self.pool,bot)
        self.assertEqual(bot.send_message.await_count,1)
        self.assertEqual(await self.pool.fetchval('SELECT status FROM crm_personal_agent_actions WHERE job_id=$1',job['id']),'sent')

    async def test_question_returned_and_context_bounded_to_owner(self):
        job=await engine.submit(self.pool,self.actor,'Нужна задача')
        tool=SimpleNamespace(type='tool_use',name='ask_owner',input={'question':'На какой срок?'},id='q1')
        response=SimpleNamespace(content=[tool],usage=SimpleNamespace(input_tokens=1,output_tokens=1))
        with patch.object(engine.rop_agent.claude.messages,'create',new=AsyncMock(return_value=response)), patch.object(engine.rop_agent,'_over_daily_limit',new=AsyncMock(return_value=False)), patch.object(engine.rop_agent,'_add_spend',new=AsyncMock()):
            status,result=await engine.execute(self.pool,job)
        self.assertEqual((status,result),('needs_input','На какой срок?'))
        await self.pool.execute('UPDATE crm_personal_agent_jobs SET status=$2,result=$3 WHERE id=$1',job['id'],status,result)
        follow=await engine.submit(self.pool,self.actor,'Завтра')
        answer=SimpleNamespace(content=[SimpleNamespace(type='text',text='Уточнение принято.')],usage=SimpleNamespace(input_tokens=1,output_tokens=1))
        model=AsyncMock(return_value=answer)
        with patch.object(engine.rop_agent.claude.messages,'create',new=model), patch.object(engine.rop_agent,'_over_daily_limit',new=AsyncMock(return_value=False)), patch.object(engine.rop_agent,'_add_spend',new=AsyncMock()): await engine.execute(self.pool,follow)
        self.assertIn('Нужна задача',str(model.await_args.kwargs['messages']))
        self.assertIn('Не повторяй',str(model.await_args.kwargs['messages']))

    async def test_uncertain_send_never_retries(self):
        job=await engine.submit(self.pool,self.actor,'Передать')
        await engine.invoke(self.pool,job,'prepare_telegram_message',{'recipient':124,'text':'Текст'},1)
        action=await self.pool.fetchrow('SELECT * FROM crm_personal_agent_actions WHERE job_id=$1',job['id'])
        await self.pool.execute("UPDATE crm_personal_agent_jobs SET status='needs_confirmation' WHERE id=$1",job['id'])
        await engine.approve(self.pool,self.actor,job['id'],action['token'])
        bot=SimpleNamespace(send_message=AsyncMock(side_effect=TimeoutError()))
        await engine.deliver_action(self.pool,bot); await engine.deliver_action(self.pool,bot)
        self.assertEqual(bot.send_message.await_count,1)
        self.assertEqual(await self.pool.fetchval('SELECT status FROM crm_personal_agent_actions WHERE job_id=$1',job['id']),'uncertain')

    async def test_revoked_recipient_blocks_and_cancellation_never_sends(self):
        job=await engine.submit(self.pool,self.actor,'Передать')
        await engine.invoke(self.pool,job,'prepare_telegram_message',{'recipient':124,'text':'Текст'},1)
        token=await self.pool.fetchval('SELECT token FROM crm_personal_agent_actions WHERE job_id=$1',job['id'])
        await self.pool.execute("UPDATE crm_personal_agent_jobs SET status='needs_confirmation' WHERE id=$1",job['id'])
        await engine.approve(self.pool,self.actor,job['id'],token,False)
        bot=SimpleNamespace(send_message=AsyncMock())
        await engine.deliver_action(self.pool,bot)
        bot.send_message.assert_not_awaited()
        job2=await engine.submit(self.pool,self.actor,'Ещё передать')
        await engine.invoke(self.pool,job2,'prepare_telegram_message',{'recipient':124,'text':'Текст'},1)
        token2=await self.pool.fetchval('SELECT token FROM crm_personal_agent_actions WHERE job_id=$1',job2['id'])
        await self.pool.execute("UPDATE crm_personal_agent_jobs SET status='needs_confirmation' WHERE id=$1",job2['id'])
        await engine.approve(self.pool,self.actor,job2['id'],token2)
        await self.pool.execute('UPDATE crm_invites SET revoked_at=now() WHERE telegram_user_id=124')
        await engine.deliver_action(self.pool,bot)
        bot.send_message.assert_not_awaited()
        self.assertEqual(await self.pool.fetchval('SELECT status FROM crm_personal_agent_actions WHERE job_id=$1',job2['id']),'failed')

    async def test_owner_http_isolation_and_token(self):
        self.login(123)
        code,created=await self.request_json('POST','/api/personal/agent/jobs',{'request':'Подготовь отчёт','request_id':'1234567890abcdef'})
        self.assertEqual(code,201)
        job_id=created['job']['id']
        self.login(124)
        code,_=await self.request_json('GET',f'/api/personal/agent/jobs/{job_id}')
        self.assertEqual(code,404)
        code,_=await self.request_json('POST',f'/api/personal/agent/jobs/{job_id}/confirm',{'token':'wrong','approve':True})
        self.assertNotEqual(code,200)

    async def test_connection_and_web_sources_are_recorded(self):
        job=await engine.submit(self.pool,self.actor,'Найди')
        with patch.object(engine.donor_web,'search',new=AsyncMock(return_value=[{'title':'Источник','url':'https://example.org/a','snippet':'Данные'}])):
            result=await engine.invoke(self.pool,job,'search_web',{'query':'пример'},1)
        self.assertEqual(result[0]['url'],'https://example.org/a')
        response=SimpleNamespace(content=[SimpleNamespace(type='tool_use',name='needs_connection',input={'service':'Почта','prepared_text':'Черновик'},id='e')],usage=SimpleNamespace(input_tokens=1,output_tokens=1))
        with patch.object(engine.rop_agent.claude.messages,'create',new=AsyncMock(return_value=response)),patch.object(engine.rop_agent,'_over_daily_limit',new=AsyncMock(return_value=False)),patch.object(engine.rop_agent,'_add_spend',new=AsyncMock()): status,result=await engine.execute(self.pool,job)
        self.assertEqual(status,'needs_connection'); self.assertIn('Почта',result)

    async def test_concurrent_queue_cap(self):
        import asyncio
        results=await asyncio.gather(*(engine.submit(self.pool,self.actor,f'Поручение {i}',f'quota:{i}') for i in range(5)),return_exceptions=True)
        self.assertEqual(sum(not isinstance(x,Exception) for x in results),3)

    async def test_real_bot_agent_text_voice_and_tasks_exit(self):
        from aiogram import Bot,Dispatcher
        from aiogram.types import Update
        import demo_bot
        bot=Bot('123456:test'); dp=Dispatcher(); dp.include_router(demo_bot.router)
        self.addCleanup(setattr,demo_bot.router,'_parent_router',None)
        def msg(i,text=None,voice=False):
            body={'message_id':i,'date':1,'chat':{'id':123,'type':'private'},'from':{'id':123,'is_bot':False,'first_name':'Тест'}}
            if voice: body['voice']={'file_id':'f','file_unique_id':'u','duration':2,'file_size':4}
            else:
                body['text']=text
                if text.startswith('/'): body['entities']=[{'type':'bot_command','offset':0,'length':len(text)}]
            return Update.model_validate({'update_id':i,'message':body},context={'bot':bot})
        async def download(path,destination,**kwargs): destination.write(b'ogg'); return destination
        with patch.object(demo_bot,'PG_POOL',self.pool),patch.object(Bot,'__call__',new=AsyncMock(return_value=True)), \
             patch.object(Bot,'get_file',new=AsyncMock(return_value=SimpleNamespace(file_path='x',file_size=4))), \
             patch.object(Bot,'download_file',side_effect=download), \
             patch('personal_executor_telegram.wav_from_audio',new=AsyncMock(return_value=b'wav')), \
             patch('personal_executor_telegram.deepgram_client.transcribe_bytes',new=AsyncMock(return_value=('Голосовое поручение',2))) as asr:
            await dp.feed_update(bot,msg(100,'/agent'))
            await dp.feed_update(bot,msg(101,'Текстовое поручение'))
            await dp.feed_update(bot,msg(102,voice=True))
            await dp.feed_update(bot,msg(102,voice=True))
            self.assertEqual(asr.await_count,1)
            await dp.feed_update(bot,msg(103,'/tasks'))
            self.assertEqual(await self.pool.fetchval('SELECT action FROM crm_personal_telegram_state WHERE telegram_user_id=123'),'idle')
            await dp.feed_update(bot,msg(104,'/agent'))
            await dp.feed_update(bot,msg(105,'/prompt'))
            self.assertNotEqual(await self.pool.fetchval('SELECT action FROM crm_personal_telegram_state WHERE telegram_user_id=123'),'agent')
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM crm_personal_agent_jobs'),2)

    async def test_recovery_reconciles_completed_and_proposed_actions(self):
        import asyncio
        sent=await engine.submit(self.pool,self.actor,'Сообщение один')
        await engine.invoke(self.pool,sent,'prepare_telegram_message',{'recipient':124,'text':'Один'},1)
        await self.pool.execute("UPDATE crm_personal_agent_actions SET status='sent',message_id=12 WHERE job_id=$1",sent['id'])
        proposal=await engine.submit(self.pool,self.actor,'Сообщение два')
        await engine.invoke(self.pool,proposal,'prepare_telegram_message',{'recipient':124,'text':'Два'},1)
        await self.pool.execute("UPDATE crm_personal_agent_jobs SET status='running' WHERE id=ANY($1::bigint[])",[sent['id'],proposal['id']])
        bot=SimpleNamespace(send_message=AsyncMock())
        runner=asyncio.create_task(engine.run(self.pool,bot))
        await asyncio.sleep(.2); runner.cancel()
        await asyncio.gather(runner,return_exceptions=True)
        self.assertEqual(await self.pool.fetchval('SELECT status FROM crm_personal_agent_jobs WHERE id=$1',sent['id']),'completed')
        self.assertEqual(await self.pool.fetchval('SELECT status FROM crm_personal_agent_jobs WHERE id=$1',proposal['id']),'needs_confirmation')
        bot.send_message.assert_not_awaited()

    async def test_corrected_validation_failure_resolves_only_same_task(self):
        job=await engine.submit(self.pool,self.actor,'Создать задачу с датой')
        def reply(blocks): return SimpleNamespace(content=blocks,usage=SimpleNamespace(input_tokens=1,output_tokens=1))
        bad=SimpleNamespace(type='tool_use',name='create_task',input={'title':'План','due_at':'2026-11-18T10:00'},id='a')
        good=SimpleNamespace(type='tool_use',name='create_task',input={'title':'План','due_at':'2026-11-18T10:00:00+03:00'},id='b')
        final=SimpleNamespace(type='text',text='Задача создана.')
        with patch.object(engine.rop_agent.claude.messages,'create',new=AsyncMock(side_effect=[reply([bad]),reply([good]),reply([final])])),patch.object(engine.rop_agent,'_over_daily_limit',new=AsyncMock(return_value=False)),patch.object(engine.rop_agent,'_add_spend',new=AsyncMock()):
            status,result=await engine.execute(self.pool,job)
        self.assertEqual(status,'completed')
        steps=await self.pool.fetch('SELECT status,evidence FROM crm_personal_agent_steps WHERE job_id=$1 ORDER BY sequence',job['id'])
        self.assertEqual([step['status'] for step in steps],['failed','ok'])
        self.assertIn('Неверная дата',str(steps[0]['evidence']))

    async def test_other_target_failure_is_not_cleared_by_success(self):
        job=await engine.submit(self.pool,self.actor,'Две задачи')
        def reply(blocks): return SimpleNamespace(content=blocks,usage=SimpleNamespace(input_tokens=1,output_tokens=1))
        bad=SimpleNamespace(type='tool_use',name='create_task',input={'title':'Первый','due_at':'ошибка'},id='a')
        good=SimpleNamespace(type='tool_use',name='create_task',input={'title':'Второй'},id='b')
        final=SimpleNamespace(type='text',text='Выполнено')
        with patch.object(engine.rop_agent.claude.messages,'create',new=AsyncMock(side_effect=[reply([bad]),reply([good]),reply([final])])),patch.object(engine.rop_agent,'_over_daily_limit',new=AsyncMock(return_value=False)),patch.object(engine.rop_agent,'_add_spend',new=AsyncMock()):
            status,result=await engine.execute(self.pool,job)
        self.assertEqual(status,'needs_input')

    async def test_unknown_write_outcome_stops_without_model_retry(self):
        job=await engine.submit(self.pool,self.actor,'Создать задачу')
        tool=SimpleNamespace(type='tool_use',name='create_task',input={'title':'План'},id='write')
        model=AsyncMock(return_value=SimpleNamespace(content=[tool],usage=SimpleNamespace(input_tokens=1,output_tokens=1)))
        with patch.object(engine.rop_agent.claude.messages,'create',new=model), \
             patch.object(engine.rop_agent,'_over_daily_limit',new=AsyncMock(return_value=False)), \
             patch.object(engine.rop_agent,'_add_spend',new=AsyncMock()), \
             patch.object(engine,'invoke',new=AsyncMock(side_effect=RuntimeError('unknown outcome'))):
            status,result=await engine.execute(self.pool,job)
        self.assertEqual(status,'needs_input')
        self.assertIn('не подтверждён',result)
        self.assertEqual(model.await_count,1)
        self.assertEqual(await self.pool.fetchval('SELECT status FROM crm_personal_agent_steps WHERE job_id=$1',job['id']),'uncertain')

    async def test_model_timeout_after_task_creation_reports_actual_saved_task(self):
        job=await engine.submit(self.pool,self.actor,'Создать задачу')
        tool=SimpleNamespace(type='tool_use',name='create_task',input={'title':'План'},id='write')
        response=SimpleNamespace(content=[tool],usage=SimpleNamespace(input_tokens=1,output_tokens=1))
        model=AsyncMock(side_effect=[response,TimeoutError()])
        with patch.object(engine.rop_agent.claude.messages,'create',new=model), \
             patch.object(engine.rop_agent,'_over_daily_limit',new=AsyncMock(return_value=False)), \
             patch.object(engine.rop_agent,'_add_spend',new=AsyncMock()):
            status,result=await engine.execute(self.pool,job)
        task=await self.pool.fetchrow("SELECT id,version FROM crm_personal_tasks WHERE title='План'")
        self.assertEqual(status,'needs_input')
        self.assertIn('задача №'+str(task['id']),result)
        self.assertIn('версия '+str(task['version']),result)
        self.assertIn('не повторяйте',result)
        self.assertEqual(await self.pool.fetchval("SELECT count(*) FROM crm_personal_tasks WHERE title='План'"),1)
        self.assertEqual(model.await_count,2)
