"""Dormant local daily snapshots. No sending and no implicit catch-up."""
import json
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from . import personal, personal_day


def enabled():
    return os.getenv('CRM_PERSONAL_DAY_RULES_ENABLED') == '1'


def due_kinds(settings, now):
    """Only the current local minute is eligible; missed minutes are not replayed."""
    if now.tzinfo is None or now.utcoffset() is None: raise ValueError('now must have a timezone')
    local = now.astimezone(ZoneInfo(settings['timezone']))
    minute = local.time().replace(second=0, microsecond=0, tzinfo=None)
    return local, [kind for kind in ('morning', 'evening') if settings[kind + '_time'] == minute]


async def _eligible(conn, company_id, tg):
    """Re-check the currently active employee and invitation inside the transaction."""
    if tg not in personal.pilot_ids(): return None
    rows = await conn.fetch('''SELECT p.id AS owner_id,p.timezone,s.morning_time,s.evening_time,
                s.work_start,s.work_end,s.priorities,s.version
        FROM crm_personal_profiles p JOIN crm_personal_day_settings s ON s.owner_id=p.id
        JOIN employees e ON e.telegram_user_id=p.telegram_user_id AND e.client_id=$1 AND e.archived_at IS NULL
            AND e.role IN ('owner','head','manager')
        JOIN crm_invites i ON i.telegram_user_id=e.telegram_user_id AND i.client_id=e.client_id AND i.revoked_at IS NULL
        WHERE p.telegram_user_id=$2 FOR SHARE OF p,s,e,i''', company_id, tg)
    return rows[0] if rows else None


async def _snapshot(conn, company_id, tg, config, day, zone):
    personal_rows = await conn.fetch("SELECT id,title,status,due_at,planned_day,blocker FROM crm_personal_tasks WHERE owner_id=$1 AND status NOT IN ('done','cancelled') ORDER BY id", config['owner_id'])
    team_rows = await conn.fetch("SELECT id,title,status,due_at,blocker FROM crm_team_assignments WHERE company_id=$1 AND assignee_id=$2 AND status NOT IN ('accepted','cancelled') ORDER BY id", company_id, tg)
    groups = {'today':[], 'overdue':[], 'blocked':[], 'unscheduled':[]}
    for kind, rows in (('personal', personal_rows), ('team', team_rows)):
        for row in rows:
            category = personal_day.classify(row, day, zone, kind)
            if category:
                groups[category].append({'kind':kind, 'id':row['id'], 'title':row['title'], 'status':row['status'],
                                         'due_at':row['due_at'].isoformat() if row['due_at'] else None})
    priorities = config['priorities']
    if isinstance(priorities, str): priorities = json.loads(priorities)
    return {'date':day.isoformat(), 'timezone':str(zone), 'work_start':config['work_start'].strftime('%H:%M'),
            'work_end':config['work_end'].strftime('%H:%M'), 'priorities':priorities, 'tasks':groups}


async def tick(pool, *, now=None):
    """Record due snapshots; return inserted IDs. No network or notifications.

    Called by an existing worker at least once per minute only after explicit enablement.
    """
    if not enabled(): return []
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None: raise ValueError('now must have a timezone')
    pilots = personal.pilot_ids()
    if not pilots: return []
    candidates = await pool.fetch('''SELECT DISTINCT e.client_id,e.telegram_user_id FROM employees e
        JOIN crm_invites i ON i.client_id=e.client_id AND i.telegram_user_id=e.telegram_user_id AND i.revoked_at IS NULL
        JOIN crm_personal_profiles p ON p.telegram_user_id=e.telegram_user_id
        JOIN crm_personal_day_settings s ON s.owner_id=p.id
        WHERE e.telegram_user_id=ANY($1::bigint[]) AND e.archived_at IS NULL
        AND e.role IN ('owner','head','manager') AND (s.morning_time IS NOT NULL OR s.evening_time IS NOT NULL)''', list(pilots))
    companies = {}
    for row in candidates: companies.setdefault(row['telegram_user_id'], set()).add(row['client_id'])
    inserted = []
    for row in candidates:
        company, tg = row['client_id'], row['telegram_user_id']
        # A Telegram identity mapped to several companies is ambiguous without a session.
        if len(companies[tg]) != 1: continue
        async with pool.acquire() as conn, conn.transaction():
            config = await _eligible(conn, company, tg)
            if not config: continue
            try: local, kinds = due_kinds(config, now)
            except (ZoneInfoNotFoundError, ValueError): continue
            if not kinds: continue
            snapshot = await _snapshot(conn, company, tg, config, local.date(), local.tzinfo)
            for kind in kinds:
                run_id = await conn.fetchval('''INSERT INTO crm_personal_day_rule_runs
                    (company_id,owner_id,telegram_user_id,local_date,kind,timezone,snapshot)
                    VALUES($1,$2,$3,$4,$5,$6,$7::jsonb)
                    ON CONFLICT(company_id,telegram_user_id,local_date,kind) DO NOTHING RETURNING id''',
                    company, config['owner_id'], tg, local.date(), kind, config['timezone'], json.dumps(snapshot, ensure_ascii=False))
                if run_id is not None: inserted.append(run_id)
    return inserted
