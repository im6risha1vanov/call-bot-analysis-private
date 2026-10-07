from __future__ import annotations

import asyncio
import json
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from comparison.registry import sha
from comparison.store import decode
from methodology import runtime
from queue_runner import RetryLater
from tools import Forbidden, get_call, report_chat_ids, resolve_actor
from reports import fmt_phone
from . import render

WAIT_FOR_AUXILIARY = timedelta(minutes=30)


def unpack(row):
    data = dict(row)
    for name in ('metadata', 'primary_analysis', 'configuration', 'auxiliary'):
        data[name] = decode(data.get(name))
    return data


async def installed(pool):
    return bool(await pool.fetchval("SELECT to_regclass('call_report_runs') IS NOT NULL"))


async def capture(pool, call_id, client_id, source_key, *, deliver=False, fresh=False, package=None):
    """Snapshot before a retry, mode switch or later reanalysis can change the source."""
    from analysis import CRITERIA, MODEL, SCORE_PROMPT, LEVEL_CRITICAL_CRITERIA, VERDICT_MIN_FAILURES, VERDICT_MIN_FAILURES_WITH_SIGNAL
    from automatic.jobs import enabled
    from combined.evaluation import VERSION as combined_version
    async with (pool.acquire() if hasattr(pool, 'acquire') else nullcontext(pool)) as conn, conn.transaction():
        call = await conn.fetchrow('SELECT * FROM calls WHERE id=$1 AND client_id=$2', call_id, client_id)
        primary = await conn.fetchrow('SELECT * FROM astra_analysis WHERE call_id=$1', call_id)
        if not call or not primary or primary['status'] != 'analyzed':
            raise ValueError('Разбор звонка ещё не сохранён.')
        zone = await conn.fetchval('SELECT timezone FROM clients WHERE id=$1', client_id)
        manager = await conn.fetchval("SELECT full_name FROM employees WHERE client_id=$1 AND extension=$2 AND role='manager'", client_id, call['extension'])
        metadata = {'manager': manager or f"доб. {call['extension']}",
                    'call_started_at': call['call_started_at'].astimezone(ZoneInfo(zone)).isoformat() if call['call_started_at'] else None,
                    'duration_seconds': call['duration_seconds'] or 0,
                    'phone': fmt_phone(call['client_number']) or 'номер не указан', 'timezone': zone}
        base = {'analysis': decode(primary['analysis']) or {}, 'score': primary['score'], 'level': primary['level'],
                'short_report': decode(primary['short_report']) or {}, 'detailed_report': decode(primary['detailed_report'])}
        cfg = {'format_version': render.FORMAT_VERSION, 'mode': runtime.settings()['mode'], 'active': 'original',
               'phone_sha256': render.phone_fingerprint(call['client_number']), 'show_full_phone': True,
               'course_version': runtime.VERSION, 'combined_version': combined_version,
               'expect_course': runtime.shadow_enabled(), 'expect_combined': enabled() and primary['level'] is not None,
               'original_source_known': fresh,
               'model': MODEL if fresh else None, 'score_prompt_sha256': sha(SCORE_PROMPT) if fresh else None,
               'criteria': CRITERIA if fresh else [(r.get('key'), r.get('weight'), r.get('title')) for r in base['analysis'].get('rows', [])],
               'verdict': {'critical': sorted(LEVEL_CRITICAL_CRITERIA), 'failures': VERDICT_MIN_FAILURES,
                           'failures_with_signal': VERDICT_MIN_FAILURES_WITH_SIGNAL} if fresh else None}
        # Historical views never claim provenance that was not saved. For new
        # tasks the package was pinned at enqueue, before any selector change.
        if fresh or package is not None:
            from . import analysis_policy
            cfg.update(package if package is not None else await analysis_policy.snapshot(conn, client_id))
            cfg['format_version'] = cfg['display_version']
            cfg['prompt_hashes'] = {name: sha((Path(__file__).parents[1] / name).read_text())
                                   for name in ('prompt_score.md','prompt_short.md','prompt_review.md')}
        run_id = await conn.fetchval('''INSERT INTO call_report_runs(call_id,client_id,source_key,extension,transcript_sha256,transcript,metadata,primary_analysis,configuration)
            VALUES($1,$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb,$9::jsonb)
            ON CONFLICT(client_id,call_id,source_key) DO UPDATE SET source_key=EXCLUDED.source_key RETURNING id''',
            call_id, client_id, source_key, call['extension'], sha(call['transcript'] or ''), call['transcript'] or '',
            json.dumps(metadata, ensure_ascii=False), json.dumps(base, ensure_ascii=False), json.dumps(cfg))
        if deliver:
            await conn.execute("INSERT INTO tasks(type,client_id,input,dedup_key) VALUES('publish_call_report',$1,$2::jsonb,$3) ON CONFLICT(type,dedup_key) DO NOTHING",
                               client_id, json.dumps({'run_id': run_id}), f'{client_id}:{call_id}:{run_id}')
    return run_id


