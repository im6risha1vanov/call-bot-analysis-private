"""Сайт CRM менеджера: python -m crm.web (сервис callbot-crm).

Слушает только 127.0.0.1, наружу его отдаёт Caddy с HTTPS (scripts/crm_setup.sh).
Страница и API в одном процессе; данные в той же базе, что у бота.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import date, time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')

import asyncpg  # noqa: E402
from aiohttp import web  # noqa: E402

import mango_client  # noqa: E402
from tools import Forbidden, is_privileged  # noqa: E402
from . import ask as asking, auth, cabinet, contracts, deals, journal, overview, people, recording, reviews, sources, staff, store, training  # noqa: E402
from . import prompts as prompt_mode, robot, robot_api, robot_runner, robot_store  # noqa: E402
from review import store as review_store  # noqa: E402
from .dial import dial  # noqa: E402

log = logging.getLogger('crm')
STATIC = Path(__file__).resolve().parent / 'static'
COOKIE = 'crm_session'
TEST_COOKIE = 'crm_test'
POOL = web.AppKey('pool', asyncpg.Pool)
SECURE_COOKIE = os.getenv('CRM_COOKIE_SECURE', '1') != '0'
HEADERS = {
    'Content-Security-Policy': "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                               "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    'X-Frame-Options': 'DENY',
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer',
}
TEST_READONLY = 'Тестовый вход только для просмотра: звонки и отметки работают во входе самого менеджера.'
VIEW_ONLY = 'Это вход только для просмотра. Менять, звонить и спрашивать можно по личной ссылке.'
# Запросы, которые ничего не меняют и разрешены входу для просмотра; остальные POST ему закрыты.
VIEW_ONLY_POSTS = {'/api/logout', '/api/test/login'}


def fail(status: int, text: str) -> web.Response:
    return web.json_response({'error': text}, status=status)


@web.middleware
async def guard(request: web.Request, handler):
    # Запросы с другого сайта не могут поставить свой заголовок без CORS, а CORS мы не разрешаем.
    if request.method not in ('GET', 'HEAD') and request.headers.get('X-CRM') != '1':
        return fail(403, 'Запрос отклонён.')
    if request.method not in ('GET', 'HEAD') and request.path not in VIEW_ONLY_POSTS:
        # Тестовая ссылка и открытый сайт: смотреть можно всё, менять ничего (а не по списку разделов).
        who = await auth.viewer_for(request.app[POOL], request.cookies.get(COOKIE))
        if who is not None and who.test:
            response = fail(403, VIEW_ONLY)
            response.headers.update(HEADERS)
            return response
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        if exc.status < 400 or not request.path.startswith('/api/'):
            raise
        response = fail(exc.status, exc.reason)
    except Forbidden as exc:
        response = fail(403, str(exc))
    except ValueError as exc:
        response = fail(400, str(exc))
    except Exception:
        log.exception('ошибка CRM: %s %s', request.method, request.path)
        response = fail(500, 'Что-то пошло не так. Попробуйте ещё раз.')
    response.headers.update(HEADERS)
    if request.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


async def viewer(request: web.Request) -> auth.Viewer:
    who = await auth.viewer_for(request.app[POOL], request.cookies.get(COOKIE))
    if who is None:
        raise web.HTTPUnauthorized(reason='Откройте CRM по своей ссылке.')
    return who


async def actor(request: web.Request, write: bool = False):
    """Сотрудник отдела: директор или менеджер. write=True закрыт для тестового входа."""
    who = await viewer(request)
    if who.actor is None:
        raise Forbidden('Этот раздел недоступен.')
    if write and who.test:
        raise Forbidden(TEST_READONLY)
    return who.actor


async def director(request: web.Request) -> auth.Viewer:
    who = await viewer(request)
    if who.role != 'director':
        raise Forbidden('Раздел директора.')
    return who


async def body(request: web.Request) -> dict:
    if request.content_length and request.content_length > 400_000:
        raise ValueError('Слишком много данных за раз.')
    try:
        data = await request.json()
    except json.JSONDecodeError:
        raise ValueError('Неверный запрос.')
    if not isinstance(data, dict):
        raise ValueError('Неверный запрос.')
    return data


def parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise ValueError('Неверная дата.')


def parse_time(value) -> time | None:
    if not value:
        return None
    try:
        return time.fromisoformat(str(value)[:5])
    except ValueError:
        raise ValueError('Неверное время.')


def int_id(value) -> int:
    text = str(value or '')
    if not text.isdigit() or len(text) > 18:
        raise ValueError('Неверный номер записи.')
    return int(text)


async def ext_of(request: web.Request, who, value=None) -> str:
    return await store.scope(request.app[POOL], who, value if value is not None else request.query.get('ext'))


# ---- страницы ----

async def index(request):
    who = await auth.viewer_for(request.app[POOL], request.cookies.get(COOKIE))
    if who is None and auth.public_on():
        raise web.HTTPFound('/t/' + auth.PUBLIC_KEY)  # открытый сайт: сразу выбор входа
    return web.FileResponse(STATIC / 'index.html')


async def test_page(request):
    if not auth.test_key_ok(request.match_info['key']):
        raise web.HTTPNotFound()
    response = web.FileResponse(STATIC / 'test.html')
    response.set_cookie(TEST_COOKIE, request.match_info['key'], max_age=int(auth.TEST_TTL.total_seconds()),
                        httponly=True, secure=SECURE_COOKIE, samesite='Lax', path='/')
    return response


async def personal_page(request):
    got = await auth.invite_login(request.app[POOL], request.match_info['token'])
    if got is None:
        return web.Response(status=404, content_type='text/html', text=(
            '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Averon CRM</title><body style="font:16px system-ui;margin:40px auto;max-width:420px;padding:0 16px">'
            '<h1>Ссылка не действует</h1><p>Её отозвали или в ней ошибка. Попросите у руководителя новую.</p></body>'))
    session, role = got
    await auth.logout(request.app[POOL], request.cookies.get(COOKIE))
    response = web.HTTPFound('/')
    response.set_cookie(COOKIE, session, max_age=int(auth.PERSONAL_TTL.total_seconds()), httponly=True,
                        secure=SECURE_COOKIE, samesite='Lax', path='/')
    raise response


async def test_exit(request):
    await auth.logout(request.app[POOL], request.cookies.get(COOKIE))
    key = request.cookies.get(TEST_COOKIE)
    response = web.HTTPFound(f'/t/{key}' if auth.test_key_ok(key) else '/')
    response.del_cookie(COOKIE, path='/')
    raise response


async def logout_page(request):
    await auth.logout(request.app[POOL], request.cookies.get(COOKIE))
    response = web.HTTPFound('/')
    response.del_cookie(COOKIE, path='/')
    raise response


async def old_director(request):
    # Отдельной страницы директора больше нет: всё в одной CRM. Старые закладки ведут на главную,
    # а #раздел из ссылки браузер переносит сам, CRM откроет нужный раздел по старому имени.
    raise web.HTTPFound('/')


# ---- API ----

async def api_logout(request):
    await auth.logout(request.app[POOL], request.cookies.get(COOKIE))
    response = web.json_response({'ok': True})
    response.del_cookie(COOKIE, path='/')
    return response


async def api_me(request):
    who = await viewer(request)
    pool = request.app[POOL]
    if who.role == 'client':
        return web.json_response({'name': who.client_key, 'role': 'client', 'test': who.test, 'client': who.client_key,
                                  'privileged': False, 'can_dial': False, 'team': [],
                                  'robot': who.client_key in await robot_store.enabled_clients(pool)})
    a = who.actor
    name = await pool.fetchval('SELECT full_name FROM employees WHERE client_id=$1 AND telegram_user_id=$2 LIMIT 1',
                               a.client_id, a.telegram_user_id) if a.telegram_user_id else None
    if name is None and a.extension:
        name = await pool.fetchval('SELECT full_name FROM employees WHERE client_id=$1 AND extension=$2 LIMIT 1',
                                   a.client_id, a.extension)
    privileged = is_privileged(a)
    can_dial = str(a.extension or '').isdigit() and not who.test
    return web.json_response({
        'name': name or ('Директор' if who.role == 'director' else ''), 'role': who.role, 'test': who.test,
        'ext': a.extension if str(a.extension or '').isdigit() else None,
        'can_dial': can_dial, 'privileged': privileged, 'readonly': who.test,
        'team': await store.team(pool, a.client_id) if privileged else [],
        'outcomes': store.OUTCOMES, 'needs_date': sorted(store.NEEDS_DATE), 'lead_status': store.LEAD_STATUS,
    })


async def api_test_options(request):
    if not auth.test_key_ok(request.query.get('key')):
        raise web.HTTPNotFound()
    pool = request.app[POOL]
    client_id = await pool.fetchval('SELECT id FROM clients WHERE processing_enabled ORDER BY id LIMIT 1')
    try:
        clients = [c['name'] for c in (await cabinet.summary())['clients']]
    except Exception:
        log.exception('список клиентов колл-центра не загрузился')
        clients = []
    return web.json_response({'managers': await store.team(pool, client_id) if client_id else [], 'clients': clients})


async def api_test_login(request):
    data = await body(request)
    if not auth.test_key_ok(data.get('key')):
        raise web.HTTPNotFound()
    role = str(data.get('role') or '')
    client_key = None
    if role == 'client':
        names = [c['name'] for c in (await cabinet.summary())['clients']]
        client_key = str(data.get('client') or '')
        if client_key not in names:
            raise ValueError('Такого клиента нет.')
    session = await auth.test_login(request.app[POOL], role, data.get('ext'), client_key)
    response = web.json_response({'ok': True, 'go': '/'})
    response.set_cookie(COOKIE, session, max_age=int(auth.TEST_TTL.total_seconds()), httponly=True,
                        secure=SECURE_COOKIE, samesite='Lax', path='/')
    return response


def doc_path(value) -> str:
    path = str(value or '')
    parts = path.split('/')
    if not path or len(path) > 300 or any(not p or p in ('.', '..') or not all(ch.isalnum() or ch in '_-.~:@+' for ch in p)
                                          for p in parts):
        raise ValueError('Неверный путь.')
    return path


async def api_docs(request):
    await director(request)
    pool = request.app[POOL]
    if 'path' in request.query:
        row = await pool.fetchval('SELECT data FROM crm_docs WHERE path=$1', doc_path(request.query['path']))
        return web.json_response({'data': json.loads(row) if row else None})
    prefix = doc_path(request.query.get('collection')) + '/'
    rows = await pool.fetch("SELECT path, data FROM crm_docs WHERE path LIKE $1 || '%' AND position('/' in substr(path, $2)) = 0",
                            prefix.replace('%', r'\%').replace('_', r'\_'), len(prefix) + 1)
    return web.json_response({'docs': [{'path': r['path'], 'data': json.loads(r['data'])} for r in rows]})


def merge(old: dict, new: dict) -> dict:
    out = dict(old)
    for k, v in new.items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


async def api_docs_write(request):
    await director(request)
    data = await body(request)
    path, op, value = doc_path(data.get('path')), data.get('op'), data.get('data')
    pool = request.app[POOL]
    if op == 'delete':
        await pool.execute('DELETE FROM crm_docs WHERE path=$1', path)
    elif op in ('set', 'update') and isinstance(value, dict):
        async with pool.acquire() as conn, conn.transaction():
            old = await conn.fetchval('SELECT data FROM crm_docs WHERE path=$1 FOR UPDATE', path)
            if op == 'update':
                value = merge(json.loads(old) if old else {}, value)
            await conn.execute(
                """INSERT INTO crm_docs(path,data,updated_at) VALUES($1,$2::jsonb,now())
                   ON CONFLICT(path) DO UPDATE SET data=EXCLUDED.data, updated_at=now()""", path, json.dumps(value))
    else:
        raise ValueError('Неверная запись.')
    return web.json_response({'ok': True})


async def api_cabinet(request):
    who = await viewer(request)
    if who.role == 'client':
        name = who.client_key
    elif who.role == 'director':
        name = request.query.get('client')
        if not name:
            return web.json_response({'clients': (await cabinet.summary())['clients']})
    else:
        raise Forbidden('Кабинет клиента недоступен.')
    try:
        return web.json_response(await cabinet.cabinet(name))
    except LookupError as exc:
        raise ValueError(str(exc))


async def api_callcenter(request):
    await director(request)
    try:
        return web.json_response(await cabinet.callcenter())
    except LookupError as exc:
        raise ValueError(str(exc))


async def api_recording(request):
    who = await actor(request)
    try:
        data, mime = await recording.get(request.app[POOL], who, int_id(request.match_info['id']))
    except recording.NoRecording as exc:
        return fail(404, str(exc))
    headers = {'Accept-Ranges': 'bytes', 'Content-Type': mime}
    part = recording.byte_range(request.headers.get('Range'), len(data))
    if part is None:
        return web.Response(body=data, headers=headers)
    start, end = part
    headers['Content-Range'] = f'bytes {start}-{end}/{len(data)}'
    return web.Response(status=206, body=data[start:end + 1], headers=headers)


async def api_review(request):
    who = await viewer(request)
    if who.actor is None:
        raise Forbidden('Этот раздел недоступен.')
    data = await reviews.review(request.app[POOL], who.actor, int_id(request.match_info['id']))
    data['can_train'] = data['can_train'] and not who.test
    return web.json_response(data)


async def api_review_train(request):
    who = await actor(request, write=True)
    assignment_id = await review_store.train_assignment(request.app[POOL], who, int_id(request.match_info['id']))
    return web.json_response({'link': training.link(assignment_id)})


async def api_trainings(request):
    who = await actor(request)
    return web.json_response(await training.listing(request.app[POOL], who, request.query.get('ext') or None))


async def api_training(request):
    return web.json_response(await training.detail(request.app[POOL], await actor(request), int_id(request.match_info['id'])))


async def api_training_assign(request):
    who = await director(request)
    data = await body(request)
    return web.json_response(await training.assign(request.app[POOL], who.actor, data.get('person_ext'),
                                                   str(data.get('mode') or ''), data.get('topic')))


def base_url(request) -> str:
    url = (os.getenv('CRM_URL') or '').strip().rstrip('/')
    return url if url.startswith('https://') else f'{request.scheme}://{request.host}'


async def api_access(request):
    who = await director(request)
    pool = request.app[POOL]
    try:
        clients = [c['name'] for c in (await cabinet.summary())['clients']]
    except Exception:
        log.exception('список клиентов колл-центра не загрузился')
        clients = []
    return web.json_response({'invites': await auth.invites(pool, who.actor.client_id),
                              'managers': await store.team(pool, who.actor.client_id), 'clients': clients,
                              'directors': await auth.directors(pool, who.actor.client_id)})


async def api_access_create(request):
    who = await director(request)
    data = await body(request)
    role = str(data.get('role') or '')
    if role == 'client' and str(data.get('client') or '') not in [c['name'] for c in (await cabinet.summary())['clients']]:
        raise ValueError('Такого клиента нет.')
    token = await auth.invite_create(request.app[POOL], who.actor.client_id, role, data.get('person_ext'), data.get('client'),
                                     data.get('name'), data.get('tg'))
    return web.json_response({'link': f'{base_url(request)}/p/{token}'})


async def api_access_revoke(request):
    who = await director(request)
    await auth.invite_revoke(request.app[POOL], who.actor.client_id, int_id(request.match_info['id']))
    return web.json_response({'ok': True})


async def api_staff(request):
    who = await director(request)
    pool = request.app[POOL]
    data = await staff.listing(pool, who.actor)
    stats = await people.summary(pool, who.actor.client_id)
    # Вход в CRM: сколько действующих личных ссылок и когда входил последний раз.
    tg = {p['ext']: p['tg'] for p in await staff._people(pool, who.actor.client_id)}
    links = await pool.fetch('SELECT extension, telegram_user_id, last_used_at FROM crm_invites WHERE client_id=$1 AND revoked_at IS NULL',
                             who.actor.client_id)
    for item in data['items']:
        item['stats'] = stats.get(item['ext']) if item['role'] == 'manager' else None
        item['kitchen'] = people.review_kitchen.handles(item['ext'])
        own = [r for r in links if r['extension'] == item['ext'] or (tg.get(item['ext']) and r['telegram_user_id'] == tg[item['ext']])]
        used = max((r['last_used_at'] for r in own if r['last_used_at']), default=None)
        item['crm_links'], item['crm_used'] = len(own), used.isoformat() if used else None
    return web.json_response(data)


async def api_person(request):
    """Карточка сотрудника. Директор выбирает человека (?person=), менеджер видит только себя."""
    who = await actor(request)
    return web.json_response(await people.card(request.app[POOL], who, request.query.get('person')))


async def api_deals(request):
    """Сделки: директор видит всех (?person= — одного менеджера), менеджер только свои."""
    who = await actor(request)
    days = request.query.get('days')
    return web.json_response(await deals.listing(request.app[POOL], who, request.query.get('person') or None,
                                                 int(days) if days and days.isdigit() else None))


async def api_deal(request):
    who = await actor(request)
    pool = request.app[POOL]
    deal = await deals.one(pool, who, request.match_info['phone'])
    return web.json_response({**deal, 'contracts': await contracts.for_deal(pool, who.client_id, deal['phone'])})


async def author_name(pool, who) -> str | None:
    name = await pool.fetchval('SELECT full_name FROM employees WHERE client_id=$1 AND telegram_user_id=$2 LIMIT 1',
                               who.client_id, who.telegram_user_id) if who.telegram_user_id else None
    return name or ('Директор' if who.role == 'owner' else None)


async def api_deal_event(request):
    who = await actor(request, write=True)
    pool = request.app[POOL]
    data = await body(request)
    return web.json_response(await deals.add_event(pool, who, request.match_info['phone'], str(data.get('kind') or ''),
                                                   data.get('note'), data.get('reason'), data.get('amount'),
                                                   await author_name(pool, who)))


async def api_presentation_link(request):
    """«Привязать к клиенту»: номер клиента для презентации, которая шла не через Mango."""
    who = await actor(request, write=True)
    pool = request.app[POOL]
    data = await body(request)
    try:
        presentation_id = int(request.match_info['id'])
    except ValueError:
        raise web.HTTPNotFound()
    return web.json_response(await deals.link_presentation(pool, who, presentation_id, str(data.get('phone') or ''),
                                                           await author_name(pool, who)))


# Генератор договоров (static/contracts/): пути — те, что ждёт страница генератора.
async def api_contracts_config(request):
    await actor(request)
    return web.json_response(contracts.config())


async def api_contracts_next(request):
    who = await actor(request)
    return web.json_response({'num': await contracts.next_number(request.app[POOL], who.client_id)})


async def api_contracts_register(request):
    who = await actor(request, write=True)
    pool = request.app[POOL]
    return web.json_response(await contracts.register(pool, who, await body(request), await author_name(pool, who)))


async def api_contracts(request):
    who = await actor(request)
    return web.json_response({'items': await contracts.listing(request.app[POOL], who),
                              'types': contracts.TYPES})


async def api_contracts_lookup(request):
    await actor(request)
    kind = {'inn': 'party', 'bik': 'bank'}.get(request.match_info['kind'])
    if not kind:
        raise web.HTTPNotFound()
    return web.json_response(await contracts.lookup(kind, request.match_info['value']))


async def api_sources(request):
    """Сохранённые подборки доноров отдела (страница «Источники»)."""
    return web.json_response({'items': await sources.listing(request.app[POOL], await actor(request))})


async def api_source(request):
    return web.json_response(await sources.one(request.app[POOL], await actor(request), int_id(request.match_info['id'])))


async def api_source_save(request):
    who = await actor(request, write=True)
    return web.json_response(await sources.save(request.app[POOL], who, await body(request)))


async def api_source_delete(request):
    await sources.delete(request.app[POOL], await actor(request, write=True), int_id(request.match_info['id']))
    return web.json_response({'ok': True})


async def api_staff_add(request):
    who = await director(request)
    data = await body(request)
    return web.json_response(await staff.add(request.app[POOL], who.actor, str(data.get('role') or ''), data.get('person_ext'),
                                             data.get('name'), data.get('username')))


async def api_staff_action(request):
    """Действие над сотрудником: invite — новая ссылка в бота, unlink — отключить, copies — копии разборов."""
    who = await director(request)
    data = await body(request)
    pool, ext, action = request.app[POOL], data.get('person_ext'), request.match_info['action']
    if action == 'invite':
        return web.json_response(await staff.invite(pool, who.actor, ext))
    if action == 'unlink':
        await staff.unlink(pool, who.actor, ext)
    elif action == 'copies':
        await staff.set_copies(pool, who.actor, ext, bool(data.get('on')))
    else:
        raise web.HTTPNotFound()
    return web.json_response({'ok': True})


async def api_prompts(request):
    return web.json_response(await prompt_mode.overview(request.app[POOL], await director(request)))


async def api_prompt(request):
    return web.json_response(await prompt_mode.state(request.app[POOL], await director(request), request.match_info['kind']))


async def api_prompt_action(request):
    who = await director(request)
    data = await body(request)
    pool, kind, action = request.app[POOL], request.match_info['kind'], request.match_info['action']
    if action == 'edit':
        out = await prompt_mode.edit(pool, who, kind, data.get('body'), data.get('history'), data.get('ask'))
    elif action == 'preview':
        out = await prompt_mode.preview(pool, who, kind, data.get('body'))
    elif action == 'save':
        out = await prompt_mode.save(pool, who, kind, data.get('body'), data.get('note'), str(data.get('scope') or ''),
                                     data.get('token'))
    elif action == 'personal-off':
        await prompt_mode.drop_personal(pool, who, kind)
        out = {'ok': True}
    else:
        raise web.HTTPNotFound()
    return web.json_response(out)


async def api_prompt_version(request):
    return web.json_response(await prompt_mode.version_body(request.app[POOL], await director(request),
                                                            int_id(request.match_info['id'])))


async def api_prompt_use(request):
    who = await director(request)
    data = await body(request)
    return web.json_response(await prompt_mode.use(request.app[POOL], who, int_id(request.match_info['id']),
                                                   str(data.get('scope') or '')))


async def api_robot(request):
    return web.json_response(await robot.overview(await director(request)))


async def api_robot_calls(request):
    return web.json_response({'items': await robot.calls(await director(request))})


async def api_robot_settings(request):
    who = await director(request)
    return web.json_response(await robot.save_settings(who, await body(request)))


async def api_robot_preview(request):
    who = await director(request)
    pcm, rate = await robot.preview(who, await body(request))
    return web.Response(body=pcm, content_type='application/octet-stream', headers={'X-Rate': str(rate)})


async def api_robot_voices(request):
    who = await director(request)
    q = request.query
    return web.json_response(await robot.voices_search(who, q.get('q', ''), q.get('gender', ''), q.get('use', ''),
                                                       q.get('page', '0')))


async def api_robot_voice_add(request):
    who = await director(request)
    return web.json_response(await robot.voices_add(who, await body(request)))


async def api_robot_voice_sample(request):
    who = await director(request)
    audio, kind = await robot.voices_preview(who, request.query.get('url', ''))
    return web.Response(body=audio, content_type=kind.split(';')[0] if kind.startswith('audio/') else 'audio/mpeg')


async def api_robot_voice_clone(request):
    """Записи для клона приходят формой multipart; читаем сами, общий предел запроса (512 КБ) для них мал."""
    who = await director(request)
    if not request.content_type.startswith('multipart/'):
        raise ValueError('Неверный запрос.')
    if request.content_length and request.content_length > robot.CLONE_MAX_BYTES:
        raise ValueError('Записи больше 20 МБ: сократите или сожмите в mp3.')
    reader = await request.multipart()
    name, files, total = '', [], 0
    while (part := await reader.next()) is not None:
        if part.name == 'name':
            name = (await part.text())[:60]
        elif part.name == 'files' and part.filename:
            chunks = []
            while chunk := await part.read_chunk(256 * 1024):
                total += len(chunk)
                if total > robot.CLONE_MAX_BYTES:
                    raise ValueError('Записи больше 20 МБ: сократите или сожмите в mp3.')
                chunks.append(chunk)
            files.append((part.filename[-80:], b''.join(chunks)))
    return web.json_response(await robot.voices_clone(who, name, files))


async def api_robot_call(request):
    who = await director(request)
    data = await body(request)
    return web.json_response(await robot.call(who, data.get('phone'), data.get('operator')))


async def api_reports(request):
    return web.json_response({'items': await journal.listing(request.app[POOL], await actor(request))})


async def api_ask_history(request):
    return web.json_response({'items': await asking.history(request.app[POOL], await actor(request))})


async def api_ask(request):
    who = await viewer(request)
    if who.actor is None or (who.test and who.role != 'director'):
        raise Forbidden(TEST_READONLY if who.test else 'Этот раздел недоступен.')
    data = await body(request)
    return web.json_response(await asking.ask(request.app[POOL], who.actor, data.get('question')))


async def api_queue(request):
    who = await actor(request)
    return web.json_response(await store.queue(request.app[POOL], who.client_id, await ext_of(request, who)))


async def api_tasks(request):
    who = await actor(request)
    return web.json_response(await store.tasks(request.app[POOL], who.client_id, await ext_of(request, who)))


async def api_task_change(request):
    who = await actor(request, write=True)
    data = await body(request)
    await store.change_task(request.app[POOL], who, who.client_id, await ext_of(request, who, data.get('ext')),
                            int_id(request.match_info['id']), str(data.get('action') or ''),
                            parse_date(data.get('due_date')), parse_time(data.get('due_time')))
    return web.json_response({'ok': True})


async def api_leads(request):
    who = await actor(request)
    status = request.query.get('status') or None
    if status and status not in store.LEAD_STATUS:
        raise ValueError('Неизвестный статус.')
    return web.json_response(await store.leads(request.app[POOL], who.client_id, await ext_of(request, who), status))


async def api_leads_add(request):
    who = await actor(request, write=True)
    data = await body(request)
    result = await store.add_leads(request.app[POOL], who.client_id, await ext_of(request, who, data.get('ext')),
                                   str(data.get('text') or ''), data.get('source'))
    return web.json_response(result)


async def api_lead_delete(request):
    who = await actor(request, write=True)
    await store.delete_lead(request.app[POOL], who.client_id, await ext_of(request, who), int_id(request.match_info['id']))
    return web.json_response({'ok': True})


async def api_card(request):
    who = await actor(request)
    return web.json_response(await store.card(request.app[POOL], who, who.client_id, await ext_of(request, who),
                                              request.query.get('number')))


async def api_dial(request):
    who = await actor(request, write=True)
    data = await body(request)
    # Только со своего добавочного: руководитель без добавочного звонить не может.
    result = await dial(request.app[POOL], who.client_id, who.extension, data.get('number'), who.telegram_user_id)
    return web.json_response(result)


async def api_outcome(request):
    who = await actor(request, write=True)
    data = await body(request)
    dial_id = data.get('dial_id')
    result = await store.outcome(
        request.app[POOL], who, who.client_id, await ext_of(request, who, data.get('ext')), data.get('number'),
        str(data.get('outcome') or ''), data.get('comment'), parse_date(data.get('due_date')),
        parse_time(data.get('due_time')), int_id(dial_id) if dial_id else None)
    return web.json_response(result)


async def api_calls(request):
    who = await actor(request)
    days = min(max(int(request.query.get('days') or 7), 1), 31)
    return web.json_response(await store.my_calls(request.app[POOL], who, who.client_id, await ext_of(request, who), days))


async def api_stats(request):
    who = await actor(request)
    days = min(max(int(request.query.get('days') or 7), 1), 31)
    ext = await ext_of(request, who) if (not is_privileged(who) or request.query.get('ext')) else None
    return web.json_response(await store.stats(request.app[POOL], who.client_id, ext, days))


def period_arg(request) -> str:
    return request.query.get('period') or '7'


async def api_summary(request):
    who = await actor(request)
    return web.json_response(await overview.summary(request.app[POOL], who, period_arg(request), request.query.get('ext') or None))


async def api_journal(request):
    who = await actor(request)
    q = request.query
    try:
        offset = int(q.get('offset') or 0)
    except ValueError:
        raise ValueError('Неверное смещение.')
    return web.json_response(await overview.journal(
        request.app[POOL], who, period_arg(request), q.get('ext') or None, q.get('result') or '', q.get('kind') or '',
        (q.get('q') or '')[:80], offset))


async def api_callbacks(request):
    who = await actor(request)
    return web.json_response(await overview.callbacks(request.app[POOL], who, request.query.get('ext') or None))


def build(pool) -> web.Application:
    app = web.Application(middlewares=[guard], client_max_size=512 * 1024)
    app[POOL] = pool
    app.router.add_get('/', index)
    app.router.add_get('/t/{key}', test_page)
    app.router.add_get('/p/{token}', personal_page)
    app.router.add_get('/test-exit', test_exit)
    app.router.add_get('/logout', logout_page)
    app.router.add_get('/director', old_director)
    app.router.add_get('/director/{name}', old_director)
    app.router.add_static('/static/', STATIC, show_index=False)
    app.router.add_post('/api/logout', api_logout)
    app.router.add_get('/api/me', api_me)
    app.router.add_get('/api/test/options', api_test_options)
    app.router.add_post('/api/test/login', api_test_login)
    app.router.add_get('/api/docs', api_docs)
    app.router.add_post('/api/docs', api_docs_write)
    app.router.add_get('/api/cabinet', api_cabinet)
    app.router.add_get('/api/callcenter', api_callcenter)
    app.router.add_get('/api/recording/{id}', api_recording)
    app.router.add_get('/api/review/{id}', api_review)
    app.router.add_get('/api/reports', api_reports)
    app.router.add_get('/api/ask', api_ask_history)
    app.router.add_post('/api/ask', api_ask)
    app.router.add_post('/api/review/{id}/train', api_review_train)
    app.router.add_get('/api/trainings', api_trainings)
    app.router.add_post('/api/trainings', api_training_assign)
    app.router.add_get('/api/trainings/{id}', api_training)
    app.router.add_get('/api/access', api_access)
    app.router.add_post('/api/access', api_access_create)
    app.router.add_post('/api/access/{id}/revoke', api_access_revoke)
    app.router.add_get('/api/staff', api_staff)
    app.router.add_get('/api/person', api_person)
    app.router.add_get('/api/deals', api_deals)
    app.router.add_get('/api/deals/{phone}', api_deal)
    app.router.add_post('/api/deals/{phone}/event', api_deal_event)
    app.router.add_get('/api/sources/selections', api_sources)
    app.router.add_post('/api/sources/selections', api_source_save)
    app.router.add_get('/api/sources/selections/{id}', api_source)
    app.router.add_post('/api/sources/selections/{id}/delete', api_source_delete)
    app.router.add_post('/api/presentations/{id}/link', api_presentation_link)
    app.router.add_get('/api/contracts', api_contracts)
    app.router.add_get('/api/contracts/config', api_contracts_config)
    app.router.add_get('/api/contracts/next-number', api_contracts_next)
    app.router.add_post('/api/contracts/register', api_contracts_register)
    app.router.add_get('/api/contracts/lookup/{kind}/{value}', api_contracts_lookup)
    app.router.add_post('/api/staff', api_staff_add)
    app.router.add_post('/api/staff/{action}', api_staff_action)
    app.router.add_get('/api/prompts', api_prompts)
    app.router.add_get('/api/robot', api_robot)
    app.router.add_get('/api/robot/calls', api_robot_calls)
    app.router.add_post('/api/robot/settings', api_robot_settings)
    app.router.add_post('/api/robot/preview', api_robot_preview)
    app.router.add_post('/api/robot/call', api_robot_call)
    app.router.add_get('/api/robot/voices', api_robot_voices)
    app.router.add_post('/api/robot/voices/add', api_robot_voice_add)
    app.router.add_get('/api/robot/voices/sample', api_robot_voice_sample)
    app.router.add_post('/api/robot/voices/clone', api_robot_voice_clone)
    app.router.add_routes(robot_api.routes(viewer, body, POOL))
    app.router.add_get('/api/prompts/version/{id}', api_prompt_version)
    app.router.add_post('/api/prompts/version/{id}/use', api_prompt_use)
    app.router.add_get('/api/prompts/{kind}', api_prompt)
    app.router.add_post('/api/prompts/{kind}/{action}', api_prompt_action)
    app.router.add_get('/api/summary', api_summary)
    app.router.add_get('/api/journal', api_journal)
    app.router.add_get('/api/callbacks', api_callbacks)
    app.router.add_get('/api/queue', api_queue)
    app.router.add_get('/api/tasks', api_tasks)
    app.router.add_post('/api/tasks/{id}', api_task_change)
    app.router.add_get('/api/leads', api_leads)
    app.router.add_post('/api/leads', api_leads_add)
    app.router.add_delete('/api/leads/{id}', api_lead_delete)
    app.router.add_get('/api/card', api_card)
    app.router.add_post('/api/dial', api_dial)
    app.router.add_post('/api/outcome', api_outcome)
    app.router.add_get('/api/calls', api_calls)
    app.router.add_get('/api/stats', api_stats)
    return app


async def make_app() -> web.Application:
    pool = await asyncpg.create_pool(os.environ['DATABASE_URL'], min_size=1, max_size=5)
    app = build(pool)

    async def close(_app):
        await pool.close()
        await mango_client.close()
    app.on_cleanup.append(close)

    async def robot_cabinet(_app):  # кампании робота по расписанию и перенос итогов его звонков
        task = asyncio.create_task(robot_runner.loop(pool)) if robot_runner.enabled() else None
        yield
        if task:
            task.cancel()
    app.cleanup_ctx.append(robot_cabinet)
    return app


def main():
    logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'), format='%(asctime)s %(levelname)s %(name)s %(message)s')
    web.run_app(make_app(), host=os.getenv('CRM_HOST', '127.0.0.1'), port=int(os.getenv('CRM_PORT', '8095')),
                access_log=None)


if __name__ == '__main__':
    main()
