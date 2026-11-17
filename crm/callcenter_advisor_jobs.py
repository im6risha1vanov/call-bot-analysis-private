"""Durable, scoped, debounced executive briefs for call-centre reports."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timedelta

from . import callcenter_advisor

log = logging.getLogger(__name__)
VERSION = 'executive-v2'
OPERATOR_VERSION = 'operator-v2'
FIELDS = ('start', 'end', 'previous_start', 'previous_end', 'filters', 'current', 'previous',
          'combinations', 'reasons', 'findings', 'coaching', 'attempts')


def _dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)


def scope_id(client_id, start, end, filters):
    scope = {'client_id': int(client_id), 'start': str(start), 'end': str(end), 'filters': filters}
    return hashlib.sha256(_dump(scope).encode()).hexdigest()


def request_data(client_id, report):
    """Use exactly the validated evidence packet as the change detector."""
    scope = {'client_id': int(client_id), 'start': report['start'], 'end': report['end'],
             'filters': report.get('filters') or {}}
    minimal = {k: report[k] for k in FIELDS if k in report}
    # Only selected-period corroborated pins reach the model, never the UI archive.
    minimal['historical_findings'] = report.get('advisor_pins') or []
    packet, refs = callcenter_advisor.evidence_packet(minimal)
    if not scope['filters'].get('operator'):
        # Preserve the aggregate cache fingerprint when only personal-review fields change.
        for field in ('operator_script_candidates', 'operator_base_candidates', 'ambiguous_attribution'):
            packet.pop(field, None)
    version = OPERATOR_VERSION if scope['filters'].get('operator') in ('olga', 'oksana') else VERSION
    fingerprint = hashlib.sha256(_dump([version, packet, refs]).encode()).hexdigest()
    key = scope_id(client_id, scope['start'], scope['end'], scope['filters'])
    return key, fingerprint, {'packet': packet, 'sources': refs}


async def operator_highlights(pool, client_id):
    """Read only the current week's cached operator conclusions; never enqueue work."""
    from . import callcenter_insights
    start, end = callcenter_insights.report_dates()
    ids = {op: scope_id(client_id, start.isoformat(), end.isoformat(),
                        {'project': '', 'base': '', 'operator': op}) for op in ('olga', 'oksana')}
    if not await pool.fetchval("SELECT to_regclass('callcenter_advisor_jobs') IS NOT NULL"):
        return {'start': start.isoformat(), 'end': end.isoformat(),
                'operators': {op: _result(None) for op in ids}}
    rows = await pool.fetch('''SELECT scope_id,fingerprint,result_fingerprint,result_at,status,
        CASE WHEN jsonb_typeof(result->'operator_review')='object' THEN
          jsonb_build_object('operator_review',jsonb_build_object(
            'script',jsonb_build_object('status',result#>>'{operator_review,script,status}',
                'observation',result#>>'{operator_review,script,observation}',
                'action',result#>>'{operator_review,script,action}'),
            'base',jsonb_build_object('status',result#>>'{operator_review,base,status}',
                'observation',result#>>'{operator_review,base,observation}',
                'action',result#>>'{operator_review,base,action}'))) ELSE NULL END AS result
        FROM callcenter_advisor_jobs WHERE client_id=$1 AND scope_id=ANY($2::text[])''',
        int(client_id), list(ids.values()))
    by_id = {row['scope_id']: row for row in rows}
    def item(key):
        value = _result(by_id.get(key))
        if isinstance(value['brief'], dict):
            review = value['brief'].get('operator_review')
            if isinstance(review, dict) and any(isinstance(review.get(name), dict) for name in ('script', 'base')):
                value['brief'] = {'operator_review': {
                    name: {field: (review.get(name) or {}).get(field) for field in ('status', 'observation', 'action')}
                    for name in ('script', 'base') if isinstance(review.get(name), dict)}}
            else:
                value['brief'] = None
        if value['brief'] is None and value['status'] == 'ready':
            value['status'] = 'waiting'
        return value
    return {'start': start.isoformat(), 'end': end.isoformat(),
            'operators': {op: item(key) for op, key in ids.items()}}


