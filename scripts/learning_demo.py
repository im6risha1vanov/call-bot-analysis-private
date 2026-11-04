"""Local fictional CRM bench. Never imports crm.web, reads .env or creates a DB pool.

Run: python scripts/learning_demo.py --port 8098
Open http://127.0.0.1:8098/demo/manager or /demo/director. It is a loopback-only
stand, with no passwords and deliberately selectable synthetic roles. Never expose
it via Caddy, public bind or production systemd. All POST/PUT/PATCH/DELETE rejected.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiohttp import web
from crm import learning

STATIC = Path(__file__).resolve().parents[1] / 'crm/static'
MODULES = ['contracts', 'automations', 'robot', 'gpt']


def identity(request):
    token = request.cookies.get('learning_demo', '')
    choices = {'manager': ('manager', 1, 101), 'director': ('director', 1, 201),
               'client': ('client', 1, 301), 'manager-other': ('manager', 1, 102),
               'director-other-company': ('director', 2, 202),
               'manager-base': ('manager', 1, 103), 'director-base': ('director', 1, 203)}
    if token not in choices:
        raise web.HTTPUnauthorized(reason='Откройте /demo/manager или /demo/director')
    role, company, account = choices[token]
    actor = SimpleNamespace(client_id=company, telegram_user_id=account, extension='101', role='manager' if role == 'manager' else 'owner') if role != 'client' else None
    return SimpleNamespace(role=role, company=company, actor=actor, invite_id=account,
                           test=False, support_by=None, modules=[] if token.endswith('-base') else MODULES)


def ctx(request, lesson=None):
    who = identity(request)
    return learning.context(who, {'id': who.company, 'modules': who.modules}, 'Europe/Moscow', lesson)


@web.middleware
async def isolated(request, handler):
    if request.method not in {'GET', 'HEAD'}:
        return web.json_response({'error': 'Учебный стенд: рабочие записи и внешние вызовы запрещены.'}, status=403)
    response = await handler(request)
    response.headers.setdefault('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
    response.headers['Cache-Control'] = 'no-store'
    return response


async def login(request):
    role = request.match_info['role']
    if role not in {'manager', 'director', 'client', 'manager-other', 'director-other-company', 'manager-base', 'director-base'}:
        raise web.HTTPNotFound()
    response = web.HTTPFound('/#learning')
    response.set_cookie('learning_demo', role, httponly=True, samesite='Strict')
    return response


async def index(request):
    if not request.cookies.get('learning_demo'):
        return web.Response(text='<h1>Учебный стенд AveronCRM</h1><p>Данные вымышленные, внешних вызовов нет.</p><a href="/demo/manager">Менеджер</a> · <a href="/demo/director">Директор</a> · <a href="/demo/director-base">Директор без модулей</a>', content_type='text/html')
    identity(request)
    return web.FileResponse(STATIC / 'index.html')


async def learning_context(request):
    return web.json_response(ctx(request, request.query.get('lesson')))


async def practice(request):
    lesson = request.query.get('lesson')
    if not lesson:
        raise web.HTTPBadRequest(reason='Укажите урок')
    ctx(request, lesson)
    return learning.practice_response()


async def fixture_api(request):
    who = identity(request)
    route = request.match_info['tail']
    clock = learning.clock_for('Europe/Moscow')
    today = clock['today']
    team = [{'ext': '101', 'name': 'Анна Учебная'}, {'ext': '102', 'name': 'Борис Учебный'}]
    if route == 'me':
        return web.json_response({'name': 'Учебный сотрудник', 'company': 'Учебная компания', 'role': who.role,
            'ext': '101', 'test': False, 'readonly': False, 'modules': who.modules,
            'privileged': who.role == 'director', 'can_dial': False, 'platform': False, 'switcher': False,
            'team': team if who.role == 'director' else [], 'outcomes': {'no_answer': 'Не взял трубку', 'callback': 'Перезвонить', 'meeting': 'Назначил встречу', 'proposal': 'Отправил КП', 'interested': 'Интерес, думает', 'not_interested': 'Отказ', 'bad_number': 'Неверный номер'}, 'needs_date': ['callback', 'meeting'], 'lead_status': {}, 'robot': False})
    if route == 'queue':
        return web.json_response({'today': today, 'later': 0, 'items': [
            {'kind':'lead','id':1,'number':'79990000000','title':'Учебный Кедр','note':'Вымышленный клиент для экскурсии','status':'new','attempts':0}]})
    if route == 'card':
        return web.json_response({'calls':[], 'outcomes':[], 'tasks':[]})
    if route == 'callbacks':
        return web.json_response({'today': today, 'items': []})
    if route == 'summary':
        cur = {'dials':82,'live':47,'result':19,'meet':6,'seconds':2400,'lost':4}
        prev = {'dials':74,'live':39,'result':14,'meet':4,'seconds':2000,'lost':5}
        managers = [{**cur,'name':m['name'],'ext':m['ext'],'mistake':None} for m in team]
        return web.json_response({'today':today,'now':'12:00','team':team,'period':request.query.get('period','7'),
            'cur':cur,'prev':prev,'vs':'к предыдущему периоду','hourly':False,'series':[], 'heat':[],
            'heat_days':30,'missed':[],'managers':managers,'live_seconds':10})
    if route == 'staff':
        if who.role != 'director':
            raise web.HTTPForbidden()
        return web.json_response({'items': [{**m, 'role': 'manager', 'role_title': 'Менеджер', 'linked': False, 'crm_links': 0, 'kitchen': False} for m in team]})
    if route == 'access':
        if who.role != 'director':
            raise web.HTTPForbidden()
        return web.json_response({'invites': [], 'directors': []})
    if route == 'reports':
        return web.json_response({'items': [{'title':'Учебный итог дня','who':'Анна Учебная',
            'at':today+'T18:00:00+03:00','note':'Вымышленный отчёт','kind':'day_manager',
            'html':'<p>Согласована отправка предложения. Дата продолжения не согласована.</p>','calls':[]}]})
    if route == 'journal':
        return web.json_response({'items': [], 'results': {}, 'kinds': {}, 'more': False})
    if route == 'gpt/history':
        return web.json_response({'name': 'AveronGPT · стенд', 'items': [], 'models': []})
    if route == 'automations':
        return web.json_response({'automations': [], 'accounts': [], 'usage': {'runs': 0, 'active': 0, 'max_runs_month': None, 'max_active': None}, 'catalog': {'templates': [], 'labels': {}, 'email_ready': False}})
    if route == 'robot/cab/campaigns':
        return web.json_response({'items':[]})
    if route == 'robot/cab':
        return web.json_response({'role': 'director', 'home': True, 'owner': '', 'owners': [], 'enabled': True})
    raise web.HTTPNotFound(reason='Этот рабочий API не подключён на изолированном стенде. Практика доступна в «Обучении».')


async def denied(request):
    return web.json_response({'error': 'Операция отключена на учебном стенде.'}, status=403)


def build():
    app = web.Application(middlewares=[isolated])
    app.router.add_get('/', index)
    app.router.add_get('/demo/{role}', login)
    app.router.add_get('/api/learning/context', learning_context)
    app.router.add_get('/learning/practice', practice)
    app.router.add_get('/api/{tail:.*}', fixture_api)
    # Register assets before wildcard matching (router uses registration order).
    app.router.add_static('/static/', STATIC, show_index=False)
    app.router.add_route('*', '/{tail:.*}', denied)
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8098)
    args = parser.parse_args()
    web.run_app(build(), host='127.0.0.1', port=args.port, access_log=None)


if __name__ == '__main__':
    main()
