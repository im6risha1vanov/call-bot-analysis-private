"""Сайт CRM менеджера: python -m crm.web (сервис callbot-crm).

Слушает только 127.0.0.1, наружу его отдаёт Caddy с HTTPS (scripts/crm_setup.sh).
Страница и API в одном процессе; данные в той же базе, что у бота.
"""
from __future__ import annotations

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
from . import auth, store  # noqa: E402
from .dial import dial  # noqa: E402

log = logging.getLogger('crm')
STATIC = Path(__file__).resolve().parent / 'static'
COOKIE = 'crm_session'
POOL = web.AppKey('pool', asyncpg.Pool)
SECURE_COOKIE = os.getenv('CRM_COOKIE_SECURE', '1') != '0'
HEADERS = {
    'Content-Security-Policy': "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                               "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    'X-Frame-Options': 'DENY',
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer',
}


def fail(status: int, text: str) -> web.Response:
    return web.json_response({'error': text}, status=status)


@web.middleware
async def guard(request: web.Request, handler):
    # Запросы с другого сайта не могут поставить свой заголовок без CORS, а CORS мы не разрешаем.
    if request.method not in ('GET', 'HEAD') and request.headers.get('X-CRM') != '1':
        return fail(403, 'Запрос отклонён.')
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


async def actor(request: web.Request):
    who = await auth.actor_for(request.app[POOL], request.cookies.get(COOKIE))
    if who is None:
        raise web.HTTPUnauthorized(reason='Войдите по ссылке из бота: команда /crm.')
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
    return web.FileResponse(STATIC / 'index.html')


async def login_page(request):
    # Сама ссылка ничего не меняет: токен тратит POST со страницы, иначе его
    # съест предпросмотр ссылки в Telegram.
    return web.FileResponse(STATIC / 'login.html')


# ---- API ----

async def api_login(request):
    data = await body(request)
    session = await auth.redeem(request.app[POOL], str(data.get('token') or ''))
    if session is None:
        return fail(401, 'Ссылка устарела или уже использована. Получите новую в боте: /crm.')
    response = web.json_response({'ok': True})
    response.set_cookie(COOKIE, session, max_age=int(auth.SESSION_TTL.total_seconds()), httponly=True,
                        secure=SECURE_COOKIE, samesite='Strict', path='/')
    return response


async def api_logout(request):
    await auth.logout(request.app[POOL], request.cookies.get(COOKIE))
    response = web.json_response({'ok': True})
    response.del_cookie(COOKIE, path='/')
    return response


async def api_me(request):
    who = await actor(request)
    pool = request.app[POOL]
    name = await pool.fetchval('SELECT full_name FROM employees WHERE client_id=$1 AND telegram_user_id=$2 LIMIT 1',
                               who.client_id, who.telegram_user_id)
    privileged = is_privileged(who)
    return web.json_response({
        'name': name or '', 'role': who.role, 'ext': who.extension if str(who.extension or '').isdigit() else None,
        'can_dial': str(who.extension or '').isdigit(), 'privileged': privileged,
        'team': await store.team(pool, who.client_id) if privileged else [],
        'outcomes': store.OUTCOMES, 'needs_date': sorted(store.NEEDS_DATE), 'lead_status': store.LEAD_STATUS,
    })


async def api_queue(request):
    who = await actor(request)
    return web.json_response(await store.queue(request.app[POOL], who.client_id, await ext_of(request, who)))


async def api_tasks(request):
    who = await actor(request)
    return web.json_response(await store.tasks(request.app[POOL], who.client_id, await ext_of(request, who)))


async def api_task_change(request):
    who = await actor(request)
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
    who = await actor(request)
    data = await body(request)
    result = await store.add_leads(request.app[POOL], who.client_id, await ext_of(request, who, data.get('ext')),
                                   str(data.get('text') or ''), data.get('source'))
    return web.json_response(result)


async def api_lead_delete(request):
    who = await actor(request)
    await store.delete_lead(request.app[POOL], who.client_id, await ext_of(request, who), int_id(request.match_info['id']))
    return web.json_response({'ok': True})


async def api_card(request):
    who = await actor(request)
    return web.json_response(await store.card(request.app[POOL], who, who.client_id, await ext_of(request, who),
                                              request.query.get('number')))


async def api_dial(request):
    who = await actor(request)
    data = await body(request)
    # Только со своего добавочного: руководитель без добавочного звонить не может.
    result = await dial(request.app[POOL], who.client_id, who.extension, data.get('number'), who.telegram_user_id)
    return web.json_response(result)


async def api_outcome(request):
    who = await actor(request)
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


def build(pool) -> web.Application:
    app = web.Application(middlewares=[guard], client_max_size=512 * 1024)
    app[POOL] = pool
    app.router.add_get('/', index)
    app.router.add_get('/in/{token}', login_page)
    app.router.add_static('/static/', STATIC, show_index=False)
    app.router.add_post('/api/login', api_login)
    app.router.add_post('/api/logout', api_logout)
    app.router.add_get('/api/me', api_me)
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
    return app


def main():
    logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'), format='%(asctime)s %(levelname)s %(name)s %(message)s')
    web.run_app(make_app(), host=os.getenv('CRM_HOST', '127.0.0.1'), port=int(os.getenv('CRM_PORT', '8095')),
                access_log=None)


if __name__ == '__main__':
    main()