async def access(pool, actor, run_id):
    if actor is None or actor.role not in {'manager', 'head', 'owner'}:
        raise Forbidden('Доступ к разбору закрыт.')
    row = await pool.fetchrow('SELECT * FROM call_report_runs WHERE id=$1 AND client_id=$2', run_id, actor.client_id)
    if not row:
        raise Forbidden('Разбор не найден или доступ к нему закрыт.')
    if actor.role == 'manager' and row['extension'] != actor.extension:
        raise Forbidden('Этот разбор относится к другому менеджеру.')
    await get_call(pool, actor, row['call_id'])
    return unpack(row)


async def saved_view(pool, actor, call_id):
    """An explicit compatible historical view; never enqueue a paid analysis."""
    if actor is None or actor.role not in {'manager', 'head', 'owner'}:
        raise Forbidden('Доступ к разбору закрыт.')
    call = await get_call(pool, actor, call_id)
    row = await pool.fetchrow('SELECT * FROM call_report_runs WHERE call_id=$1 AND client_id=$2 ORDER BY id DESC LIMIT 1', call_id, actor.client_id)
    if row:
        return (await access(pool, actor, row['id']))['id']
    primary = await pool.fetchrow('SELECT analysis,score,level,short_report,detailed_report FROM astra_analysis WHERE call_id=$1', call_id)
    fingerprint = sha(json.dumps(dict(primary), sort_keys=True, default=str) + (call['transcript'] or '')) if primary else ''
    run_id = await capture(pool, call_id, actor.client_id, 'view:' + fingerprint)
    await prepare(pool, run_id, wait=False)
    return run_id


async def prepare(pool, run_id, *, wait=True):
    async with pool.acquire() as conn, conn.transaction():
        raw = await conn.fetchrow('SELECT * FROM call_report_runs WHERE id=$1 FOR UPDATE', run_id)
        if raw is None:
            raise ValueError('Разбор не найден.')
        row = unpack(raw)
        if row['status'] == 'ready':
            return row
        cfg = row['configuration']
        if cfg.get('explicit_generation'):
            raise ValueError('Новый разбор ещё готовится. Попробуйте позже.')
        aux = {}; waiting = False; limits = []
        if not cfg.get('original_source_known'):
            limits.append('Для исторической оценки не сохранилась полная версия исходных настроек; используются её сохранённые результаты.')
        for key, table, version in (
            ('course', 'methodology_evaluations', cfg['course_version']),
            ('combined', 'automatic_call_reports', cfg['combined_version']),
        ):
            if not cfg.get('expect_' + key):
                continue
            # The table/column names above are a fixed allowlist, never user input.
            saved = await conn.fetchrow(f'SELECT status,result,version,created_at FROM {table} WHERE call_id=$1 AND client_id=$2 AND version=$3 AND transcript_sha256=$4',
                                       row['call_id'], row['client_id'], version, row['transcript_sha256'])
            if saved and saved['status'] == 'complete' and saved['result']:
                aux[key] = decode(saved['result'])
                aux.setdefault('sources', {})[key] = {'version': saved['version'], 'created_at': saved['created_at'].isoformat(),
                                                      'transcript_sha256': row['transcript_sha256']}
            elif saved and saved['status'] in {'failed', 'uncertain'}:
                limits.append('Часть дополнительной проверки недоступна из-за ошибки обработки.')
            else:
                waiting = True
        if waiting and wait and datetime.now(timezone.utc) - row['created_at'] < WAIT_FOR_AUXILIARY:
            raise RetryLater(timedelta(seconds=20), 'ожидаются внутренние результаты для одного итогового разбора', record_limit=False)
        if waiting:
            limits.append('Часть дополнительной проверки ещё не готова; итог построен по доступным данным этого запуска.')
        aux['limitations'] = list(dict.fromkeys(limits))
        row['auxiliary'] = aux
        text = render.summary(row)
        await conn.execute("UPDATE call_report_runs SET status='ready',summary=$2,auxiliary=$3::jsonb,ready_at=now() WHERE id=$1 AND status='pending'", run_id, text, json.dumps(aux, ensure_ascii=False))
        row.update(status='ready', summary=text)
        return row


