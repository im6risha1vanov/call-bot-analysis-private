"""Новые звонки Ольги и Оксаны в ежедневных разборах CRM."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

from . import callcenter_quality


PREFIX = 'callcenter-monitor:'
RULESET = '20261117-v3'
OPERATORS = {'olga': 'ольга', 'oksana': 'оксана'}
INTERVAL = 60
LOOKBACK = timedelta(minutes=45)


def script_for(dedup_key: str) -> str | None:
    return dedup_key.rsplit(':', 1)[1] if dedup_key.startswith(PREFIX) else None


async def enqueue(pool) -> None:
    client_id = os.getenv('CALLCENTER_MONITOR_CLIENT_ID')
    if not client_id:
        return
    since = datetime.fromisoformat(os.environ['CALLCENTER_MONITOR_FROM'])
    if since.tzinfo is None:
        raise ValueError('CALLCENTER_MONITOR_FROM must include an offset')
    client_id = int(client_id)

    from . import call_analysis

    employees = await call_analysis._employees(pool, client_id)
    operators = []
    for operator in call_analysis.operator_specs(employees=employees):
        if not operator['configured'] or operator['id'] not in OPERATORS:
            continue
        first_name = OPERATORS[operator['id']]
        extension = operator['extension']
        if not any(e['extension'] == extension and not e['archived_at']
                   and first_name in (e['full_name'] or '').casefold().split() for e in employees):
            continue
        script = os.getenv(f'CALLCENTER_MONITOR_{operator["id"].upper()}_SCRIPT', 'auto')
        if script != 'auto' and script not in callcenter_quality.SCRIPTS:
            raise ValueError(f'Unknown call center script for {operator["id"]}')
        operators.append((operator['id'], extension, script))

    if not operators:
        return
    async with pool.acquire() as conn, conn.transaction():
        if not await conn.fetchval("SELECT to_regclass('callcenter_monitor_state') IS NOT NULL"):
            await conn.execute("""CREATE TABLE IF NOT EXISTS callcenter_monitor_state (
                client_id INTEGER PRIMARY KEY REFERENCES clients(id) ON DELETE CASCADE,
                scanned_at TIMESTAMPTZ NOT NULL)""")
        for column, kind in (('full_scanned_at', 'TIMESTAMPTZ'), ('scan_signature', 'TEXT')):
            if not await conn.fetchval("SELECT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid='callcenter_monitor_state'::regclass AND attname=$1 AND NOT attisdropped)", column):
                await conn.execute(f'ALTER TABLE callcenter_monitor_state ADD COLUMN IF NOT EXISTS {column} {kind}')
        if not await conn.fetchval("SELECT to_regclass('callcenter_audio_retries') IS NOT NULL"):
            await conn.execute('''CREATE TABLE IF NOT EXISTS callcenter_audio_retries (
                item_id BIGINT PRIMARY KEY REFERENCES call_analysis_items(id) ON DELETE CASCADE,
                attempts INTEGER NOT NULL DEFAULT 0, next_at TIMESTAMPTZ NOT NULL)''')
        await conn.execute("INSERT INTO callcenter_monitor_state(client_id,scanned_at) VALUES($1,$2) ON CONFLICT DO NOTHING", client_id, since)
        state = await conn.fetchrow(
            'SELECT scanned_at,full_scanned_at,scan_signature FROM callcenter_monitor_state WHERE client_id=$1 FOR UPDATE', client_id)
        scan_at = await conn.fetchval('SELECT clock_timestamp()')
        since_update = max(since, state['scanned_at'] - LOOKBACK)
        signature = json.dumps([RULESET, operators], ensure_ascii=True, sort_keys=True)
        full = (state['full_scanned_at'] is None or state['scan_signature'] != signature
                or scan_at - state['full_scanned_at'] >= timedelta(days=1))
        await conn.execute('''INSERT INTO callcenter_audio_retries(item_id,next_at)
            SELECT i.id,coalesce(i.processed_at,now()) + interval '5 minutes'
            FROM call_analysis_items i JOIN call_analysis_runs r ON r.id=i.run_id
            WHERE i.client_id=$1 AND r.dedup_key LIKE $2 AND i.status='transcript_failed'
              AND i.transcript_source='transcript_failed'
              AND ($3::boolean OR i.processed_at >= $4)
            ON CONFLICT(item_id) DO NOTHING''', client_id, PREFIX + '%', full, since_update)
        for operator_id, extension, script in operators:
            days = await conn.fetch(
                """SELECT (call_started_at AT TIME ZONE 'Europe/Moscow')::date AS day
                   FROM calls WHERE client_id=$1 AND extension=$2 AND direction IN (1,2)
                     AND call_started_at >= $3 AND ($5::boolean OR updated_at >= $4)
                   UNION
                   SELECT period_start FROM call_analysis_runs WHERE client_id=$1 AND extension=$2 AND operator=$6
                     AND period_end_at > $3 AND dedup_key LIKE $7 AND dedup_key NOT LIKE $8
                   UNION
                   SELECT r.period_start FROM call_analysis_runs r JOIN call_analysis_items i ON i.run_id=r.id
                   JOIN callcenter_audio_retries retry ON retry.item_id=i.id
                   WHERE r.client_id=$1 AND r.extension=$2 AND r.operator=$6 AND r.dedup_key LIKE $7
                     AND i.status='transcript_failed' AND i.transcript_source='transcript_failed'
                     AND retry.attempts < 3 AND retry.next_at <= now()
                   UNION
                   SELECT r.period_start FROM call_analysis_runs r JOIN call_analysis_items i ON i.run_id=r.id
                   WHERE r.client_id=$1 AND r.extension=$2 AND r.operator=$6 AND r.dedup_key LIKE $7
                     AND r.status IN ('done','failed') AND i.status='pending'
                   ORDER BY day""", client_id, extension, since, since_update, full, operator_id,
                PREFIX + '%', f'{PREFIX}%:{RULESET}:{script}')
            for row in days:
                day = row['day']
                start, end = call_analysis.period_bounds(day, day)
                await _enqueue_day(conn, client_id, operator_id, extension, script, day, max(start, since), end)
        await conn.execute('UPDATE callcenter_monitor_state SET scanned_at=$2, full_scanned_at=CASE WHEN $3 THEN $2 ELSE full_scanned_at END, scan_signature=$4 WHERE client_id=$1', client_id, scan_at, full, signature)


async def _enqueue_day(conn, client_id, operator, extension, script, day, start, end) -> None:
    key = f'{PREFIX}{operator}:{extension}:{day.isoformat()}:{RULESET}:{script}'
    run = await conn.fetchrow(
        """SELECT id,dedup_key FROM call_analysis_runs WHERE client_id=$1 AND operator=$2 AND extension=$3
             AND period_start=$4 AND dedup_key LIKE $5 ORDER BY id DESC LIMIT 1 FOR UPDATE""",
        client_id, operator, extension, day, PREFIX + '%')
    if run and run['dedup_key'] != key:
        await conn.execute(
            """UPDATE call_analysis_runs SET dedup_key=$2, status='queued', stage='Обновляю проверку скрипта',
                 processed=0, result=NULL, error=NULL, saved_at=NULL, finished_at=NULL WHERE id=$1""", run['id'], key)
        await conn.execute("UPDATE call_analysis_items SET status='pending', processed_at=NULL WHERE run_id=$1", run['id'])

    # Не отбираем свежую запись у основного бота, который уже поставил её на расшифровку.
    ready = """c.client_id=$1 AND c.extension=$2 AND c.direction IN (1,2)
        AND c.call_started_at >= $3 AND c.call_started_at < $4
        AND (nullif(btrim(c.transcript),'') IS NOT NULL OR
             c.status NOT IN ('new','processing','awaiting_record') OR
             c.updated_at <= now() - interval '20 minutes')"""
    run_id = run['id'] if run else await conn.fetchval(
        f"""INSERT INTO call_analysis_runs(client_id, operator, extension, kind, source, period_start, period_end,
             period_start_at, period_end_at, dedup_key, status, stage, author_name)
             SELECT $1,$5,$2,'operator','calls',$6,$6,$3,$4,$7,'queued','Ждёт обработки','Авторазбор колл-центра'
             WHERE EXISTS (SELECT 1 FROM calls c WHERE {ready})
             ON CONFLICT (client_id, dedup_key) DO NOTHING RETURNING id""", client_id, extension, start, end, operator, day, key)
    if run_id is None:
        return
    await conn.fetchval(
        f"""WITH updated AS (
             INSERT INTO call_analysis_items(run_id,client_id,call_ref,call_id,phone,at,duration_s,status)
             SELECT $5,$1,'calls:'||c.id,c.id,c.client_number,c.call_started_at,coalesce(c.duration_seconds,0),'pending'
             FROM calls c LEFT JOIN call_analysis_items old ON old.run_id=$5 AND old.call_id=c.id
             WHERE {ready} AND (old.id IS NULL OR (
                  old.status <> 'pending' AND (
                    (old.status NOT IN ('failed','missing_call') AND NOT (old.stages ? 'script_check')) OR
                    old.duration_s IS DISTINCT FROM coalesce(c.duration_seconds,0) OR
                    old.phone IS DISTINCT FROM c.client_number OR
                    old.at IS DISTINCT FROM c.call_started_at OR
                    c.updated_at > old.processed_at OR
                    ((old.transcript_source IN ('none','transcript_failed') OR old.status='failed')
                     AND nullif(btrim(c.transcript),'') IS NOT NULL) OR
                    (old.transcript_source='none' AND c.recording_id IS NOT NULL))))
             ON CONFLICT (run_id,call_ref) DO UPDATE SET status='pending', phone=excluded.phone, at=excluded.at,
                duration_s=excluded.duration_s, error=NULL, processed_at=NULL
             RETURNING id)
             SELECT count(*) FROM updated""", client_id, extension, start, end, run_id)
    await conn.fetchval('''WITH due AS (
        UPDATE callcenter_audio_retries retry SET attempts=retry.attempts+1,
          next_at=now() + CASE retry.attempts WHEN 0 THEN interval '30 minutes' ELSE interval '2 hours' END
        FROM call_analysis_items i WHERE i.id=retry.item_id AND i.run_id=$1
          AND i.status='transcript_failed' AND i.transcript_source='transcript_failed'
          AND retry.attempts < 3 AND retry.next_at <= now()
        RETURNING retry.item_id
    ), requeued AS (
        UPDATE call_analysis_items SET status='pending',processed_at=NULL,error=NULL
        WHERE id IN (SELECT item_id FROM due) RETURNING id
    ) SELECT count(*) FROM requeued''', run_id)
    await conn.execute(
            """UPDATE call_analysis_runs SET status='queued', stage='Есть новые звонки', finished_at=NULL, error=NULL,
                 total_calls=(SELECT count(*) FROM call_analysis_items WHERE run_id=$1),
                 processed=(SELECT count(*) FROM call_analysis_items WHERE run_id=$1 AND status <> 'pending')
                 WHERE id=$1 AND status <> 'running'
                   AND EXISTS(SELECT 1 FROM call_analysis_items WHERE run_id=$1 AND status='pending')""", run_id)


async def telegram_summary(pool, client_id: int) -> str:
    rows = await pool.fetch(
        """SELECT DISTINCT ON (operator) operator, period_start, status, result, total_calls, processed
           FROM call_analysis_runs WHERE client_id=$1 AND dedup_key LIKE $2
           ORDER BY operator, period_start DESC, id DESC""", client_id, PREFIX + '%')
    reports = {row['operator']: row for row in rows}
    lines = ['Колл-центр · последние автоматические сводки']
    for operator, name in (('olga', 'Ольга'), ('oksana', 'Оксана')):
        row = reports.get(operator)
        if row is None:
            lines.append(f'\n{name}\nСводка пока не сформирована.')
            continue
        lines.append(f'\n{name} · {row["period_start"]:%d.%m.%Y} (Москва)')
        result = row['result']
        if isinstance(result, str):
            result = json.loads(result)
        if row['status'] != 'done':
            stage = 'Ошибка разбора' if row['status'] == 'failed' else 'Разбор в процессе'
            lines.append(f'{stage}. Обработано {row["processed"]} из {row["total_calls"]}.')
        if not result or 'quality' not in result:
            continue
        quality = result['quality']
        base, states = quality['base'], quality['states']
        full, review = states.get('complete', 0), states.get('review', 0)
        lines.append(f'Скрипт · все шаги найдены в {full} из {full + review}, '
                     f'проверить {review}. Всего звонков {result["total"]}.')
        if quality.get('topics'):
            lines.append('Темы · ' + ', '.join(f'{name} {count}' for name, count in quality['topics'].items()) + '.')
        for direction, counts in quality.get('directions', {}).items():
            if counts.get('complete') or counts.get('review'):
                lines.append(f'{direction} · все шаги {counts.get("complete", 0)}, проверить {counts.get("review", 0)}.')
        if quality.get('warnings'):
            lines.extend(f'Проверить · {label}: {count}.' for label, count in quality['warnings'].items())
        skipped = states.get('other_topic', 0) + states.get('unknown', 0)
        if skipped:
            lines.append(f'Без оценки из-за темы или качества записи: {skipped}.')
        lines.append(f'База · контактов {base["contacts"]}, живой разговор подтверждён по {base["reached"]}, '
                     f'без дозвона {base["not_reached"]}, неясный исход {base["technical"]}.')
        if base.get('talk_recorded') is not None:
            lines.append(f'Mango зафиксировал время разговора по {base["talk_recorded"]} номерам.')
        if base['reach_pct'] is not None:
            lines.append(f'Подтверждённая доля дозвона: {base["reach_pct"]:g} % от прозвоненных номеров.')
        lines.append(f'Повторный недозвон {base["repeated_no_contact"]}, неверный номер {base["wrong_number"]}, '
                     f'отрицали заявку {base["no_request"]}, неактуально {base["not_relevant"]}.')
        if base.get('criteria'):
            lines.append('Критерии базы · ' + ', '.join(f'{label} — {count}' for label, count in base['criteria'].items()) + '.')
        sheet = quality.get('sheet_base')
        if sheet and sheet['status'] == 'ready':
            lines.append(f'Таблицы обзвона · связаны {sheet["matched"]} номеров, '
                         f'в нескольких проектах {sheet["ambiguous"]}, без совпадения {sheet["absent"]}.')
            lines.extend(f'{project["title"]} · прозвонено {project["matched"]}, '
                         f'живой разговор {project["live"]}, без дозвона {project["no_contact"]}.'
                         for project in sheet['projects'] if project['matched'])
        elif sheet:
            lines.append('Таблица обзвона пока недоступна.')
    return '\n'.join(lines)
