"""Чтение рабочего Telegram: папки, чей клиент, дочитывание сообщений. Клиент Telegram поддельный."""
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import asyncpg

from review import tgchats as T

DSN = os.environ.get('METHODOLOGY_TEST_DATABASE_URL')
NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


class User(SimpleNamespace):
    pass


class Channel(SimpleNamespace):
    pass


def folder(title, include=(), exclude=(), folder_id=2, **flags):
    return SimpleNamespace(id=folder_id, title=SimpleNamespace(text=title), include_peers=list(include), pinned_peers=[],
                           exclude_peers=list(exclude), **flags)


class FakeClient:
    def __init__(self, filters, dialogs, messages):
        self.filters, self.dialogs, self.messages, self.calls = filters, dialogs, messages, []

    async def __call__(self, request):
        return SimpleNamespace(filters=self.filters)

    async def iter_dialogs(self, limit=None):
        for d in self.dialogs:
            yield d

    async def iter_messages(self, entity, reverse=False, min_id=None, offset_date=None):
        self.calls.append({'peer': entity.id, 'min_id': min_id, 'offset_date': offset_date})
        for m in self.messages.get(entity.id, []):
            if (min_id and m.id <= min_id) or (offset_date and m.date < offset_date):
                continue
            yield m


def msg(i, days_ago, out, text, **extra):
    return SimpleNamespace(id=i, date=NOW - timedelta(days=days_ago), out=out, message=text, media=None, **extra)


class FolderTests(unittest.TestCase):
    def test_explicit_owner_configuration(self):
        for raw, expected in (('', {}), ('2:10, 8:11', {2: '10', 8: '11'})):
            with patch.dict(os.environ, {'TG_FOLDER_OWNERS': raw}):
                self.assertEqual(T.folder_owners(), expected)
        for raw in ('2:10,2:11', 'Илья:12', '0:10', '2:head'):
            with patch.dict(os.environ, {'TG_FOLDER_OWNERS': raw}), self.assertRaises(ValueError):
                T.folder_owners()

    def test_membership(self):
        contact = User(id=1, contact=True, bot=False)
        stranger = User(id=2, contact=False, bot=False)
        group = Channel(id=-1003, megagroup=True)
        by_type = folder('Лузин', contacts=True, non_contacts=False)
        self.assertTrue(T.in_folder(by_type, 1, contact, {'include': set(), 'exclude': set()}))
        self.assertFalse(T.in_folder(by_type, 2, stranger, {'include': set(), 'exclude': set()}))
        self.assertFalse(T.in_folder(by_type, 1, contact, {'include': set(), 'exclude': {1}}))
        self.assertTrue(T.in_folder(folder('x'), 2, stranger, {'include': {2}, 'exclude': set()}))
        self.assertFalse(T.in_folder(folder('x'), -1003, group, {'include': set(), 'exclude': set()}))
        self.assertEqual((T.chat_kind(contact), T.chat_kind(group)), ('user', 'group'))
        self.assertEqual(T.title_text(SimpleNamespace(text=' Козлов ')), 'Козлов')


