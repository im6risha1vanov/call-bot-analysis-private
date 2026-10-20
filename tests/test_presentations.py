"""Разбор презентаций из реестра: строки таблицы, поиск звонка Mango, проверка ответа модели. Данные вымышленные."""
import json
import os
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import asyncpg

os.environ.setdefault('BOT_TOKEN', '1:test')
os.environ.setdefault('CVC_API_KEY', 'test')

from review import presentations as P

DSN = os.environ.get('METHODOLOGY_TEST_DATABASE_URL')
MSK = ZoneInfo('Europe/Moscow')
LINK = '=HYPERLINK("https://drive.google.com/file/d/{}/view";"Слушать")'
TRANSCRIPT = '\n'.join([
    '[00:01 Спикер 0] Добрый день, Олег, это Аверон, как договаривались, расскажу про контакты клиентов ваших конкурентов.',
    '[01:10 Спикер 1] Сколько стоит пакет на тысячу номеров и как быстро начнёте?',
    '[01:20 Спикер 0] Семьдесят тысяч, номера пойдут через неделю после договора.',
    '[02:30 Спикер 1] Дороговато, нам надо подумать с директором.',
    '[03:05 Спикер 0] Хорошо, наберу вам на следующей неделе.',
])
ROLES = {'manager': 'Спикер 0', 'client': 'Спикер 1', 'other': None, 'merged': False}
ANSWER = {
    'roles': ROLES, 'kind': 'presentation', 'stage_after': 'dmp_meeting',
    'stage_evidence': [{'timecode': '02:30', 'speaker': 'client', 'quote': 'нам надо подумать с директором'}],
    'temperature': 'warm',
    'key_moment': {'category': 'price_no_value', 'timecode': '01:20', 'speaker': 'manager',
                   'quote': 'Семьдесят тысяч, номера пойдут через неделю после договора',
                   'what_happened': 'Цена прозвучала без расчёта заявок.', 'say_instead': 'Это около ста заявок в месяц...'},
    'positives': [{'timecode': '00:01', 'speaker': 'manager', 'quote': 'выдуманная цитата', 'why': 'x'}],
    'contract': {'company': 'Ромашка', 'missing': ['inn', 'signer', 'colour']},
    'manager_text': 'Клиент тёплый.', 'rop_line': 'Презентация. Тёплый, пауза без дня.', 'details': '...',
}


class ParseTests(unittest.TestCase):
    def test_rows_and_links(self):
        rows = P.parse_rows([
            ['1', '21.09.2026', '08:11', 'Илья Козлов', LINK.format('17MnFxr91QA2HSwpmQYpZpu3vgZKgTs1k')],
            ['40', '13.10.2026', '', 'Марк Амирханян', LINK.format('1Ab2_dnZiz0RajwDdXi4D2iF8-IyVbufI'), 'клиент Ильи'],
            ['41', '14.10.2026', '09:13', 'Илья Козлов', 'Слушать'],  # без ссылки: пропускаем
        ])
        self.assertEqual([r['file_id'] for r in rows], ['17MnFxr91QA2HSwpmQYpZpu3vgZKgTs1k', '1Ab2_dnZiz0RajwDdXi4D2iF8-IyVbufI'])
        self.assertEqual((rows[0]['row_no'], rows[0]['day'], rows[0]['time_text']), (1, date(2026, 9, 21), '08:11'))
        self.assertEqual((rows[1]['time_text'], rows[1]['comment']), (None, 'клиент Ильи'))

    def test_employee_by_name(self):
        staff = [{'extension': '11', 'full_name': 'Марк Амирханян'}, {'extension': '12', 'full_name': 'Козлов Илья'},
                 {'extension': '10', 'full_name': 'Ефим Лузин'}]
        self.assertEqual(P.match_employee('Илья Козлов', staff), '12')
        self.assertEqual(P.match_employee('Марк Амирханян', staff), '11')
        self.assertIsNone(P.match_employee('Ольга', staff))
        self.assertIsNone(P.match_employee('Марк', staff + [{'extension': '15', 'full_name': 'Марк Петров'}]))

    def test_validate_checks_quotes(self):
        data = P.validate(json.loads(json.dumps(ANSWER)), TRANSCRIPT)
        self.assertEqual((data['stage_after'], data['moved'], data['reviewed']), ('dmp_meeting', True, True))
        self.assertEqual(data['key_moment']['category'], 'price_no_value')
        self.assertIsNotNone(data['key_moment']['quote'])
        self.assertEqual(data['positives'], [])  # цитаты нет в расшифровке
        self.assertEqual(data['contract']['missing'], ['inn', 'signer'])

        fake = {**ANSWER, 'stage_after': 'contract', 'stage_evidence': [{'timecode': '03:00', 'speaker': 'client',
                                                                         'quote': 'присылайте договор'}]}
        data = P.validate(json.loads(json.dumps(fake)), TRANSCRIPT)
        self.assertEqual((data['stage_after'], data['moved'], data['text_unverified']), ('none', False, True))
        self.assertFalse(P.validate({'kind': 'not_presentation'}, TRANSCRIPT)['reviewed'])


