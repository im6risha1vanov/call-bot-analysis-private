"""Действия сотрудников в CRM. Записываем шаблоны маршрутов, без тела запроса."""
from __future__ import annotations

import logging

from aiohttp import web

from . import tour

log = logging.getLogger(__name__)
ACTOR = web.RequestKey('activity_actor')
DETAIL = web.RequestKey('activity_detail')
PAGE_SIZE = 50
ROUTES = {
    ('POST', '/api/login'): 'Вход по паролю',
    ('GET', '/p/{token}'): 'Вход по личной ссылке',
    ('POST', '/api/logout'): 'Выход',
    ('GET', '/logout'): 'Выход',
    ('GET', '/api/recording/{id}'): 'Прослушивание записи',
    ('GET', '/api/callcenter/export'): 'Экспорт из колл-центра',
    ('POST', '/api/staff'): 'Добавление сотрудника и приглашение в Telegram',
    ('POST', '/api/staff/register'): 'Создание менеджера с логином',
    ('POST', '/api/staff/archive'): 'Удаление менеджера из активного списка',
    ('POST', '/api/access'): 'Создание учётной записи',
    ('POST', '/api/access/{id}/revoke'): 'Отзыв учётной записи',
    ('POST', '/api/access/{id}/account'): 'Изменение учётной записи',
    ('POST', '/api/tasks/{id}'): 'Изменение перезвона',
    ('POST', '/api/outcome'): 'Сохранение результата звонка',
    ('POST', '/api/dial'): 'Запуск звонка',
    ('POST', '/api/leads'): 'Добавление контактов',
    ('DELETE', '/api/leads/{id}'): 'Удаление контакта',
}
TASK_DETAILS = {'done': 'Перезвон закрыт', 'cancel': 'Перезвон отменён', 'move': 'Срок перезвона изменён'}


def employee(who) -> bool:
    return bool(who and who.actor and who.role in {'manager', 'director'} and not who.test and not who.support)


async def record(pool, request, status: int) -> None:
    who = request.get(ACTOR)
    if not employee(who) or tour.active(request) or not (200 <= status < 400 or status >= 500):
        return
    resource = request.match_info.route.resource
    if resource is None:
        return
    method, route = request.method, resource.canonical
    action = request.get(DETAIL) or ROUTES.get((method, route))
    if action is None:
        if method in {'GET', 'HEAD'} or not route.startswith('/api/') or route == '/api/learning/mode':
            return
        action = f'{method} {route}'
    if route == '/api/recording/{id}' and not request.headers.get('Range', 'bytes=0-').startswith('bytes=0-'):
        return
    try:
        await pool.execute(
            """INSERT INTO crm_activity(client_id, actor_name, actor_role, action, status)
               VALUES($1, COALESCE(
                 (SELECT coalesce(nullif(name,''), login) FROM crm_invites WHERE id=$2 AND client_id=$1),
                 (SELECT nullif(full_name,'') FROM employees WHERE client_id=$1 AND
                    (extension=$3 OR telegram_user_id=$4) ORDER BY extension LIMIT 1),
                 $5), $6, $7, $8)""",
            who.actor.client_id, who.invite_id, who.actor.extension, who.actor.telegram_user_id or None,
            ('Директор' if who.role == 'director' else 'Менеджер'), who.role, action, status)
    except Exception:
        log.exception('не удалось записать действие сотрудника в журнал CRM')


async def listing(pool, client_id: int, before: int | None) -> dict:
    rows = await pool.fetch(
        """SELECT id, created_at, actor_name, actor_role, action, status FROM crm_activity
           WHERE client_id=$1 AND ($2::bigint IS NULL OR id<$2) ORDER BY id DESC LIMIT $3""",
        client_id, before, PAGE_SIZE + 1)
    page = rows[:PAGE_SIZE]
    return {'items': [{'at': row['created_at'].isoformat(), 'who': row['actor_name'], 'role': row['actor_role'],
                       'action': row['action'], 'status': row['status']} for row in page],
            'next': str(page[-1]['id']) if len(rows) > PAGE_SIZE else None}
