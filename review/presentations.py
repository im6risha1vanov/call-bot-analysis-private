"""Разбор презентаций из реестра Григория «Реестр звонков с клиентами».

Другой бот кладёт в Google-таблицу строку на каждую презентацию: №, дата, время,
менеджер, ссылка «Слушать» на mp3 на Google Диске, комментарий. Здесь:

1. sync: читаем реестр (только чтение, в таблицу ничего не пишем) и заводим новые
   файлы в таблицу presentations;
2. process: скачиваем mp3, распознаём (Deepgram, как записи Mango), ищем тот же звонок
   в Mango и разбираем по промту презентации (presentation_prompt.md).

Время в реестре — время звонка (подтвердил Григорий 20.10), поэтому звонок Mango
сначала ищется по менеджеру и времени, длительность подтверждает. Если по времени не
нашлось (время пустое, как в строке 40), ищем по дню (и двум дням до него) и
длительности, а текст подтверждает. Совпадение даёт номер клиента, по нему сделка.

Включается PRESENTATIONS=on; реестр — PRESENTATIONS_SHEET_ID. Сервисному аккаунту
бота нужен доступ на чтение к таблице и к файлам на Диске.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

from . import engine

log = logging.getLogger(__name__)

PROMPT_VERSION = 'presentation_review_v1'
PROMPT = (Path(__file__).with_name('presentation_prompt.md').read_text(encoding='utf-8')
          .split('\n---\n', 1)[-1].strip())
MAX_TOKENS = int(os.getenv('PRESENTATION_MAX_TOKENS', '9000'))
RANGE = os.getenv('PRESENTATIONS_RANGE', 'Звонки!A2:F')
SHEETS_API = 'https://sheets.googleapis.com/v4/spreadsheets/'
DRIVE_API = 'https://www.googleapis.com/drive/v3/files/'
SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly', 'https://www.googleapis.com/auth/drive.readonly']
MAX_ATTEMPTS = 3
PER_RUN = int(os.getenv('PRESENTATIONS_PER_RUN', '3'))
SYNC_MINUTES = 10
DURATION_SLACK = 3  # секунд: mp3 из реестра и запись Mango одной длины
LOOK_BACK_DAYS = 2  # без времени: файл могут отправить в чат на день-два позже звонка
TIME_WINDOW = timedelta(minutes=10)  # время в реестре и начало звонка в Mango
TIME_EXACT = timedelta(minutes=2)  # так близко по времени — тот самый звонок, даже если длина не сверилась
MIN_WORDS_SHARED = 0.3  # доля общих слов, чтобы считать тексты одним разговором

STAGES = ('paid', 'contract', 'decision_date', 'materials', 'dmp_meeting', 'none')
TEMPERATURES = {'hot', 'warm', 'cold', 'lost'}
KINDS = {'presentation', 'followup'}
CATEGORIES = {'no_discovery', 'generic_pitch', 'price_no_value', 'objection_unhandled', 'no_trust', 'no_dmp',
              'no_next_step', 'pushy', 'unanswered_question', 'other'}
CONTRACT_FIELDS = ('legal_name', 'inn', 'signer', 'service', 'package', 'competitors', 'region', 'start', 'delivery')
FILE_ID = re.compile(r'(?:/file/d/|[?&]id=)([\w-]{10,})')

_last_sync: datetime | None = None


def enabled() -> bool:
    return os.getenv('PRESENTATIONS', 'off').strip().lower() == 'on' and bool(os.getenv('PRESENTATIONS_SHEET_ID'))


def norm_name(text) -> set[str]:
    return set(re.findall(r'\w+', str(text or '').lower().replace('ё', 'е')))


def parse_rows(values: list[list]) -> list[dict]:
    """Строки реестра (значения с формулами): №, дата, время, менеджер, =HYPERLINK(...), комментарий."""
    out = []
    for row in values:
        row = list(row) + [''] * (6 - len(row))
        found = FILE_ID.search(str(row[4]))
        if not found:
            continue
        try:
            day = datetime.strptime(str(row[1]).strip(), '%d.%m.%Y').date()
        except ValueError:
            day = None
        try:
            number = int(str(row[0]).strip())
        except ValueError:
            number = None
        out.append({'file_id': found[1], 'row_no': number, 'day': day, 'time_text': str(row[2]).strip() or None,
                    'manager_name': str(row[3]).strip() or None, 'comment': str(row[5]).strip() or None})
    return out


def match_employee(name, employees) -> str | None:
    """«Илья Козлов» → добавочный менеджера, у которого совпадает больше всего слов имени. Ничья — не угадываем."""
    words = norm_name(name)
    scored = sorted(((len(words & norm_name(e['full_name'])), e['extension']) for e in employees), reverse=True)
    if not scored or scored[0][0] == 0 or (len(scored) > 1 and scored[1][0] == scored[0][0]):
        return None
    return scored[0][1]


def token() -> str:
    from google.auth.transport.requests import Request
    from google.oauth2.service_account import Credentials
    credentials = Credentials.from_service_account_file(os.environ['GOOGLE_SERVICE_ACCOUNT_FILE'], scopes=SCOPES)
    credentials.refresh(Request())
    return credentials.token


async def read_registry(http: httpx.AsyncClient, access_token: str) -> list[list]:
    """Строки реестра как их видно в таблице, а в колонке «Запись» — сама формула =HYPERLINK со ссылкой.

    Всё читать формулами нельзя: дата, время и № тогда приходят числами (46316, 0.34) вместо «20.10.2026»."""
    async def get(render: str, cells: str) -> list[list]:
        r = await http.get(SHEETS_API + os.environ['PRESENTATIONS_SHEET_ID'] + '/values/' + cells,
                           params={'valueRenderOption': render}, headers={'Authorization': 'Bearer ' + access_token})
        r.raise_for_status()
        return r.json().get('values') or []
    sheet, cells = RANGE.split('!', 1)
    first_row = re.match(r'[A-Z]+(\d+)', cells)[1]
    shown, links = await get('FORMATTED_VALUE', RANGE), await get('FORMULA', f'{sheet}!E{first_row}:E')
    rows = []
    for i, row in enumerate(shown):
        row = list(row) + [''] * (6 - len(row))
        if i < len(links) and links[i]:
            row[4] = links[i][0]
        rows.append(row)
    return rows


def drive_reason(r) -> str:
    """Понятная причина отказа Диска: выключен Drive API, нет доступа к файлу или файла нет."""
    try:
        error = r.json().get('error') or {}
    except ValueError:
        error = {}
    reasons = {e.get('reason') for e in error.get('errors') or []}
    detail = (error.get('message') or '')[:200]
    if reasons & {'accessNotConfigured', 'SERVICE_DISABLED'} or 'has not been used' in detail:
        return 'в Google-проекте бота выключен Google Drive API (включить в Cloud Console) · ' + detail
    if r.status_code == 404 or 'notFound' in reasons:
        return 'файл не найден: у бота нет доступа (откройте папку с записями сервисному аккаунту) или файл удалён'
    return f'нет доступа к файлу ({", ".join(sorted(filter(None, reasons))) or "403"}) · {detail}'


async def download(http: httpx.AsyncClient, access_token: str, file_id: str) -> bytes:
    r = await http.get(DRIVE_API + file_id, params={'alt': 'media', 'supportsAllDrives': 'true'},
                       headers={'Authorization': 'Bearer ' + access_token}, follow_redirects=True)
    if r.status_code in (403, 404):
        raise PermissionError(f'Диск {r.status_code}: {drive_reason(r)}')
    r.raise_for_status()
    return r.content


async def sync(pool, client_id: int, values: list[list]) -> int:
    """Новые строки реестра в presentations. Уже заведённые файлы не трогаем, кроме полей самой строки."""
    employees = await pool.fetch("SELECT extension, full_name FROM employees WHERE client_id=$1 AND role='manager'", client_id)
    added = 0
    for row in parse_rows(values):
        ext = match_employee(row['manager_name'], employees)
        status = await pool.fetchval(
            """INSERT INTO presentations(client_id, file_id, row_no, day, time_text, manager_name, extension, comment)
               VALUES($1,$2,$3,$4,$5,$6,$7,$8)
               ON CONFLICT(client_id, file_id) DO UPDATE SET row_no=EXCLUDED.row_no, day=EXCLUDED.day,
                 time_text=EXCLUDED.time_text, manager_name=EXCLUDED.manager_name,
                 extension=coalesce(presentations.extension, EXCLUDED.extension), comment=EXCLUDED.comment,
                 -- в реестре поменялись дата или время: звонок Mango ищем заново и разбираем с историей клиента
                 -- (расшифровка сохранена, повторно файл не качаем)
                 status=CASE WHEN (presentations.day, presentations.time_text) IS DISTINCT FROM (EXCLUDED.day, EXCLUDED.time_text)
                             THEN 'new' ELSE presentations.status END,
                 attempts=CASE WHEN (presentations.day, presentations.time_text) IS DISTINCT FROM (EXCLUDED.day, EXCLUDED.time_text)
                               THEN 0 ELSE presentations.attempts END
               RETURNING (xmax = 0)""",
            client_id, row['file_id'], row['row_no'], row['day'], row['time_text'], row['manager_name'], ext, row['comment'])
        added += bool(status)
    return added


def shared_words(a: str, b: str) -> float:
    def words(text):
        return {w for w in engine.norm(re.sub(r'\[[^\]]*\]', ' ', text or '')).split() if len(w) > 3}
    left, right = words(a), words(b)
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def registry_time(day: date | None, time_text: str | None, tz) -> datetime | None:
    m = re.fullmatch(r'(\d{1,2})[:.](\d{2})', (time_text or '').strip())
    if not (day and m) or int(m[1]) > 23:
        return None
    return datetime.combine(day, datetime.min.time(), tz).replace(hour=int(m[1]), minute=int(m[2]))


async def find_call(pool, client_id: int, ext: str | None, day: date | None, seconds: int | None, transcript: str, tz,
                    time_text: str | None = None) -> dict:
    """Звонок Mango того же менеджера: сначала по времени из реестра, потом по дню и длине.

    Возвращает {'call_id', 'by', 'candidates', ...}. Иначе запись ждёт ручного выбора в CRM."""
    at = registry_time(day, time_text, tz)
    if ext and at:
        rows = await pool.fetch(
            """SELECT id, client_number, call_started_at, duration_seconds FROM calls
               WHERE client_id=$1 AND extension=$2 AND call_started_at BETWEEN $3 AND $4
               ORDER BY abs(extract(epoch FROM call_started_at - $5))""",
            client_id, ext, at - TIME_WINDOW, at + TIME_WINDOW + timedelta(minutes=1), at)
        near = [{'call_id': r['id'], 'number': r['client_number'], 'at': r['call_started_at'].isoformat(),
                 'seconds': r['duration_seconds'], 'minutes_off': round(abs((r['call_started_at'] - at).total_seconds()) / 60, 1)}
                for r in rows]
        sized = [c for c in near if seconds and c['seconds'] is not None and abs(c['seconds'] - seconds) <= DURATION_SLACK]
        if len(sized) >= 1:
            return {'call_id': sized[0]['call_id'], 'by': 'time+duration', 'candidates': near}
        exact = [c for c in near if c['minutes_off'] * 60 <= TIME_EXACT.total_seconds()]
        if len(exact) == 1:
            return {'call_id': exact[0]['call_id'], 'by': 'time', 'candidates': near}
    if not (ext and day and seconds):
        return {'call_id': None, 'by': None, 'candidates': [], 'reason': 'нет менеджера, дня или длительности'}
    start = datetime.combine(day - timedelta(days=LOOK_BACK_DAYS), datetime.min.time(), tz)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tz)
    rows = await pool.fetch(
        """SELECT id, client_number, call_started_at, duration_seconds, transcript FROM calls
           WHERE client_id=$1 AND extension=$2 AND call_started_at >= $3 AND call_started_at < $4
             AND abs(coalesce(duration_seconds, 0) - $5) <= $6
           ORDER BY abs(coalesce(duration_seconds, 0) - $5), call_started_at DESC""",
        client_id, ext, start, end, seconds, DURATION_SLACK)
    candidates = [{'call_id': r['id'], 'number': r['client_number'], 'at': r['call_started_at'].isoformat(),
                   'seconds': r['duration_seconds'], 'text': round(shared_words(transcript, r['transcript']), 2)
                   if r['transcript'] else None} for r in rows]
    texts = [c for c in candidates if c['text'] is not None and c['text'] >= MIN_WORDS_SHARED]
    if len(texts) == 1:
        return {'call_id': texts[0]['call_id'], 'by': 'duration+text', 'candidates': candidates}
    if len(candidates) == 1 and candidates[0]['text'] is None:
        return {'call_id': candidates[0]['call_id'], 'by': 'duration', 'candidates': candidates}
    reason = 'нет звонка такой длины' if not candidates else 'несколько похожих звонков'
    return {'call_id': None, 'by': None, 'candidates': candidates, 'reason': reason}


def validate(data: dict, transcript: str) -> dict:
    """Согласует ответ модели с расшифровкой: этап засчитывается только с проверенной репликой клиента."""
    data = dict(data or {})
    lines = engine.turns(transcript)
    if data.get('kind') not in KINDS:
        return {**data, 'kind': 'not_presentation', 'reviewed': False, 'moved': False, 'stage_after': 'none',
                'key_moment': None, 'manager_text': None, 'details': None}
    evidence = [e for e in data.get('stage_evidence') or [] if isinstance(e, dict) and engine.quote_ok(e, data, lines)]
    stage = data.get('stage_after') if data.get('stage_after') in STAGES else 'none'
    unverified = stage != 'none' and not evidence
    if unverified:
        stage = 'none'
    temperature = data.get('temperature') if data.get('temperature') in TEMPERATURES else 'warm'
    moment = data.get('key_moment') if isinstance(data.get('key_moment'), dict) else None
    if moment:
        if moment.get('category') not in CATEGORIES:
            moment['category'] = 'other'
        if moment.get('quote') and not engine.quote_ok(moment, data, lines):
            unverified = unverified or engine.norm(moment['quote']) in engine.norm(data.get('manager_text'))
            moment = engine.strip_quote(moment)
    moved = stage != 'none'
    if moved and temperature == 'hot':
        moment = None
    contract = data.get('contract') if isinstance(data.get('contract'), dict) else {}
    # Клиент отказался: список «не хватает для договора» только шумит.
    contract['missing'] = [] if temperature == 'lost' else [f for f in contract.get('missing') or [] if f in CONTRACT_FIELDS]
    data.update(
        stage_after=stage, stage_evidence=evidence, moved=moved, temperature=temperature, key_moment=moment,
        contract=contract, reviewed=True, text_unverified=unverified,
        other_misses=[m if engine.quote_ok(m, data, lines) else engine.strip_quote(m)
                      for m in data.get('other_misses') or [] if isinstance(m, dict)][:2],
        positives=[p for p in data.get('positives') or [] if isinstance(p, dict)
                   and (not p.get('quote') or engine.quote_ok(p, data, lines))][:2])
    return data


async def previous(pool, client_id: int, number: str | None) -> list[str]:
    """Что известно о клиенте: итоги прошлых разборов по этому номеру (звонки и презентации)."""
    if not number:
        return []
    rows = await pool.fetch(
        """SELECT c.call_started_at AS at, r.data->>'rop_line' AS line FROM call_reviews r JOIN calls c ON c.id=r.call_id
           WHERE r.client_id=$1 AND c.client_number=$2 AND r.data->>'rop_line' IS NOT NULL
           UNION ALL
           SELECT c.call_started_at, p.data->>'rop_line' FROM presentations p JOIN calls c ON c.id=p.call_id
           WHERE p.client_id=$1 AND c.client_number=$2 AND p.status='reviewed'
           ORDER BY 1 DESC LIMIT 6""", client_id, number)
    return [f"{r['at']:%d.%m}: {r['line']}" for r in rows if r['line']]


def build_input(meta: dict, transcript: str) -> str:
    head = [f"Менеджер: {meta.get('manager') or 'не указан'}",
            f"Дата: {meta.get('date') or 'не указана'}",
            f"Длительность: {meta.get('duration') or 'не указана'}"]
    if meta.get('history'):
        head.append('Что было с этим клиентом раньше (сначала новое):\n' + '\n'.join(meta['history']))
    return '\n'.join(head) + '\n\nРАСШИФРОВКА:\n' + transcript


async def review_one(pool, row, http, access_token, *, transcribe=None, call=None) -> None:
    """Одна запись: скачать, распознать, найти звонок Mango, разобрать, сохранить."""
    from zoneinfo import ZoneInfo
    if transcribe is None:
        from deepgram_client import transcribe_bytes as transcribe
    if call is None:
        from analysis import _call as call
    tz = ZoneInfo(await pool.fetchval('SELECT timezone FROM clients WHERE id=$1', row['client_id']) or 'Europe/Moscow')
    transcript, seconds = row['transcript'], row['duration_seconds']
    if not transcript:
        audio = await download(http, access_token, row['file_id'])
        transcript, seconds = await transcribe(audio)
        seconds = int(round(seconds or 0))
        await pool.execute('UPDATE presentations SET transcript=$2, duration_seconds=$3, updated_at=now() WHERE id=$1',
                           row['id'], transcript, seconds)
    match = await find_call(pool, row['client_id'], row['extension'], row['day'], seconds, transcript, tz, row['time_text'])
    number = (next((c['number'] for c in match['candidates'] if c['call_id'] == match['call_id']), None)
              if match['call_id'] else None)
    if not (transcript or '').strip():
        data, cost = validate({'kind': 'not_presentation'}, ''), 0.0
    else:
        meta = {'manager': row['manager_name'], 'date': row['day'].strftime('%d.%m.%Y') if row['day'] else None,
                'duration': f'{seconds // 60} мин {seconds % 60} с' if seconds else None,
                'history': await previous(pool, row['client_id'], number)}
        raw, cost = await call(PROMPT, build_input(meta, transcript), MAX_TOKENS)
        data = validate(raw, transcript)
    await pool.execute(
        """UPDATE presentations SET status=$2, call_id=$3, match=$4::jsonb, data=$5::jsonb, prompt_version=$6,
             cost_units=$7, error=NULL, updated_at=now() WHERE id=$1""",
        row['id'], 'reviewed' if data.get('reviewed') else 'skipped', match['call_id'],
        json.dumps(match, ensure_ascii=False), json.dumps(data, ensure_ascii=False), PROMPT_VERSION, cost)
    if cost:
        from .prompts import add_spend
        await add_spend(pool, row['client_id'], cost)


async def process(pool, http, access_token, limit: int = PER_RUN, **kw) -> int:
    rows = await pool.fetch(
        "SELECT * FROM presentations WHERE status IN ('new','error') AND attempts < $1 ORDER BY day NULLS LAST, row_no LIMIT $2",
        MAX_ATTEMPTS, limit)
    done = 0
    for row in rows:
        await pool.execute('UPDATE presentations SET attempts=attempts+1 WHERE id=$1', row['id'])
        try:
            await review_one(pool, row, http, access_token, **kw)
            done += 1
        except Exception as exc:
            log.exception('презентация %s не разобрана', row['file_id'])
            await pool.execute("UPDATE presentations SET status='error', error=$2, updated_at=now() WHERE id=$1",
                               row['id'], str(exc)[:500])
    return done


async def maybe_run(pool, now: datetime | None = None) -> int:
    """Из цикла astra_worker: раз в SYNC_MINUTES читаем реестр и разбираем несколько новых записей."""
    global _last_sync
    if not enabled():
        return 0
    now = now or datetime.now(timezone.utc)
    if _last_sync and now - _last_sync < timedelta(minutes=SYNC_MINUTES):
        return 0
    _last_sync = now
    client_id = await pool.fetchval('SELECT id FROM clients WHERE processing_enabled ORDER BY id LIMIT 1')
    if client_id is None:
        return 0
    import asyncio
    access_token = await asyncio.to_thread(token)
    async with httpx.AsyncClient(timeout=300) as http:
        await sync(pool, client_id, await read_registry(http, access_token))
        return await process(pool, http, access_token)


if __name__ == '__main__':
    # python -m review.presentations check — только чтение: видит ли бот реестр и файлы на Диске.
    import asyncio
    import sys

    async def _check():
        from scripts.kitchen_backfill import load_env
        load_env(Path(__file__).resolve().parents[1] / '.env')
        os.environ.setdefault('PRESENTATIONS_SHEET_ID', '1K7UJe2eaZ7NPhleSbPpgRuAV7PbtmfE0Y4HxyPCiers')
        access_token = token()
        async with httpx.AsyncClient(timeout=120) as http:
            rows = parse_rows(await read_registry(http, access_token))
            print('строк с записью в реестре:', len(rows))
            for row in rows[:2] + rows[-1:]:
                try:
                    audio = await download(http, access_token, row['file_id'])
                    print(row['row_no'], row['day'], row['manager_name'], 'скачан,', len(audio) // 1024, 'КБ')
                except Exception as exc:
                    print(row['row_no'], row['day'], row['manager_name'], 'НЕ скачан:', exc)

    async def _matches():
        # python -m review.presentations matches — только чтение: почему запись не привязалась к звонку.
        # По каждой расшифрованной записи: время из реестра, длина mp3 и разговоры менеджера в тот день.
        import asyncpg
        from zoneinfo import ZoneInfo
        from scripts.kitchen_backfill import load_env
        load_env(Path(__file__).resolve().parents[1] / '.env')
        pool = await asyncpg.create_pool(os.environ['DATABASE_URL'], min_size=1, max_size=2)
        try:
            rows = await pool.fetch(
                """SELECT p.*, cl.timezone FROM presentations p JOIN clients cl ON cl.id=p.client_id
                   WHERE p.duration_seconds IS NOT NULL ORDER BY p.day NULLS LAST, p.row_no""")
            print('расшифровано записей:', len(rows), '· привязано:', sum(1 for r in rows if r['call_id']))
            for r in rows:
                tz = ZoneInfo(r['timezone'] or 'Europe/Moscow')
                match = json.loads(r['match']) if r['match'] else {}
                print(f"\n№{r['row_no']} {r['day']} {r['time_text']} доб.{r['extension']} mp3 {r['duration_seconds']} с → "
                      f"{match.get('by') or 'не найден: ' + str(match.get('reason'))}")
                if not (r['day'] and r['extension']):
                    continue
                start = datetime.combine(r['day'] - timedelta(days=1), datetime.min.time(), tz)
                calls = await pool.fetch(
                    """SELECT id, call_started_at, duration_seconds, direction FROM calls
                       WHERE client_id=$1 AND extension=$2 AND call_started_at >= $3 AND call_started_at < $4
                         AND duration_seconds >= 60 ORDER BY call_started_at""",
                    r['client_id'], r['extension'], start, start + timedelta(days=2))
                print('   разговоры от минуты за этот и прошлый день:',
                      ', '.join(f"{c['call_started_at'].astimezone(tz):%d.%m %H:%M} {c['duration_seconds']}с"
                                f"{'' if c['direction'] == 2 else ' вх'}" for c in calls) or 'нет')
        finally:
            await pool.close()

    if sys.argv[1:] == ['check']:
        asyncio.run(_check())
    elif sys.argv[1:] == ['matches']:
        asyncio.run(_matches())
