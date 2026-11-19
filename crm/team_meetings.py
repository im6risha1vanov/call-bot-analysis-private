"""Pilot meeting import and restricted CRM meeting views."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from datetime import datetime

from aiohttp import web
from tools import Forbidden
from . import team_assignments

MAX_BYTES = 390_000
SECTIONS = ('summary', 'decisions', 'open_questions', 'suggestions')


def _source_config(source):
    try:
        config = json.loads(os.getenv('CRM_MEETINGS_SOURCES_JSON', '{}'))[source]
        company = config['company_id']
        owners = config['owner_ids']
        series = config['series_id']
        if type(company) is not int or company <= 0 or not isinstance(series, str) or not series or len(series) > 100:
            raise ValueError()
        if not isinstance(owners, list) or not owners or any(type(x) is not int or x <= 0 for x in owners):
            raise ValueError()
        return company, series, owners
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise Forbidden('Источник встречи не настроен.')


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError()
        return parsed
    except (AttributeError, TypeError, ValueError):
        raise ValueError('Некорректное время версии источника.')


def validate(data):
    if not isinstance(data, dict) or data.get('schema_version') != 1:
        raise ValueError('Некорректная версия формата.')
    source, session, revision = data.get('source'), data.get('session_id'), data.get('revision')
    if not isinstance(source, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', source):
        raise ValueError('Некорректный источник.')
    if not isinstance(session, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', session):
        raise ValueError('Некорректный идентификатор встречи.')
    if not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{64}', revision):
        raise ValueError('Некорректная версия встречи.')
    updated = _timestamp(data.get('source_updated_at'))
    state, transcript, report = data.get('state'), data.get('transcript'), data.get('report')
    if not isinstance(state, dict) or transcript is not None and not isinstance(transcript, dict) or report is not None and not isinstance(report, dict):
        raise ValueError('Некорректные данные встречи.')
    allowed_state = {'id','state','created_at','recording_started_at','recording_ended_at','finished_at','title','capture_warning','capture_error'}
    if set(state) - allowed_state or state.get('state') not in {'complete','capture_failed','transcription_partial','transcription_failed','failed','interrupted'}:
        raise ValueError('Некорректное состояние встречи.')
    if any(v is not None and not isinstance(v, str) for v in state.values()):
        raise ValueError('Некорректное состояние встречи.')
    utterances = (transcript or {}).get('utterances', [])
    if not isinstance(utterances, list) or len(utterances) > 20000:
        raise ValueError('Некорректная расшифровка.')
    for u in utterances:
        if not isinstance(u, dict) or not isinstance(u.get('id'), str) or not re.fullmatch(r'u-[A-Za-z0-9_-]{1,128}', u['id']) or not isinstance(u.get('text', ''), str) or len(u.get('text', '')) > 10000:
            raise ValueError('Некорректная реплика.')
    notes = (report or {}).get('notes', {})
    if not isinstance(notes, dict):
        raise ValueError('Некорректный отчёт.')
    for section in SECTIONS:
        items = notes.get(section, [])
        if not isinstance(items, list) or len(items) > 100:
            raise ValueError('Некорректные пункты отчёта.')
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get('text'), str) or not 0 < len(item['text']) <= 2000:
                raise ValueError('Некорректный пункт отчёта.')
            ids = item.get('evidence_ids')
            if not isinstance(ids, list) or len(ids) > 20 or any(not isinstance(i, str) or i not in {u.get('id') for u in utterances} for i in ids):
                raise ValueError('Некорректные ссылки на реплики.')
    return source, session, revision, updated, state, transcript, report


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _decode(value):
    return json.loads(value) if isinstance(value, str) else value


def items(report):
    result = []
    for section in SECTIONS:
        for item in (report or {}).get('notes', {}).get(section, []):
            canonical = _json([section, item['text'], item['evidence_ids']])
            result.append({'id': int.from_bytes(hashlib.sha256(canonical.encode()).digest()[:8], 'big') & ((1 << 52) - 1), 'section': section, **item})
    return result


async def import_snapshot(conn, data):
    source, session, revision, updated, state, transcript, report = validate(data)
    company, series, owners = _source_config(source)
    # Serialize even a first import to prevent two simultaneous revisions racing.
    await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1, 0))', source + ':' + session)
    old = await conn.fetchrow('SELECT * FROM crm_team_meetings WHERE source=$1 AND source_session_id=$2 FOR UPDATE', source, session)
    if old:
        if old['company_id'] != company or old['series_id'] != series:
            raise Forbidden('Настройка источника изменилась.')
        if old['revision'] == revision:
            return {'id': old['id'], 'result': 'duplicate'}
        if updated <= old['source_updated_at']:
            raise web.HTTPConflict(reason='Устаревшая версия встречи.')
        await conn.execute('''UPDATE crm_team_meetings SET revision=$2, source_updated_at=$3,
            state=$4::jsonb, transcript=$5::jsonb, report=$6::jsonb, owner_ids=$7, imported_at=now() WHERE id=$1''',
            old['id'], revision, updated, _json(state), _json(transcript), _json(report), owners)
        return {'id': old['id'], 'result': 'updated'}
    ident = await conn.fetchval('''INSERT INTO crm_team_meetings
       (company_id,series_id,source,source_session_id,owner_ids,revision,source_updated_at,state,transcript,report)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb,$10::jsonb) RETURNING id''',
       company, series, source, session, owners, revision, updated, _json(state), _json(transcript), _json(report))
    return {'id': ident, 'result': 'created'}


async def _meeting(conn, identity, meeting_id, lock=False):
    row = await conn.fetchrow('SELECT * FROM crm_team_meetings WHERE id=$1 AND company_id=$2' + (' FOR UPDATE' if lock else ''), meeting_id, identity[0])
    if row is None or not _allowed(row, identity):
        raise Forbidden('Встреча недоступна.')
    return row


def _allowed(row, identity):
    try:
        company, series, owners = _source_config(row['source'])
        return company == identity[0] == row['company_id'] and series == row['series_id'] and identity[1] in owners and identity[1] in row['owner_ids']
    except Forbidden:
        return False


def _view(row, detail=False):
    out = {'id': row['id'], 'series_id': row['series_id'], 'source': row['source'],
           'revision': row['revision'], 'source_updated_at': row['source_updated_at'].isoformat(),
           'state': _decode(row['state']), 'protocol_version': row['protocol_version']}
    if detail:
        out.update(report=_decode(row['report']), protocol=_decode(row['protocol']), items=items(_decode(row['report'])))
    return out


def routes(viewer, body, pool_key):
    async def import_route(request):
        if any(k in request.headers for k in ('Forwarded', 'X-Forwarded-For', 'X-Forwarded-Host', 'X-Forwarded-Proto')) or request.remote not in ('127.0.0.1', '::1') or request.host not in ('127.0.0.1:8095', 'localhost:8095', '[::1]:8095'):
            raise web.HTTPForbidden()
        token = os.getenv('CRM_MEETINGS_IMPORT_TOKEN', '')
        supplied = request.headers.get('Authorization', '')
        if len(token) < 32 or not hmac.compare_digest(supplied, 'Bearer ' + token):
            raise web.HTTPUnauthorized()
        if request.content_length is not None and request.content_length > MAX_BYTES:
            raise web.HTTPRequestEntityTooLarge(max_size=MAX_BYTES, actual_size=request.content_length)
        raw = await request.read()
        if len(raw) > MAX_BYTES:
            raise web.HTTPRequestEntityTooLarge(max_size=MAX_BYTES, actual_size=len(raw))
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise ValueError('Некорректный JSON.')
        async with request.app[pool_key].acquire() as conn:
            async with conn.transaction():
                result = await import_snapshot(conn, data)
        return web.json_response(result)

    async def listing(request):
        pool = request.app[pool_key]
        ident = await team_assignments.identity(pool, await viewer(request))
        rows = await pool.fetch('SELECT * FROM crm_team_meetings WHERE company_id=$1 AND $2=ANY(owner_ids) ORDER BY source_updated_at DESC LIMIT 100', *ident)
        return web.json_response({'meetings': [_view(row) for row in rows if _allowed(row, ident)]})

    async def detail(request):
        pool = request.app[pool_key]
        ident = await team_assignments.identity(pool, await viewer(request))
        row = await _meeting(pool, ident, int(request.match_info['id']))
        return web.json_response(_view(row, True))

    async def snippets(request):
        pool = request.app[pool_key]
        ident = await team_assignments.identity(pool, await viewer(request))
        row = await _meeting(pool, ident, int(request.match_info['id']))
        try:
            ids = [x for x in request.query.get('ids', '').split(',') if x]
        except ValueError:
            raise ValueError('Некорректные номера реплик.')
        utterances = (_decode(row['transcript']) or {}).get('utterances', [])
        by_id = {u.get('id'): u for u in utterances}
        if len(ids) > 30 or any(i not in by_id for i in ids):
            raise ValueError('Некорректные номера реплик.')
        return web.json_response({'utterances': [by_id[i] for i in ids]})

    async def patch(request):
        pool = request.app[pool_key]
        ident = await team_assignments.identity(pool, await viewer(request))
        data = await body(request)
        protocol = data.get('protocol')
        if not isinstance(protocol, dict) or len(_json(protocol).encode()) > 100000 or type(data.get('version')) is not int:
            raise ValueError('Некорректный протокол.')
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await _meeting(conn, ident, int(request.match_info['id']), True)
                if row['protocol_version'] != data['version']:
                    raise web.HTTPConflict(reason='Протокол уже изменён.')
                version = await conn.fetchval('UPDATE crm_team_meetings SET protocol=$2::jsonb, protocol_version=protocol_version+1 WHERE id=$1 RETURNING protocol_version', row['id'], _json(protocol))
        return web.json_response({'version': version})

    async def convert(request):
        pool = request.app[pool_key]
        ident = await team_assignments.identity(pool, await viewer(request))
        data = await body(request)
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await _meeting(conn, ident, int(request.match_info['id']), True)
                item_id = data.get('item_id')
                if type(item_id) is not int or not 0 <= item_id < (1 << 52):
                    raise ValueError('Некорректный пункт встречи.')
                request_data = {k: data.get(k) for k in ('item_id', 'title', 'description', 'assignee_id', 'due_at')}
                fingerprint = hashlib.sha256(_json(request_data).encode()).hexdigest()
                existing = await conn.fetchrow('SELECT assignment_id, request_fingerprint FROM crm_team_meeting_conversions WHERE meeting_id=$1 AND item_id=$2', row['id'], item_id)
                if existing:
                    if existing['request_fingerprint'] != fingerprint:
                        raise web.HTTPConflict(reason='Этот пункт уже преобразован с другими параметрами.')
                    return web.json_response({'assignment_id': existing['assignment_id'], 'duplicate': True})
                selected = next((i for i in items(_decode(row['report'])) if i['id'] == item_id and i['section'] == 'decisions'), None)
                if not selected:
                    raise ValueError('Пункт встречи не найден.')
                if not isinstance(data.get('description', ''), str):
                    raise ValueError('Некорректное описание.')
                utterances = (_decode(row['transcript']) or {}).get('utterances', [])
                evidence = {u['id']: u for u in utterances if u['id'] in selected['evidence_ids']}
                refs = '; '.join(f"{i} ({evidence[i].get('start', '?')}–{evidence[i].get('end', '?')} сек.)" for i in selected['evidence_ids'])
                normalized = {'title': data.get('title') or selected['text'], 'description': (data.get('description') or '') + '\nИсточник: встреча ' + str(row['id']) + ', версия ' + row['revision'] + ', реплики: ' + refs, 'assignee_id': data.get('assignee_id'), 'due_at': data.get('due_at')}
                assignment = await team_assignments.create(conn, ident, normalized, source_meeting_id=row['id'], source_item_id=selected['id'], idempotency_key='meeting:' + str(row['id']) + ':' + str(selected['id']))
                await conn.execute('INSERT INTO crm_team_meeting_conversions(meeting_id,item_id,assignment_id,request_fingerprint) VALUES($1,$2,$3,$4)', row['id'], selected['id'], assignment['id'], fingerprint)
        return web.json_response({'assignment': assignment, 'evidence_ids': selected['evidence_ids']})

    async def agenda(request):
        pool = request.app[pool_key]
        ident = await team_assignments.identity(pool, await viewer(request))
        series = request.query.get('series_id', '')
        if not series or len(series) > 100:
            raise ValueError('Укажите серию встреч.')
        meetings = await pool.fetch('SELECT * FROM crm_team_meetings WHERE company_id=$1 AND series_id=$2 AND $3=ANY(owner_ids) ORDER BY source_updated_at DESC LIMIT 100', ident[0], series, ident[1])
        meetings = [r for r in meetings if _allowed(r, ident)]
        meeting_ids = [r['id'] for r in meetings]
        assignments = await pool.fetch("""SELECT id,title,status,assignee_id,due_at,blocker,result_text,result_links,source_meeting_id
            FROM crm_team_assignments WHERE company_id=$1 AND source_meeting_id=ANY($2::bigint[])
            AND status <> 'cancelled' AND (creator_id=$3 OR assignee_id=$3) ORDER BY due_at NULLS LAST""", ident[0], meeting_ids, ident[1])
        tasks = []
        for row in assignments:
            task = dict(row)
            task['result_links'] = _decode(task['result_links'])
            task['overdue'] = bool(task['due_at'] and task['due_at'] < datetime.now(task['due_at'].tzinfo) and task['status'] != 'accepted')
            task['due_at'] = task['due_at'].isoformat() if task['due_at'] else None
            tasks.append(task)
        questions = [{'meeting_id': r['id'], **i} for r in meetings for i in items(_decode(r['report'])) if i['section'] == 'open_questions' and i['id'] not in (_decode(r['protocol']) or {}).get('resolved_question_ids', [])]
        return web.json_response({'series_id': series, 'assignments': [t for t in tasks if t['status'] != 'accepted'], 'accepted': [t for t in tasks if t['status'] == 'accepted'], 'open_questions': questions})

    return [web.post('/api/internal/team/meetings/import', import_route), web.get('/api/team/meetings/agenda', agenda), web.get('/api/team/meetings', listing),
            web.get('/api/team/meetings/{id:\\d+}', detail), web.get('/api/team/meetings/{id:\\d+}/snippets', snippets),
            web.patch('/api/team/meetings/{id:\\d+}/protocol', patch), web.post('/api/team/meetings/{id:\\d+}/convert', convert)]
