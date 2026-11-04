"""Read-only learning catalog and a network-disabled practice document.

No progress, fictional rows or grading is written to PostgreSQL. Browser-local
progress is separated by a server-derived account/company/role scope. Importing
this module does not load .env, connect providers, create a pool or start tasks.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from aiohttp import web

ROOT = Path(__file__).resolve().parent
PRACTICE_CSP = "; ".join((
    "default-src 'none'", "script-src 'self'", "style-src 'self'",
    "connect-src 'none'", "form-action 'none'", "base-uri 'none'",
    "object-src 'none'", "frame-src 'none'", "frame-ancestors 'self'",
    "sandbox allow-scripts",
))


def catalog() -> dict:
    return json.loads((ROOT / 'learning_lessons.json').read_text(encoding='utf-8'))


def clock_for(zone: str, now: datetime | None = None) -> dict:
    """Freeze at attempt start. Translate Moscow appointment into CRM timezone."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('Attempt time must be timezone-aware')
    tz, msk = ZoneInfo(zone), ZoneInfo('Europe/Moscow')
    msk_today = now.astimezone(msk).date()

    def appointment(days, hour, minute):
        at = datetime.combine(msk_today + timedelta(days=days), time(hour, minute), msk)
        local = at.astimezone(tz)
        return {'due_date': local.date().isoformat(), 'due_time': local.strftime('%H:%M'),
                'moscow_date': at.date().isoformat(), 'moscow_time': at.strftime('%H:%M')}
    local_today = now.astimezone(tz).date()
    week_end = local_today - timedelta(days=local_today.weekday() + 1)
    week_start = week_end - timedelta(days=6)
    return {'week_start': week_start.isoformat(), 'week_end': week_end.isoformat(),
            'timezone': zone, 'today': local_today.isoformat(),
            'started_at': now.isoformat(), 'callback': appointment(1, 15, 0),
            'move': appointment(2, 11, 30)}


def context(who, firm: dict, zone: str, lesson_id: str | None = None, *, now=None) -> dict:
    actor = getattr(who, 'actor', None)
    role, company = getattr(who, 'role', None), getattr(who, 'company', None)
    if role not in {'manager', 'director'} or actor is None or company is None:
        raise web.HTTPForbidden(reason='Обучение доступно менеджеру и директору.')
    if actor.client_id != company:
        raise web.HTTPForbidden(reason='Вход и компания не совпадают.')
    account = getattr(who, 'invite_id', None)
    identity = ('invite', account) if account is not None else ('actor', actor.telegram_user_id, actor.extension)
    if account is None and not actor.telegram_user_id and not actor.extension:
        raise web.HTTPForbidden(reason='Не удалось определить учётную запись для обучения.')
    if firm.get('id', company) != company:
        raise web.HTTPForbidden(reason='Настройки относятся к другой компании.')
    modules = list(firm.get('modules') or [])
    scope = hashlib.sha256(json.dumps([company, identity, role, bool(getattr(who, 'test', False)),
                                      getattr(who, 'support_by', None)], ensure_ascii=False).encode()).hexdigest()
    all_lessons = catalog()
    lessons = []
    for original in all_lessons['lessons']:
        if original['role'] != role:
            continue
        lesson = dict(original)
        lesson['available'] = not lesson.get('module') or lesson['module'] in modules
        lesson['unavailable_reason'] = '' if lesson['available'] else 'Модуль «Договоры» отключён у компании.'
        lesson['omitted_guide_modules'] = sorted({s['module'] for s in lesson['guide']
                                                if s.get('module') and s['module'] not in modules})
        lesson['guide'] = [s for s in lesson['guide'] if not s.get('module') or s['module'] in modules]
        lessons.append(lesson)
    if lesson_id is not None:
        selected = next((item for item in lessons if item['id'] == lesson_id), None)
        if selected is None or not selected['available']:
            raise web.HTTPForbidden(reason='Урок недоступен вашей роли или компании.')
    return {'version': all_lessons['version'], 'role': role, 'scope': scope, 'modules': modules,
            'clock': clock_for(zone, now), 'lessons': lessons, 'progress': 'browser_local_only',
            'work_readonly': bool(getattr(who, 'test', False)), 'practice_allowed': True}


def practice_response() -> web.FileResponse:
    return web.FileResponse(ROOT / 'static/learning/practice.html', headers={
        'Content-Security-Policy': PRACTICE_CSP,
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
        'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'SAMEORIGIN',
    })