@unittest.skipUnless(DSN, 'METHODOLOGY_TEST_DATABASE_URL не задан')
class StoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pool = await asyncpg.create_pool(DSN, min_size=1, max_size=4)
        self.assertTrue((await self.pool.fetchval('SELECT current_database()')).startswith('callbot_methodology_test_'))
        root = Path(__file__).resolve().parents[1]
        if not await self.pool.fetchval("SELECT to_regclass('clients') IS NOT NULL"):
            await self.pool.execute((root / 'tests/bootstrap.sql').read_text())
        await self.pool.execute("""
            ALTER TABLE calls ADD COLUMN IF NOT EXISTS duration_seconds INTEGER DEFAULT 90;
            ALTER TABLE calls ADD COLUMN IF NOT EXISTS client_number TEXT;
            ALTER TABLE calls ADD COLUMN IF NOT EXISTS direction INTEGER DEFAULT 2;
            ALTER TABLE employees ADD COLUMN IF NOT EXISTS id BIGSERIAL;""")
        for name in ('008_call_reviews.sql', '022_presentations.sql'):
            await self.pool.execute((root / 'migrations' / name).read_text())
        await self.pool.execute('TRUNCATE clients, astra_daily_spend CASCADE')
        await self.pool.execute("INSERT INTO clients VALUES(1,'Europe/Moscow',true)")
        await self.pool.execute("INSERT INTO employees VALUES(1,'12',123,'manager','Козлов Илья'),(1,'11',124,'manager','Марк Амирханян')")

    async def asyncTearDown(self):
        await self.pool.close()

    async def call(self, ext, at, seconds, number, transcript=None):
        return await self.pool.fetchval(
            """INSERT INTO calls(client_id,extension,call_started_at,duration_seconds,client_number,transcript)
               VALUES(1,$1,$2,$3,$4,$5) RETURNING id""", ext, at, seconds, number, transcript)

    async def test_sync_match_and_review(self):
        values = [['1', '14.10.2026', '09:13', 'Илья Козлов', LINK.format('fileAAAAAAAAAA')],
                  ['2', '14.10.2026', '11:05', 'Илья Козлов', LINK.format('fileBBBBBBBBBB')],
                  ['3', '14.10.2026', '', 'Кто-то', LINK.format('fileCCCCCCCCCC')],
                  ['4', '16.10.2026', '', 'Илья Козлов', LINK.format('fileDDDDDDDDDD')]]
        self.assertEqual(await P.sync(self.pool, 1, values), 4)
        self.assertEqual(await P.sync(self.pool, 1, values), 0)  # повторное чтение не плодит строки
        self.assertIsNone(await self.pool.fetchval("SELECT extension FROM presentations WHERE row_no=3"))

        # запись 1: звонок той же длины днём раньше, у Mango своя расшифровка того же разговора
        same = await self.call('12', datetime(2026, 10, 13, 15, 0, tzinfo=MSK), 245, '79990001111', TRANSCRIPT)
        await self.call('12', datetime(2026, 10, 13, 16, 0, tzinfo=MSK), 247, '79990002222', '[00:01 Спикер 0] Совсем другой разговор про погоду и рыбалку.')
        await self.call('12', datetime(2026, 10, 9, 15, 0, tzinfo=MSK), 245, '79990003333')  # слишком давно
        await self.call('11', datetime(2026, 10, 14, 15, 0, tzinfo=MSK), 245, '79990004444')  # другой менеджер
        # запись 2 (11:05): звонок в 11:04 той же длины; соседний в 11:12 другой длины, в 12:00 вне окна
        by_time = await self.call('12', datetime(2026, 10, 14, 11, 4, tzinfo=MSK), 100, '79990005555')
        await self.call('12', datetime(2026, 10, 14, 11, 12, tzinfo=MSK), 300, '79990007777')
        await self.call('12', datetime(2026, 10, 14, 12, 0, tzinfo=MSK), 101, '79990006666')
        # запись 4 без времени: два звонка подходящей длины без расшифровки, угадывать нельзя
        await self.call('12', datetime(2026, 10, 16, 11, 0, tzinfo=MSK), 80, '79990008888')
        await self.call('12', datetime(2026, 10, 15, 12, 0, tzinfo=MSK), 81, '79990009999')
        await self.pool.execute(
            """INSERT INTO call_reviews(call_id,client_id,extension,prompt_version,kind,success,chance,category,data)
               VALUES($1,1,'12','v','cold_first',true,null,null,'{"rop_line":"Холодный. Встреча на 13.10."}'::jsonb)""",
            await self.call('12', datetime(2026, 10, 10, 10, 0, tzinfo=MSK), 60, '79990001111'))

        lengths = {'fileAAAAAAAAAA': 246.4, 'fileBBBBBBBBBB': 100.0, 'fileCCCCCCCCCC': 50.0, 'fileDDDDDDDDDD': 80.0}
        downloaded = []

        async def fake_download(http, token, file_id):
            downloaded.append(file_id)
            return file_id.encode()

        async def transcribe(audio):
            return TRANSCRIPT, lengths[audio.decode()]

        seen = []

        async def fake_call(system, user, max_tokens):
            seen.append(user)
            return json.loads(json.dumps(ANSWER)), 0.7

        with patch.object(P, 'download', fake_download):
            self.assertEqual(await P.process(self.pool, None, 't', limit=5, transcribe=transcribe, call=fake_call), 4)
        rows = {r['row_no']: r for r in await self.pool.fetch('SELECT * FROM presentations')}
        first = rows[1]
        self.assertEqual((first['status'], first['call_id'], first['duration_seconds']), ('reviewed', same, 246))
        self.assertEqual(json.loads(first['match'])['by'], 'duration+text')
        self.assertIn('Холодный. Встреча на 13.10.', seen[0])  # история клиента по номеру
        self.assertEqual(json.loads(first['data'])['stage_after'], 'dmp_meeting')
        self.assertEqual((rows[2]['call_id'], json.loads(rows[2]['match'])['by']), (by_time, 'time+duration'))
        fourth = json.loads(rows[4]['match'])
        self.assertEqual((rows[4]['call_id'], fourth['reason'], len(fourth['candidates'])), (None, 'несколько похожих звонков', 2))
        self.assertEqual(json.loads(rows[3]['match'])['reason'], 'нет менеджера, дня или длительности')
        self.assertAlmostEqual(float(await self.pool.fetchval('SELECT sum(spent_units) FROM astra_daily_spend')), 2.8)

        # лист «Презентации» в таблице бота: только разобранные, с телефоном из найденного звонка Mango
        from review import sheets
        table = sheets.presentations_table(await self.pool.fetch(
            """SELECT p.*, c.client_number, c.call_started_at, 'Козлов Илья' AS manager FROM presentations p
               LEFT JOIN calls c ON c.id=p.call_id WHERE p.status='reviewed' ORDER BY p.row_no"""), MSK)
        self.assertEqual(table.rows[0], sheets.PRESENTATIONS_HEADER)
        row = table.rows[1]
        self.assertEqual(row[:7], ['14.10.2026', 'Козлов Илья', row[2], 'Ромашка', 'разговор с ЛПР', 'тёплый', 'цена без расчёта пользы'])
        self.assertIn('9990001111', row[2].replace(' ', '').replace('-', '').replace('(', '').replace(')', ''))
        self.assertEqual(row[9], 'ИНН, кто подписывает')
        self.assertEqual((row[11], row[12]), ('13.10 15:00', 'https://drive.google.com/file/d/fileAAAAAAAAAA/view'))
        self.assertEqual(table.rows[2][11], '14.10 11:04')
        self.assertEqual(table.rows[4][11], 'не найден: несколько похожих звонков')

        # ошибка доступа к Диску: запись помечается, после трёх попыток бот её больше не трогает
        await self.pool.execute("UPDATE presentations SET status='new', transcript=NULL WHERE row_no=3")

        async def denied(http, token, file_id):
            raise PermissionError('Диск 404')

        with patch.object(P, 'download', denied):
            for _ in range(4):
                await P.process(self.pool, None, 't', transcribe=transcribe, call=fake_call)
        row = await self.pool.fetchrow('SELECT status, attempts, error FROM presentations WHERE row_no=3')
        self.assertEqual((row['status'], row['attempts'], row['error']), ('error', 3, 'Диск 404'))