@unittest.skipUnless(DSN, 'METHODOLOGY_TEST_DATABASE_URL не задан')
class KeysTests(unittest.TestCase):
    def test_save_keys(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}):
            env, backups = Path(tmp) / '.env', Path(tmp) / 'backups'
            backups.mkdir()
            env.write_text('DATABASE_URL=x\nTG_API_ID=1\nTG_API_HASH=old\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                T.save_keys(env, '12a', 'f' * 32, backups)
            with self.assertRaises(ValueError):
                T.save_keys(env, '123', 'xyz', backups)
            T.save_keys(env, '123', 'ab' * 16, backups)
            self.assertEqual(env.read_text(encoding='utf-8'),
                             'DATABASE_URL=x\nTG_API_ID=123\nTG_API_HASH=' + 'ab' * 16 + '\n')
            self.assertIn('TG_API_HASH=old', (backups / 'env.before-tg').read_text(encoding='utf-8'))
            self.assertEqual(os.environ['TG_API_ID'], '123')


APPS_PAGE = """<form class="form-horizontal" role="form">
<div class="form-group"><label for="app_id" class="col-md-4 text-right control-label">App api_id:</label>
<div class="col-md-7"><span class="form-control input-xlarge uneditable-input" onclick="this.select();"><strong>1234567</strong></span></div></div>
<div class="form-group"><label for="app_hash" class="col-md-4 text-right control-label">App api_hash:</label>
<div class="col-md-7"><span class="form-control input-xlarge uneditable-input" onclick="this.select();">0123456789abcdef0123456789abcdef</span></div></div>
</form>"""
CREATE_PAGE = '''<form id="app_create_form"><input type="hidden" name="hash" value="f00&amp;d"/>
<input name="app_title"/><input name="app_shortname"/></form>'''


class TgAppTests(unittest.TestCase):
    def site(self, app_exists=True, send=None, login='true'):
        import httpx
        state = {'app': app_exists, 'requests': []}

        def handler(request):
            body = request.content.decode()
            state['requests'].append((request.url.path, body, request.headers.get('cookie')))
            if request.url.path == '/auth/send_password':
                return httpx.Response(200, text=send) if send else httpx.Response(200, json={'random_hash': 'rh1'})
            if request.url.path == '/auth/login':
                return httpx.Response(200, text=login, headers={'set-cookie': 'stel_token=tok; path=/'})
            if request.url.path == '/apps':
                return httpx.Response(200, text=APPS_PAGE if state['app'] else CREATE_PAGE)
            if request.url.path == '/apps/create':
                state['app'] = True
                return httpx.Response(200, text='')
            return httpx.Response(404)
        client = httpx.Client(base_url='https://my.telegram.org', transport=httpx.MockTransport(handler))
        return client, state

    def test_existing_app(self):
        from review import tgapp
        client, state = self.site()
        self.assertEqual(tgapp.fetch_keys('+79001112233', lambda: 'Ab1Cd2', client),
                         ('1234567', '0123456789abcdef0123456789abcdef'))
        self.assertIn('random_hash=rh1', state['requests'][1][1])
        self.assertIn('password=Ab1Cd2', state['requests'][1][1])
        self.assertEqual(state['requests'][2][2], 'stel_token=tok')

    def test_creates_app(self):
        from review import tgapp
        client, state = self.site(app_exists=False)
        self.assertEqual(tgapp.fetch_keys('+79001112233', lambda: 'x', client)[0], '1234567')
        create = next(body for path, body, _ in state['requests'] if path == '/apps/create')
        self.assertIn('hash=f00%26d', create)
        self.assertIn('app_shortname=averoncrm', create)

    def test_errors(self):
        from review import tgapp
        client, _ = self.site(send='Too many attempts. Please try again later.')
        with self.assertRaisesRegex(tgapp.TgAppError, 'Too many attempts'):
            tgapp.fetch_keys('+79001112233', lambda: 'x', client)
        client, _ = self.site(login='Invalid confirmation code!')
        with self.assertRaisesRegex(tgapp.TgAppError, 'Invalid confirmation code'):
            tgapp.fetch_keys('+79001112233', lambda: 'x', client)
        self.assertEqual(tgapp.site_phone('8 951 727-22-86'), '+79517272286')
        with self.assertRaises(tgapp.TgAppError):
            tgapp.site_phone('727-22-86')


class SyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pool = await asyncpg.create_pool(DSN, min_size=1, max_size=4)
        self.assertTrue((await self.pool.fetchval('SELECT current_database()')).startswith('callbot_methodology_test_'))
        root = Path(__file__).resolve().parents[1]
        if not await self.pool.fetchval("SELECT to_regclass('clients') IS NOT NULL"):
            await self.pool.execute((root / 'tests/bootstrap.sql').read_text())
        await self.pool.execute((root / 'migrations' / '023_tg_chats.sql').read_text())
        await self.pool.execute((root / 'migrations' / '044_tg_folder_ownership.sql').read_text())
        await self.pool.execute('TRUNCATE clients, tg_messages CASCADE')
        await self.pool.execute("INSERT INTO clients VALUES(1,'Europe/Moscow',true)")
        await self.pool.execute("INSERT INTO employees VALUES(1,'12',123,'manager','Козлов Илья'),(1,'10',124,'manager','Ефим Лузин')")

    async def asyncTearDown(self):
        await self.pool.close()

    async def test_sync_reads_folders_and_new_messages(self):
        anna = User(id=501, contact=True, bot=False, username='anna', phone='79990001111')
        oleg = User(id=502, contact=False, bot=False, username=None, phone=None)
        both = User(id=503, contact=False, bot=False, username=None, phone=None)
        other = User(id=504, contact=True, bot=False, username=None, phone=None)  # вне папок
        dialogs = [SimpleNamespace(id=u.id, entity=u, name=n) for u, n in
                   ((anna, 'Анна Ромашка'), (oleg, 'Олег'), (both, 'Общий'), (other, 'Мама'))]
        messages = {501: [msg(1, 40, True, 'старое'), msg(2, 3, True, 'Отправил КП'), msg(3, 1, False, 'Посмотрим', voice=True)],
                    502: [msg(7, 2, False, 'Здравствуйте')]}
        filters = [SimpleNamespace(title=None),  # «Все чаты»
                   folder('Папка после переименования', include=['p501', 'p503'], contacts=True),
                   folder('Лузин', include=['p502', 'p503'], folder_id=3),
                   folder('Без назначения', include=['p504'], folder_id=4)]
        await self.pool.execute("""INSERT INTO tg_chats(client_id,peer_id,kind,phone,folders,extension)
                                 VALUES(1,501,'user','79991112233',ARRAY['Старое имя'],'12'),
                                       (1,503,'user','79995554433',ARRAY['Старое имя'],'12'),
                                       (1,505,'user',NULL,ARRAY['Удалённая папка'],'10')""")
        client = FakeClient(filters, dialogs, messages)
        with patch('telethon.utils.get_peer_id', lambda p: int(p[1:])), \
                patch.dict(os.environ, {'TG_FOLDER_OWNERS': '2:12,3:10,9:10'}):
            stats = await T.sync(self.pool, client, 1, NOW)
            self.assertEqual(stats, {'folders': 3, 'configured_folders': 2, 'missing_mappings': 1,
                                     'chats': 2, 'messages': 3, 'ambiguous': 1, 'ownership_cleared': 2})
            chats = {r['peer_id']: r for r in await self.pool.fetch('SELECT * FROM tg_chats')}
            self.assertEqual((chats[501]['extension'], chats[501]['folders'], chats[501]['last_message_id']),
                             ('12', ['Папка после переименования'], 3))
            self.assertTrue(chats[501]['folder_owner_verified'])
            self.assertEqual(chats[501]['phone'], '79991112233')  # ручная связь не теряется
            self.assertEqual(chats[502]['extension'], '10')
            self.assertIsNone(chats[503]['extension'])  # в папках двух менеджеров
            self.assertIsNone(chats[505]['extension'])  # папки больше нет
            self.assertNotIn(504, chats)
            self.assertEqual([c['peer'] for c in client.calls], [501, 502])
            voice = await self.pool.fetchrow('SELECT outgoing, text, media FROM tg_messages WHERE peer_id=501 AND message_id=3')
            self.assertEqual(tuple(voice), (False, 'Посмотрим', 'voice'))

            messages[501].append(msg(4, 0, True, 'Напомню завтра'))
            client.calls.clear()
            stats = await T.sync(self.pool, client, 1, NOW)
        self.assertEqual(stats['messages'], 1)
        self.assertEqual(next(c for c in client.calls if c['peer'] == 501)['min_id'], 3)
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM tg_messages'), 4)

    async def test_folder_sync_can_be_reviewed_without_personal_bot_binding(self):
        from review import tgreview

        await self.pool.execute('ALTER TABLE calls ADD COLUMN IF NOT EXISTS client_number TEXT')
        await self.pool.execute("UPDATE employees SET telegram_user_id=NULL WHERE extension='12'")
        await self.pool.execute("""INSERT INTO tg_chats(client_id,peer_id,kind,folders,extension,last_message_at)
                                 VALUES(1,502,'user',ARRAY['Старая папка'],'12',$1)""", NOW - timedelta(days=2))
        await self.pool.execute("INSERT INTO tg_messages VALUES(1,502,1,$1,false,'Личный чат',NULL)",
                                NOW - timedelta(days=2))
        entity = User(id=501, bot=False)
        client = FakeClient([folder('Моя папка', include=['p501'])], [SimpleNamespace(id=501, entity=entity, name='Клиент')],
                            {501: [msg(1, 1, False, 'Пришлите условия')]})
        with patch('telethon.utils.get_peer_id', lambda p: int(p[1:])), \
                patch.dict(os.environ, {'TG_FOLDER_OWNERS': '2:12'}):
            await T.sync(self.pool, client, 1, NOW)

        async def fake(system, prompt, max_tokens):
            self.assertIn('Козлов Илья', prompt)
            self.assertIn('Пришлите условия', prompt)
            return {'kind': 'client', 'rop_line': 'Запросил условия'}, 0

        self.assertEqual(await tgreview.process(self.pool, 1, NOW, call=fake), 1)
        reviewed = await self.pool.fetchval("SELECT analysis->>'reviewed' FROM tg_chats WHERE peer_id=501")
        self.assertEqual(reviewed, 'true')
        self.assertIsNone(await self.pool.fetchval('SELECT analyzed_at FROM tg_chats WHERE peer_id=502'))

    async def test_disabled_without_keys(self):
        with patch.dict(os.environ, {'TG_CHATS': 'on', 'TG_API_ID': '', 'TG_API_HASH': ''}):
            self.assertFalse(T.enabled())
            self.assertIsNone(await T.maybe_run(self.pool))