async def overview(pool, actor, run_id):
    await access(pool, actor, run_id)
    async with pool.acquire() as conn, conn.transaction():
        row = unpack(await conn.fetchrow('SELECT * FROM call_report_runs WHERE id=$1 FOR UPDATE', run_id))
        if row['status'] != 'ready':
            raise ValueError('Итог ещё готовится. Попробуйте открыть обзор позже.')
        from . import overview_cache
        from .extended_review import VERSION, build
        from . import legacy_display
        cache = overview_cache.decode(row['expanded'])
        if legacy_display.selected(row):
            key = legacy_display.cache_key(row)
            cache['selected_version'] = key
            saved = overview_cache.entry(cache)
            if saved:
                return saved['text']
            text = render.expanded(row)
            cache['entries'][key] = {'text': text, 'methodology_version': legacy_display.METHOD,
                'display_version': legacy_display.VERSION, 'transcript_sha256': row.get('transcript_sha256'),
                'delivery': {}}
            await conn.execute('UPDATE call_report_runs SET expanded=$2 WHERE id=$1', run_id, overview_cache.encode(cache))
            return text
        saved = overview_cache.entry(cache)
        if saved:
            return saved['text']
        # The row lock protects simultaneous clicks. This is a local rendering
        # of pinned evaluations, not a new model request or historical rewrite.
        text = render.expanded(row)
        facts = build(row)
        cache['entries'][VERSION] = {'text': text, 'transcript_sha256': row.get('transcript_sha256'),
            'facts': facts['facts'], 'strengths': facts['strengths'], 'improvements': facts['improvements'],
            'sources': [{'id': t['id'], 'speaker': t['speaker'], 'start': t['start'], 'end': t['end']} for t in facts['turns']],
            'delivery': {}}
        await conn.execute('UPDATE call_report_runs SET expanded=$2 WHERE id=$1', run_id, overview_cache.encode(cache))
        return text


async def recipients(pool, row):
    if row['primary_analysis'].get('level') is None:
        return []  # Preserve the existing automatic filter for unevaluable calls.
    people = set(await report_chat_ids(pool, row['client_id']))
    if row['primary_analysis'].get('level') == '❌':
        manager = await pool.fetchval("SELECT telegram_user_id FROM employees WHERE client_id=$1 AND extension=$2 AND role='manager'", row['client_id'], row['extension'])
        if manager:
            people.add(manager)
    return sorted(people)


async def deliver(pool, bot, row, *, only_to=None):
    """At-most-once on an unknown Telegram outcome; known rejections can retry.

    Bot API has no idempotency key. A crash after send but before storing the
    receipt cannot be distinguished from a failed send. Never blindly resend.
    """
    targets = await recipients(pool, row) if only_to is None else [only_to]
    for recipient in targets:
        actor = await resolve_actor(pool, recipient)
        try:
            await access(pool, actor, row['id'])
        except Forbidden:
            continue
        await pool.execute('INSERT INTO call_report_delivery(run_id,call_id,recipient) VALUES($1,$2,$3) ON CONFLICT DO NOTHING', row['id'], row['call_id'], recipient)
        claimed = await pool.fetchval("UPDATE call_report_delivery SET status='sending',attempts=attempts+1,updated_at=now() WHERE run_id=$1 AND recipient=$2 AND status='pending' RETURNING run_id", row['id'], recipient)
        if not claimed:
            continue
        try:
            message = await bot.send_message(recipient, row['summary'], parse_mode='HTML', reply_markup=render.keyboard(row['id']))
        except TelegramForbiddenError:
            await pool.execute("UPDATE call_report_delivery SET status='forbidden',error='Telegram denied delivery',updated_at=now() WHERE run_id=$1 AND recipient=$2", row['id'], recipient)
            continue
        except (TelegramBadRequest, TelegramRetryAfter) as exc:
            await pool.execute("UPDATE call_report_delivery SET status='pending',error=$3,updated_at=now() WHERE run_id=$1 AND recipient=$2", row['id'], recipient, type(exc).__name__)
            # Other recipients must still receive their result before retrying this one.
            continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await pool.execute("UPDATE call_report_delivery SET status='uncertain',error=$3,updated_at=now() WHERE run_id=$1 AND recipient=$2", row['id'], recipient, type(exc).__name__)
            continue
        await pool.execute("UPDATE call_report_delivery SET status='sent',message_id=$3,error=NULL,updated_at=now() WHERE run_id=$1 AND recipient=$2", row['id'], recipient, message.message_id)
    if await pool.fetchval("SELECT count(*) FROM call_report_delivery WHERE run_id=$1 AND status='pending'", row['id']):
        raise RetryLater(timedelta(minutes=2), 'Telegram отклонил доставку; повтор только недоставленным получателям', record_limit=False)
    # Compatibility counters remain aggregate counters; the ledger is authoritative.
    if not row['configuration'].get('explicit_generation'):
        await pool.execute('''UPDATE astra_analysis SET
        immediate_sent_manager = immediate_sent_manager OR EXISTS(
          SELECT 1 FROM call_report_delivery d JOIN employees e ON e.telegram_user_id=d.recipient
          WHERE d.run_id=$2 AND d.status='sent' AND e.client_id=$3 AND e.role='manager'),
        immediate_sent_head = immediate_sent_head OR EXISTS(
          SELECT 1 FROM call_report_delivery d JOIN employees e ON e.telegram_user_id=d.recipient
          WHERE d.run_id=$2 AND d.status='sent' AND e.client_id=$3 AND e.role IN ('head','owner'))
        WHERE call_id=$1''', row['call_id'], row['id'], row['client_id'])
    counts = await pool.fetch('SELECT status,count(*) n FROM call_report_delivery WHERE run_id=$1 GROUP BY status', row['id'])
    return {r['status']: r['n'] for r in counts}
