"""Durable personal task reminders; uncertain delivery is never retried blindly."""
import asyncio
import logging
from datetime import timedelta
from types import SimpleNamespace
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError
from personal_telegram import authorized, keyboard

log = logging.getLogger(__name__)

async def tick(pool, bot):
    await pool.execute("""UPDATE crm_personal_reminder_delivery SET status='uncertain',updated_at=now()
       WHERE status='sending' AND updated_at < now()-interval '5 minutes'""")
    await pool.execute("""INSERT INTO crm_personal_reminder_delivery(task_id,revision,status)
       SELECT t.id,t.reminder_revision,'pending' FROM crm_personal_tasks t JOIN crm_personal_profiles p ON p.id=t.owner_id
       WHERE t.reminders_enabled AND t.status NOT IN ('done','cancelled') AND coalesce(t.reminder_at, least(t.due_at, (date_trunc('day', t.due_at AT TIME ZONE p.timezone) + interval '9 hours') AT TIME ZONE p.timezone))<=now()
       ON CONFLICT DO NOTHING""")
    for _ in range(10):
        async with pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow("""SELECT d.task_id,d.revision FROM crm_personal_reminder_delivery d
                WHERE d.status='pending' AND d.retry_at<=now() ORDER BY d.retry_at
                FOR UPDATE SKIP LOCKED LIMIT 1""")
            if not row: return
            task = await conn.fetchrow("""SELECT t.*, p.telegram_user_id,p.timezone FROM crm_personal_tasks t
                 JOIN crm_personal_profiles p ON p.id=t.owner_id WHERE t.id=$1 FOR UPDATE OF t""", row['task_id'])
            if not task or task['reminder_revision'] != row['revision'] or task['status'] in ('done','cancelled') or not task['reminders_enabled'] or not (task['reminder_at'] or task['due_at']):
                await conn.execute("UPDATE crm_personal_reminder_delivery SET status='cancelled',updated_at=now() WHERE task_id=$1 AND revision=$2", row['task_id'], row['revision'])
                continue
            tg = task['telegram_user_id']
            actor = await authorized(conn, SimpleNamespace(id=tg), SimpleNamespace(id=tg,type='private'))
            if not actor:
                await conn.execute("UPDATE crm_personal_reminder_delivery SET status='blocked',updated_at=now() WHERE task_id=$1 AND revision=$2", row['task_id'], row['revision'])
                continue
            await conn.execute("UPDATE crm_personal_reminder_delivery SET status='sending', attempts=attempts+1,updated_at=now() WHERE task_id=$1 AND revision=$2", row['task_id'], row['revision'])
        # Commit the claim first. Lock the task during the bounded send so a
        # completed/rescheduled task cannot race past the last validation.
        async with pool.acquire() as conn, conn.transaction():
            task = await conn.fetchrow("""SELECT t.*,p.telegram_user_id,p.timezone,
                coalesce(t.reminder_at, least(t.due_at,(date_trunc('day',t.due_at AT TIME ZONE p.timezone)+interval '9 hours') AT TIME ZONE p.timezone)) AS scheduled
                FROM crm_personal_tasks t JOIN crm_personal_profiles p ON p.id=t.owner_id
                WHERE t.id=$1 FOR UPDATE OF t""",row['task_id'])
            tg = task['telegram_user_id'] if task else None
            actor = await authorized(conn,SimpleNamespace(id=tg),SimpleNamespace(id=tg,type='private')) if tg else None
            due = await conn.fetchval('SELECT $1::timestamptz <= now()', task['scheduled']) if task and task['scheduled'] else False
            if not task or task['reminder_revision'] != row['revision'] or task['status'] in ('done','cancelled') or not task['reminders_enabled'] or not due or not actor:
                await conn.execute("UPDATE crm_personal_reminder_delivery SET status=$3,updated_at=now() WHERE task_id=$1 AND revision=$2 AND status='sending'",row['task_id'],row['revision'],'blocked' if not actor else 'cancelled')
                continue
            try:
                from zoneinfo import ZoneInfo
                when = task['scheduled'].astimezone(ZoneInfo(task['timezone'])).strftime('%d.%m.%Y %H:%M %Z')
                due_text = task['due_at'].astimezone(ZoneInfo(task['timezone'])).strftime('%d.%m.%Y %H:%M %Z') if task['due_at'] else None
                text = 'Напоминание: ' + task['title'][:300] + '\nНапоминаю в: ' + when
                if due_text: text += '\nСрок задачи: ' + due_text
                markup = keyboard([[('Открыть задачу', f"pt:open:{task['id']}:{task['version']}")]])
                await asyncio.wait_for(bot.send_message(tg, text, parse_mode=None, reply_markup=markup), timeout=20)
            except TelegramRetryAfter as exc:
                await conn.execute("""UPDATE crm_personal_reminder_delivery SET status=CASE WHEN attempts<4 THEN 'pending' ELSE 'blocked' END,
                    retry_at=now()+$3::interval,updated_at=now() WHERE task_id=$1 AND revision=$2 AND status='sending'""", row['task_id'], row['revision'], timedelta(seconds=max(exc.retry_after, 1)))
            except TelegramForbiddenError:
                await conn.execute("UPDATE crm_personal_reminder_delivery SET status='blocked',updated_at=now() WHERE task_id=$1 AND revision=$2 AND status='sending'", row['task_id'], row['revision'])
            except Exception:
                await conn.execute("UPDATE crm_personal_reminder_delivery SET status='uncertain',updated_at=now() WHERE task_id=$1 AND revision=$2 AND status='sending'", row['task_id'], row['revision'])
            else:
                await conn.execute("UPDATE crm_personal_reminder_delivery SET status='sent',updated_at=now() WHERE task_id=$1 AND revision=$2 AND status='sending'", row['task_id'], row['revision'])

async def run(pool, bot):
    announced = False
    while True:
        try:
            await tick(pool, bot)
            if not announced:
                log.info('Personal reminder loop started successfully')
                announced = True
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception('Personal reminder loop failed')
        await asyncio.sleep(20)
