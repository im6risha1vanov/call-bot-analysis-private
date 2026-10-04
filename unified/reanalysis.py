"""One explicit fresh call review, with durable paid receipts and no historical rewrite."""
from __future__ import annotations

import json
import types
from datetime import datetime
from zoneinfo import ZoneInfo

import analysis
from automatic.jobs import over_budget
from combined import evaluation
from comparison import transport
from comparison.engine import legacy_level, parse, validate_legacy
from comparison.store import decode
from methodology import runtime
from tools import Forbidden, get_call, resolve_actor
from . import render, store


async def create(pool, actor, call_id, request_key):
    if actor is None or actor.role not in {'owner', 'head'}:
        raise Forbidden('Новый разбор доступен руководителю.')
    call = await get_call(pool, actor, call_id)
    if not call['transcript'].strip():
        raise ValueError('Сохранённой транскрипции нет.')
    if not isinstance(request_key, str) or not request_key or len(request_key) > 100:
        raise ValueError('Invalid explicit request key')
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',
                           f'review-create:{actor.client_id}:{call_id}:{request_key}')
        source = 'explicit:' + request_key
        previous = await conn.fetchval('SELECT id FROM call_report_runs WHERE client_id=$1 AND call_id=$2 AND source_key=$3',
                                       actor.client_id, call_id, source)
        if previous:
            await store.access(conn, actor, previous)
            return previous
        run_id = await store.capture(conn, call_id, actor.client_id, source, fresh=True)
        row = store.unpack(await conn.fetchrow('SELECT * FROM call_report_runs WHERE id=$1 FOR UPDATE', run_id))
        cfg = row['configuration']
        cfg.update(explicit_generation=True, initiator=actor.telegram_user_id,
                   generation_config=runtime.settings(), generation_catalog=runtime.approved_catalog(),
                   combined_package_sha256=evaluation.verify()['sha256'], expect_course=False)
        await conn.execute('UPDATE call_report_runs SET configuration=$2::jsonb WHERE id=$1', run_id, json.dumps(cfg))
        params = transport.settings()
        for stage, prompt, tokens in (('score', analysis.SCORE_PROMPT, 6000), ('combined', evaluation.prompt(), 8000)):
            await conn.execute('INSERT INTO call_report_generation(run_id,stage,prompt,settings) VALUES($1,$2,$3,$4::jsonb)',
                               run_id, stage, prompt, json.dumps({**params, 'max_output_tokens': tokens}))
        return run_id


