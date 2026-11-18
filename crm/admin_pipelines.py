"""Versioned pipeline configuration and manual transitions for CRM deals."""
import re
from collections import Counter
from datetime import datetime, time, timezone

from aiohttp import web

from tools import Actor, Forbidden
from . import admin_store as admin, deals, store


KEY = re.compile(r'[a-z][a-z0-9_]{0,39}\Z')
DEFAULT = 'default'


def default_config():
    return {'name': 'Продажи', 'stages': [{'key': k, 'name': name, 'stall_days': deals.STALL_DAYS.get(k)}
                                          for k, name in deals.STAGES],
            'reasons': dict(deals.LOST_REASONS), 'transitions': {}}


def check_key(key):
    if not isinstance(key, str) or not KEY.fullmatch(key):
        raise ValueError('Код должен начинаться с буквы и содержать до 40 латинских букв, цифр и подчёркиваний.')
    return key


def label(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 100:
        raise ValueError('Название должно содержать от 1 до 100 символов.')
    return value.strip()


def clean_config(pipeline, value):
    if not isinstance(value, dict):
        raise ValueError('Передайте описание воронки.')
    stages = value.get('stages')
    if not isinstance(stages, list) or not 2 <= len(stages) <= 30:
        raise ValueError('Нужно от 2 до 30 этапов.')
    result = []
    for stage in stages:
        if not isinstance(stage, dict):
            raise ValueError('Неверное описание этапа.')
        limit = stage.get('stall_days')
        if limit is not None and (type(limit) is not int or not 1 <= limit <= 365):
            raise ValueError('Срок без движения должен быть от 1 до 365 дней.')
        result.append({'key': check_key(stage.get('key')), 'name': label(stage.get('name')), 'stall_days': limit})
    keys = [stage['key'] for stage in result]
    if len(set(keys)) != len(keys) or keys[0] != 'base' or keys[-1] != 'paid':
        raise ValueError('Коды этапов должны быть уникальны. Первый этап — base, последний — paid.')
    if pipeline == DEFAULT and set(dict(deals.STAGES)) - set(keys):
        raise ValueError('Сохраните этапы, на которые ссылаются существующие события CRM.')
    reasons = value.get('reasons', deals.LOST_REASONS)
    if not isinstance(reasons, dict) or not 1 <= len(reasons) <= 40:
        raise ValueError('Нужно от 1 до 40 причин отказа.')
    reasons = {check_key(k): label(v) for k, v in reasons.items()}
    transitions = value.get('transitions', {})
    if not isinstance(transitions, dict) or set(transitions) - set(keys):
        raise ValueError('Переход должен начинаться с существующего этапа.')
    for starts, ends in transitions.items():
        if not isinstance(ends, list) or any(not isinstance(end, str) or end not in keys or end == starts
                                             for end in ends):
            raise ValueError('Выберите существующие этапы для перехода.')
        transitions[starts] = list(dict.fromkeys(ends))
    return {'name': label(value.get('name')), 'stages': result, 'reasons': reasons, 'transitions': transitions}


def present(row):
    return {'pipeline': row['pipeline'], 'version': row['version'], 'config': admin.decoded(row['config']),
            'published_at': row['published_at'].isoformat() if row['published_at'] else None}


def implicit():
    return {'pipeline': DEFAULT, 'version': 0, 'config': default_config(), 'published_at': None}


async def versions(pool, client_id, pipeline):
    rows = await pool.fetch(
        '''SELECT * FROM crm_pipeline_versions WHERE client_id=$1 AND pipeline=$2
           ORDER BY version DESC''', client_id, pipeline)
    return [present(row) for row in rows] + ([implicit()] if pipeline == DEFAULT and all(row['version'] for row in rows) else [])


async def listing(pool, client_id):
    keys = await pool.fetch(
        'SELECT DISTINCT pipeline FROM crm_pipeline_versions WHERE client_id=$1 ORDER BY pipeline', client_id)
    items = []
    for pipeline in sorted({DEFAULT, *(r['pipeline'] for r in keys)}):
        history = await versions(pool, client_id, pipeline)
        items.append({'pipeline': pipeline,
                      'draft': next((v for v in history if not v['published_at'] and v['version']), None),
                      'active': next((v for v in history if v['published_at'] or v['version'] == 0), None)})
    return items


async def save(conn, who, client_id, pipeline, data):
    check_key(pipeline)
    await admin.client(conn, client_id, lock=True)
    history = await versions(conn, client_id, pipeline)
    last = history[0] if history else None
    admin.check_version(data, last['version'] if last else 0)
    if 'restore_version' in data:
        source = next((v for v in history if v['version'] == data['restore_version']
                       and (v['published_at'] or v['version'] == 0)), None)
        if source is None:
            raise web.HTTPNotFound(reason='Опубликованная версия не найдена.')
        config = source['config']
    else:
        config = clean_config(pipeline, data.get('config'))
    if last and last['version'] and not last['published_at']:
        await conn.execute(
            '''DELETE FROM crm_pipeline_versions WHERE client_id=$1 AND pipeline=$2 AND published_at IS NULL''',
            client_id, pipeline)
    created = await conn.fetchrow(
        '''INSERT INTO crm_pipeline_versions(client_id,pipeline,version,config) VALUES($1,$2,$3,$4)
           RETURNING *''', client_id, pipeline, (last['version'] if last else 0) + 1, admin.as_json(config))
    result = present(created)
    await admin.audit(conn, who, client_id, 'pipelines', pipeline, 'draft', last, result)
    return result


async def preview(pool, client_id, pipeline, version):
    history = await versions(pool, client_id, pipeline)
    draft = next((v for v in history if v['version'] == version and not v['published_at'] and version), None)
    if draft is None:
        raise web.HTTPNotFound(reason='Черновик воронки не найден.')
    stage_keys = {stage['key'] for stage in draft['config']['stages']}
    tz = await store.zone(pool, client_id)
    now = datetime.now(timezone.utc)
    current = await deals.all_deals(pool, Actor(0, client_id, 'owner', None), tz, now)
    affected = Counter((d['stage'], d['status']) for d in current
                       if d.get('pipeline', DEFAULT) == pipeline and d['stage'] not in stage_keys)
    return {'draft': draft, 'affected': [{'stage': stage, 'status': status, 'count': count}
                                        for (stage, status), count in sorted(affected.items())]}


async def report(pool, client_id, pipeline, as_of):
    now = datetime.now(timezone.utc)
    if as_of:
        now = datetime.fromisoformat(as_of)
        if now.tzinfo is None:
            raise ValueError('Укажите дату и время с часовым поясом.')
    active = await deals.pipeline_configuration(pool, client_id, pipeline, now)
    if active is None:
        raise web.HTTPNotFound(reason='На эту дату воронка ещё не опубликована.')
    actor = Actor(0, client_id, 'owner', None)
    tz = await store.zone(pool, client_id)
    raw = await deals.load(pool, client_id, actor=actor)
    if as_of:
        for name, field in (('calls', 'call_started_at'), ('events', 'created_at'), ('base', 'created_at'),
                            ('outcomes', 'created_at'), ('legacy', 'updated_at'), ('chats', 'last_message_at')):
            raw[name] = [r for r in raw[name] if r[field] and r[field] <= now]
        raw['shows'] = [r for r in raw['shows'] if
                        (r['call_started_at'] or (datetime.combine(r['day'], time.min, tzinfo=tz) if r['day'] else now))
                        <= now]
        raw['tasks'] = []
    current = await deals.all_deals(pool, actor, tz, now, raw, now)
    selected = [d for d in current if d.get('pipeline', DEFAULT) == pipeline]
    stages = [(s['key'], s['name']) for s in active['config']['stages']]
    base = len({deals.key(r['number']) for r in raw['base']} - {None}) if pipeline == DEFAULT else len(selected)
    return {'pipeline': pipeline, 'version': active['version'], 'as_of': now.isoformat(),
            'funnel': deals.funnel(selected, base, stages)}


async def publish(conn, who, client_id, pipeline, data):
    await admin.client(conn, client_id, lock=True)
    history = await versions(conn, client_id, pipeline)
    if not history or not history[0]['version'] or history[0]['published_at']:
        raise web.HTTPConflict(reason='Создайте черновик воронки.')
    draft = history[0]
    admin.check_version(data, draft['version'])
    before = next((v for v in history if v['published_at'] or v['version'] == 0), None)
    impact = await preview(conn, client_id, pipeline, draft['version'])
    mapping = data.get('remap', {})
    affected = {item['stage'] for item in impact['affected']}
    choices = {stage['key'] for stage in draft['config']['stages']} - {'paid'}
    if (not isinstance(mapping, dict)
            or any(not isinstance(stage, str) or stage not in choices for stage in mapping.values())
            or mapping.keys() != affected):
        raise ValueError('Выберите этап для каждой группы сделок на удаляемых этапах.')
    row = await conn.fetchrow(
        '''UPDATE crm_pipeline_versions SET published_at=clock_timestamp()
           WHERE client_id=$1 AND pipeline=$2 AND version=$3 RETURNING *''', client_id, pipeline, draft['version'])
    if impact['affected']:
        await conn.execute(
            '''INSERT INTO crm_deal_stage_changes(client_id,phone,pipeline,pipeline_version,stage)
               SELECT client_id, phone, pipeline, $3, $4::jsonb->>stage FROM (
                   SELECT DISTINCT ON (phone) * FROM crm_deal_stage_changes
                   WHERE client_id=$1 ORDER BY phone,id DESC
               ) current WHERE pipeline=$2 AND $4::jsonb ? stage''',
            client_id, pipeline, draft['version'], admin.as_json(mapping))
    after = present(row)
    await admin.audit(conn, who, client_id, 'pipelines', pipeline, 'publish', before, after)
    return after


async def move(conn, actor, phone, data):
    client_id = actor.client_id
    await admin.client(conn, client_id, lock=True)
    current = await deals.one(conn, actor, phone)
    if current['status'] != 'active':
        raise web.HTTPConflict(reason='Переход доступен для открытой сделки.')
    if data.get('from') != current['stage'] or data.get('from_pipeline', DEFAULT) != current.get('pipeline', DEFAULT):
        raise web.HTTPConflict(reason='Сделка уже перешла на другой этап.')
    pipeline = check_key(data.get('pipeline') or current.get('pipeline', DEFAULT))
    active = await deals.pipeline_configuration(conn, client_id, pipeline)
    if active is None:
        raise web.HTTPNotFound(reason='Воронка ещё не опубликована.')
    admin.check_version(data, active['version'])
    target = check_key(data.get('stage'))
    config = active['config']
    if target == 'paid':
        raise Forbidden('Оплату отметьте в карточке сделки.')
    if target not in {s['key'] for s in config['stages']}:
        raise ValueError('Этап не найден.')
    source = current['stage']
    if pipeline != current.get('pipeline', DEFAULT):
        if actor.role not in {'owner', 'head'}:
            raise Forbidden('Воронку сделки выбирает руководитель.')
    elif source == target or target not in config['transitions'].get(source, [target]):
        raise ValueError('Этот переход не разрешён.')
    if active['version'] == 0:
        raise web.HTTPConflict(reason='Сначала опубликуйте настройки воронки.')
    change = await conn.fetchval(
        '''INSERT INTO crm_deal_stage_changes(client_id,phone,pipeline,pipeline_version,stage)
           VALUES($1,$2,$3,$4,$5) RETURNING id''', client_id, current['phone'], pipeline, active['version'], target)
    payload = {'deal': {'phone': current['phone'], 'client': current['company'], 'manager': current['manager']},
               'transition': {'pipeline': pipeline, 'from': source, 'to': target, 'version': active['version']}}
    await conn.execute(
        '''INSERT INTO automation_events(client_id,kind,ref_id,payload)
           SELECT $1,'deal.stage_changed',$2,$3 WHERE EXISTS (
               SELECT 1 FROM automations WHERE client_id=$1 AND enabled AND spec->'trigger'->>'type'='deal.stage_changed'
           )''', client_id, change, admin.as_json(payload))
    return await deals.one(conn, actor, phone)
