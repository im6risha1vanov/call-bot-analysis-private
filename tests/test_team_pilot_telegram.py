import asyncio
import os
import unittest
from pathlib import Path
from unittest.mock import patch
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import ReplyKeyboardRemove
from tests import test_personal_telegram as fixtures
from team_pilot_telegram import install

@unittest.skipUnless(fixtures.DSN, 'requires isolated PostgreSQL')
class TeamPilotTelegramTests(fixtures.PersonalHTTPTests):
    test_owner_isolation_versions_and_history = None
    test_parallel_updates_and_support_are_denied = None
    test_same_person_second_invite_has_same_profile_and_pilot_closed = None
    feed_message = fixtures.PersonalTelegramTests.feed_message
    feed_callback = fixtures.PersonalTelegramTests.feed_callback

    async def asyncSetUp(self):
        await super().asyncSetUp()
        for name in ('056_crm_team_assignments.sql','057_crm_team_meetings.sql'):
            await self.pool.execute(Path('migrations',name).read_text(encoding='utf-8'))
        await self.pool.execute('TRUNCATE crm_team_assignments, crm_personal_telegram_events, crm_personal_telegram_state CASCADE')
        self.enterContext(patch.dict(os.environ, {'CRM_PERSONAL_DAY_ENABLED':'1','CRM_URL':'https://example.com'}))
        self.bot=Bot('123456:test'); self.calls=[]
        async def fake(method, **kwargs): self.calls.append(method); return True
        self.enterContext(patch.object(Bot,'__call__',side_effect=fake))
        router=Router()
        async def menu(message): return ReplyKeyboardRemove()
        install(router,lambda:self.pool,menu)
        @router.message(F.text)
        async def fallback(message): await message.answer('FALLBACK',parse_mode=None)
        self.dp=Dispatcher(); self.dp.include_router(router)

    async def test_create_submit_accept_and_replay(self):
        await self.feed_callback(123,'choose','tp:person:124')
        await asyncio.gather(self.feed_message(123,100,'Сделать отчёт'),self.feed_message(123,101,'Другое'))
        rows=await self.pool.fetch('SELECT * FROM crm_team_assignments')
        self.assertEqual(len(rows),1); row=rows[0]
        await self.feed_callback(123,'bad-submit',f"tp:i:submit:{row['id']}:1")
        self.assertTrue(any('недоступно' in str(c).lower() for c in self.calls))
        await self.feed_callback(124,'submit',f"tp:i:submit:{row['id']}:1")
        await self.feed_message(124,110,'Готово\nhttps://example.org/result')
        await self.feed_callback(123,'stale',f"tp:a:accept:{row['id']}:1")
        self.assertEqual(await self.pool.fetchval('SELECT version FROM crm_team_assignments WHERE id=$1',row['id']),2)
        await self.feed_callback(123,'accept',f"tp:a:accept:{row['id']}:2")
        await self.feed_callback(123,'accept',f"tp:a:accept:{row['id']}:2")
        self.assertEqual(await self.pool.fetchval('SELECT version FROM crm_team_assignments WHERE id=$1',row['id']),3)

    async def test_old_menu_cancels_mode_and_nonpilot_denied(self):
        await self.feed_callback(123,'choose','tp:person:124')
        await self.feed_message(123,200,'↩️ Старая методика')
        self.assertEqual(await self.pool.fetchval('SELECT action FROM crm_personal_telegram_state WHERE telegram_user_id=123'),'idle')
        await self.feed_message(123,201,'обычный текст')
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM crm_team_assignments'),0)
        await self.feed_callback(125,'outside','tp:person:124')
        self.assertFalse(await self.pool.fetchval('SELECT 1 FROM crm_personal_telegram_state WHERE telegram_user_id=125'))
        await self.pool.execute('UPDATE crm_invites SET revoked_at=now() WHERE telegram_user_id=123')
        await self.feed_callback(123,'revoked','tp:new')
        self.assertTrue(any('недоступен' in str(c).lower() for c in self.calls))