async def run(pool, actor, run_id):
    async with pool.acquire() as conn:
        key = f'review-generate:{run_id}'
        if not await conn.fetchval('SELECT pg_try_advisory_lock(hashtextextended($1,0))', key):
            return {'status': 'busy', 'run_id': run_id}
        try:
            # Resolve again rather than trust stale caller rights before billing.
            current = await resolve_actor(conn, actor.telegram_user_id)
            row = await store.access(conn, current, run_id)
            cfg = row['configuration']
            if not cfg.get('explicit_generation') or cfg.get('initiator') != current.telegram_user_id or current.role not in {'head', 'owner'}:
                raise Forbidden('Это другой запрос разбора.')
            if row['status'] == 'ready':
                return {'status': 'ready', 'run_id': run_id, 'cached': True}
            if evaluation.verify()['sha256'] != cfg['combined_package_sha256']:
                raise ValueError('Pinned combined package changed')
            client = await conn.fetchrow('SELECT * FROM clients WHERE id=$1', current.client_id)
            for stage in ('score', 'combined'):
                item = await conn.fetchrow('SELECT * FROM call_report_generation WHERE run_id=$1 AND stage=$2', run_id, stage)
                if item['status'] == 'complete':
                    continue
                if item['response_text'] is None and item['status'] != 'pending':
                    if item['status'] == 'processing':
                        await conn.execute("UPDATE call_report_generation SET status='uncertain',error='interrupted paid request',updated_at=now() WHERE run_id=$1 AND stage=$2", run_id, stage)
                    return {'status': 'needs_review', 'run_id': run_id, 'stage': stage}
                response = item['response_text']
                if response is None:
                    if not client['processing_enabled'] or await over_budget(conn, client):
                        return {'status': 'blocked', 'run_id': run_id, 'stage': stage}
                    await conn.execute("UPDATE call_report_generation SET status='processing',updated_at=now() WHERE run_id=$1 AND stage=$2", run_id, stage)
                    user = ('КОНТЕКСТ (не заменяет цитаты):\n' + json.dumps(row['metadata'], ensure_ascii=False)
                            + '\n\nТРАНСКРИПТ:\n' + row['transcript'])
                    try:
                        response, cost, usage = await transport.request(item['prompt'], user, decode(item['settings']))
                    except transport.RequestFailure as exc:
                        state = 'failed' if exc.status in {400, 401, 402, 403, 404, 429} else 'uncertain'
                        await conn.execute('UPDATE call_report_generation SET status=$3,error=$4,updated_at=now() WHERE run_id=$1 AND stage=$2', run_id, stage, state, str(exc))
                        return {'status': state, 'run_id': run_id, 'stage': stage}
                    # Save and account every received response before validation.
                    async with conn.transaction():
                        await conn.execute("UPDATE call_report_generation SET status='received',response_text=$3,cost_units=$4,usage=$5::jsonb,updated_at=now() WHERE run_id=$1 AND stage=$2", run_id, stage, response, cost, json.dumps(usage))
                        table = 'astra_daily_spend' if stage == 'score' else 'automatic_call_spend'
                        await conn.execute(f'INSERT INTO {table}(client_id,day,spent_units) VALUES($1,$2,$3) ON CONFLICT(client_id,day) DO UPDATE SET spent_units={table}.spent_units+EXCLUDED.spent_units',
                                           current.client_id, datetime.now(ZoneInfo(client['timezone'])).date(), cost)
                try:
                    raw = parse(response)
                    if stage == 'score':
                        validate_legacy(raw, 'score')
                        namespace = dict(analysis.compute_score.__globals__)
                        namespace['CRITERIA'] = cfg['criteria']
                        score, rows = types.FunctionType(analysis.compute_score.__code__, namespace)(raw)
                        result = {'analysis': {**raw, 'rows': rows}, 'score': score,
                                  'level': legacy_level(analysis, raw, rows, decode(item['settings'])),
                                  'short_report': {}, 'detailed_report': None}
                    else:
                        result = evaluation.evaluate(raw, row['transcript'], config=cfg['generation_config'], catalog=cfg['generation_catalog'])
                except (ValueError, TypeError, KeyError, AttributeError) as exc:
                    await conn.execute("UPDATE call_report_generation SET status='failed',error=$3,updated_at=now() WHERE run_id=$1 AND stage=$2", run_id, stage, type(exc).__name__)
                    return {'status': 'failed', 'run_id': run_id, 'stage': stage, 'reason': type(exc).__name__}
                await conn.execute("UPDATE call_report_generation SET status='complete',result=$3::jsonb,error=NULL,updated_at=now() WHERE run_id=$1 AND stage=$2", run_id, stage, json.dumps(result, ensure_ascii=False))
            primary = decode(await conn.fetchval("SELECT result FROM call_report_generation WHERE run_id=$1 AND stage='score'", run_id))
            combined = decode(await conn.fetchval("SELECT result FROM call_report_generation WHERE run_id=$1 AND stage='combined'", run_id))
            auxiliary = {'combined': combined, 'limitations': [], 'sources': {'combined': {
                'version': evaluation.VERSION, 'transcript_sha256': row['transcript_sha256'], 'origin': 'explicit_fresh_analysis'}}}
            row.update(primary_analysis=primary, auxiliary=auxiliary)
            summary = render.summary(row)
            await conn.execute("UPDATE call_report_runs SET primary_analysis=$2::jsonb,auxiliary=$3::jsonb,summary=$4,status='ready',ready_at=now() WHERE id=$1 AND status='pending'", run_id,
                               json.dumps(primary, ensure_ascii=False), json.dumps(auxiliary, ensure_ascii=False), summary)
            return {'status': 'ready', 'run_id': run_id, 'cached': False}
        finally:
            await conn.execute('SELECT pg_advisory_unlock(hashtextextended($1,0))', key)