async def highlights_for_viewer(pool, who):
    from . import call_analysis
    if not call_analysis.can_manage(who):
        raise PermissionError('Раздел директора.')
    client_id = call_analysis.company_of(who)
    if client_id is None:
        raise PermissionError('Компания не определена.')
    return await operator_highlights(pool, client_id)


async def ensure(pool):
    if await pool.fetchval("SELECT to_regclass('callcenter_advisor_jobs') IS NOT NULL"):
        if not await pool.fetchval("SELECT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid='callcenter_advisor_jobs'::regclass AND attname='started_at' AND NOT attisdropped)"):
            await pool.execute('ALTER TABLE callcenter_advisor_jobs ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ')
        return
    await pool.execute('''CREATE TABLE IF NOT EXISTS callcenter_advisor_jobs (
        scope_id TEXT PRIMARY KEY, client_id INTEGER NOT NULL, fingerprint TEXT NOT NULL,
        report JSONB NOT NULL, status TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
        next_at TIMESTAMPTZ NOT NULL DEFAULT now(), lease_until TIMESTAMPTZ, token TEXT, started_at TIMESTAMPTZ,
        result JSONB, result_fingerprint TEXT, result_at TIMESTAMPTZ, error TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
    await pool.execute('''CREATE INDEX IF NOT EXISTS callcenter_advisor_jobs_queue_idx
                          ON callcenter_advisor_jobs(status,next_at)''')


def _result(row):
    if row is None:
        return {'status': 'waiting', 'brief': None, 'updated_at': None, 'message': ''}
    output = row['result']
    if isinstance(output, str): output = json.loads(output)
    stale = bool(output is not None and row['fingerprint'] != row['result_fingerprint'])
    state = 'error' if row['status'] == 'error' else ('stale' if stale else row['status'])
    return {'status': state, 'brief': output,
            'updated_at': row['result_at'].isoformat() if row['result_at'] else None,
            'message': ('Не удалось обновить вывод; повтор временно приостановлен' if state == 'error' else
                        ('Данные обновились, готовлю новый вывод' if stale else ''))}


async def request(pool, client_id, report):
    """Queue only when evidence changes; keep the last result of this exact scope."""
    await ensure(pool)
    scope, fingerprint, minimal = request_data(client_id, report)
    await pool.execute('''INSERT INTO callcenter_advisor_jobs(scope_id,client_id,fingerprint,report,next_at)
        VALUES($1,$2,$3,$4::jsonb,now()+interval '30 seconds')
        ON CONFLICT(scope_id) DO UPDATE SET fingerprint=excluded.fingerprint, report=excluded.report,
          status=CASE WHEN callcenter_advisor_jobs.status='running' THEN 'running' ELSE 'queued' END,
          attempts=0, error=NULL, updated_at=now(),
          next_at=GREATEST(now()+interval '30 seconds',coalesce(callcenter_advisor_jobs.result_at + interval '15 minutes',now()),
                           coalesce(callcenter_advisor_jobs.started_at + interval '15 minutes',now()))
        WHERE callcenter_advisor_jobs.client_id=excluded.client_id
          AND callcenter_advisor_jobs.fingerprint IS DISTINCT FROM excluded.fingerprint''',
        scope, client_id, fingerprint, _dump(minimal))
    row = await pool.fetchrow('''SELECT fingerprint,result_fingerprint,result,result_at,status
                                 FROM callcenter_advisor_jobs WHERE scope_id=$1 AND client_id=$2''', scope, client_id)
    return _result(row)


async def work_once(pool):
    """Claim one scoped job, with expiring lease and fencing against late results."""
    await ensure(pool)
    token = uuid.uuid4().hex
    row = await pool.fetchrow('''UPDATE callcenter_advisor_jobs SET status='running',token=$1,started_at=now(),
         lease_until=now()+interval '8 minutes',attempts=attempts+1,updated_at=now()
         WHERE scope_id=(SELECT scope_id FROM callcenter_advisor_jobs
           WHERE (status='queued' AND next_at<=now() OR status='running' AND lease_until<now())
             AND attempts<3 ORDER BY CASE WHEN report#>>'{packet,period,end}'=(now() AT TIME ZONE 'Europe/Moscow')::date::text
                   AND coalesce(report#>>'{packet,filters,project}','')=''
                   AND coalesce(report#>>'{packet,filters,base}','')=''
                   AND coalesce(report#>>'{packet,filters,operator}','') IN ('','olga','oksana') THEN 0 ELSE 1 END,
                 next_at,created_at LIMIT 1 FOR UPDATE SKIP LOCKED)
         RETURNING scope_id,client_id,fingerprint,report,attempts''', token)
    if row is None:
        # Expired final leases must become observable rather than remaining running forever.
        await pool.execute("UPDATE callcenter_advisor_jobs SET status='error',token=NULL,error='Истекло время обработки' WHERE status='running' AND lease_until<now() AND attempts>=3")
        return False
    payload = row['report']
    if isinstance(payload, str): payload = json.loads(payload)
    try:
        result = await asyncio.wait_for(callcenter_advisor.advise_packet(payload['packet'], payload['sources']), timeout=360)
        if not isinstance(result, dict) or result.get('status') not in ('ready', 'insufficient'):
            raise ValueError('Некорректный результат анализа')
        changed = await pool.execute('''UPDATE callcenter_advisor_jobs SET status='ready', result=$4::jsonb,
            result_fingerprint=$3,result_at=now(),token=NULL,lease_until=NULL,error=NULL,updated_at=now()
            WHERE scope_id=$1 AND token=$2 AND fingerprint=$3''',
            row['scope_id'], token, row['fingerprint'], _dump(result))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        changed = await pool.execute('''UPDATE callcenter_advisor_jobs SET
            status=CASE WHEN attempts>=3 THEN 'error' ELSE 'queued' END,
            next_at=now()+CASE WHEN attempts=1 THEN interval '5 minutes' ELSE interval '30 minutes' END,
            token=NULL,lease_until=NULL,error=$4,updated_at=now()
            WHERE scope_id=$1 AND token=$2 AND fingerprint=$3''',
            row['scope_id'], token, row['fingerprint'], type(exc).__name__)
    if changed == 'UPDATE 0':
        # New evidence arrived while the model was working; the old output is discarded.
        await pool.execute('''UPDATE callcenter_advisor_jobs SET status='queued',token=NULL,lease_until=NULL
                              WHERE scope_id=$1 AND token=$2''', row['scope_id'], token)
    return True


async def warm_default(pool, client_id):
    from . import callcenter_insights
    report = await callcenter_insights.report_for_company(pool, int(client_id))
    return await request(pool, int(client_id), report)


async def warm_all(pool, client_id):
    """Queue overall and operator summaries using one shared period snapshot."""
    from . import callcenter_insights
    client_id = int(client_id)
    start, end = callcenter_insights.report_dates()
    previous = start - timedelta(days=(end - start).days + 1)
    records = await callcenter_insights.fetch_records(pool, client_id, previous, end)
    states = {}
    for operator in ('', 'olga', 'oksana'):
        report = await callcenter_insights.report_for_company(pool, client_id, start, end,
                                                                operator=operator, _records=records)
        states[operator or 'all'] = await request(pool, client_id, report)
    return states


async def loop(pool):
    next_warm = 0.0
    while True:
        try:
            now = asyncio.get_running_loop().time()
            if now >= next_warm:
                client = os.getenv('CALLCENTER_MONITOR_CLIENT_ID', '').strip()
                if client.isdigit():
                    await warm_all(pool, int(client))
                next_warm = now + 900
            worked = await work_once(pool)
            await asyncio.sleep(2 if worked else 30)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception('синтез руководителю колл-центра')
            await asyncio.sleep(30)
