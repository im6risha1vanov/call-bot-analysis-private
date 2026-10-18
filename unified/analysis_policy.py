"""Per-client durable selector; no Git/reset, trainer or course settings writes."""
from __future__ import annotations
import hashlib
import json
import types
from pathlib import Path
from tools import Forbidden, resolve_actor
from comparison.registry import verify, LEGACY
from .legacy_display import VERSION, METHOD

BACKUP = '/tmp/callbot-analysis-backup-20261007T085440Z'
LOCK = 'callbot-analysis-policy-v1'

def evaluation_parameters():
    import analysis
    return {'criteria':analysis.CRITERIA,
            'verdict':{'critical':sorted(analysis.LEVEL_CRITICAL_CRITERIA),
                       'failures':analysis.VERDICT_MIN_FAILURES,
                       'failures_with_signal':analysis.VERDICT_MIN_FAILURES_WITH_SIGNAL}}

def evaluate(scores, package):
    """Same original functions, with the parameters frozen at enqueue."""
    import analysis
    namespace=dict(analysis.compute_score.__globals__)
    namespace['CRITERIA']=package.get('criteria',analysis.CRITERIA)
    rules=package.get('verdict') or {}
    namespace.update(LEVEL_CRITICAL_CRITERIA=set(rules.get('critical',analysis.LEVEL_CRITICAL_CRITERIA)),
        VERDICT_MIN_FAILURES=rules.get('failures',analysis.VERDICT_MIN_FAILURES),
        VERDICT_MIN_FAILURES_WITH_SIGNAL=rules.get('failures_with_signal',analysis.VERDICT_MIN_FAILURES_WITH_SIGNAL))
    score,rows=types.FunctionType(analysis.compute_score.__code__,namespace)(scores)
    level=types.FunctionType(analysis.compute_level.__code__,namespace)(scores,rows)
    return score,level,rows

def verify_original():
    package=verify()[LEGACY]
    if package['source_commit']!='52e4d575c03788d09194346362b1e06ef4476feb':
        raise ValueError('Unknown original baseline')
    root=Path(__file__).resolve().parents[1]
    for name in ('analysis.py','prompt_score.md','prompt_short.md','prompt_review.md'):
        if hashlib.sha256((root/name).read_bytes()).hexdigest()!=package['files']['legacy/'+name]:
            raise ValueError('Original analysis package changed: '+name)
    return package

def current_configuration():
    from methodology import runtime
    from automatic.jobs import enabled
    from .render import FORMAT_VERSION
    return {'methodology_version':METHOD, 'display_version':FORMAT_VERSION,
            'isolate_primary':False, 'expect_course':runtime.shadow_enabled(),
            'expect_combined':enabled(), 'package_sha256':verify_original()['sha256'],
            **evaluation_parameters()}

def legacy_configuration():
    return {'methodology_version':METHOD,'display_version':VERSION,
            'isolate_primary':True,'expect_course':False,'expect_combined':False,
            'package_sha256':verify_original()['sha256'],**evaluation_parameters()}

async def installed(pool):
    return bool(await pool.fetchval("SELECT to_regclass('call_analysis_policy') IS NOT NULL"))

async def snapshot(pool, client_id):
    if not await installed(pool):return current_configuration()
    row=await pool.fetchrow('SELECT * FROM call_analysis_policy WHERE client_id=$1',client_id)
    if not row:return current_configuration()
    saved=json.loads(row['saved_configuration']) if isinstance(row['saved_configuration'],str) else row['saved_configuration']
    return legacy_configuration() if row['active']=='legacy' else dict(saved)

async def enqueue(pool, client_id, call_id, dedup_key):
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',LOCK)
        cfg=await snapshot(conn,client_id)
        await conn.execute("INSERT INTO tasks(type,client_id,input,dedup_key) VALUES('analyze_call',$1,$2::jsonb,$3) ON CONFLICT(type,dedup_key) DO NOTHING",
                           client_id,json.dumps({'call_id':call_id,'analysis_package':cfg}),dedup_key)

async def pin_task(pool, task, payload):
    if isinstance(payload.get('analysis_package'),dict):return payload['analysis_package']
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',LOCK)
        raw=await conn.fetchval('SELECT input FROM tasks WHERE id=$1 FOR UPDATE',task['id'])
        value=json.loads(raw) if isinstance(raw,str) else dict(raw)
        if 'analysis_package' not in value:
            value['analysis_package']=await snapshot(conn,task['client_id'])
            await conn.execute('UPDATE tasks SET input=$2::jsonb WHERE id=$1',task['id'],json.dumps(value))
        return value['analysis_package']

async def switch(pool, actor, target):
    if target not in {'legacy','saved_current'}:raise ValueError('Unknown analysis target')
    if actor is None:raise Forbidden('Доступ к переключению закрыт.')
    async with pool.acquire() as conn, conn.transaction():
        actor=await resolve_actor(conn,actor.telegram_user_id)
        if actor is None or actor.role not in {'owner','head'}:
            raise Forbidden('Переключение доступно владельцу и руководителю своего отдела.')
        if not await installed(conn):
            raise ValueError('Переключатель ещё не внедрён: требуется миграция 007 и загрузка кода служб.')
        verify_original()
        await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',LOCK)
        old=await snapshot(conn,actor.client_id)
        # Freeze unstamped queued jobs BEFORE changing the active selector.
        # New enqueues and first execution take this same lock.
        await conn.execute("UPDATE tasks SET input=jsonb_set(input::jsonb,'{analysis_package}',$2::jsonb) WHERE client_id=$1 AND type='analyze_call' AND status IN ('new','processing','awaiting_approval') AND NOT (input::jsonb ? 'analysis_package')",
                           actor.client_id,json.dumps(old))
        await conn.execute("INSERT INTO call_analysis_policy(client_id,active,saved_configuration,backup_path) VALUES($1,$2,$3::jsonb,$4) ON CONFLICT(client_id) DO UPDATE SET active=EXCLUDED.active,updated_at=now()",
                           actor.client_id,target,json.dumps(old),BACKUP)
        return await snapshot(conn,actor.client_id)

def buttons():
    from aiogram.types import InlineKeyboardButton,InlineKeyboardMarkup
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='↩️ Старая методика',callback_data='ap:legacy')],
        [InlineKeyboardButton(text='🔄 Вернуть сохранённую версию',callback_data='ap:saved_current')],
        [InlineKeyboardButton(text='ℹ️ Версия анализа',callback_data='ap:status')]])

def install(router, pool_getter):
    from aiogram import F
    from aiogram.filters import Command
    from .ui import private_action
    async def action(event, target):
        private_action(event)
        pool=pool_getter();actor=await resolve_actor(pool,event.from_user.id)
        if actor is None or actor.role not in {'owner','head'}:raise Forbidden('Доступ к управлению закрыт.')
        cfg=await snapshot(pool,actor.client_id) if target=='status' else await switch(pool,actor,target)
        return ('Версия анализа: '+cfg['methodology_version']+'\nОформление: '+cfg['display_version']+
                '\nОткат: проверенная исходная шкала до курса.\nПереключение действует только на новые задания вашего отдела.')
    @router.message(Command('analysis_status','analysis_rollback','analysis_restore'))
    async def command(message):
        target={'/analysis_status':'status','/analysis_rollback':'legacy','/analysis_restore':'saved_current'}[message.text.split()[0].split('@')[0]]
        try:
            text=await action(message,target)
            await message.answer(text,parse_mode=None,reply_markup=buttons())
        except (Forbidden,ValueError) as exc:await message.answer(str(exc),parse_mode=None)
        except Exception:await message.answer('Переключение не удалось. Версия не подтверждена; попробуйте позже.',parse_mode=None)
    @router.message(F.text.in_({'↩️ Старая методика','🔄 Вернуть сохранённую версию','ℹ️ Версия анализа'}))
    async def menu(message):
        target={'↩️ Старая методика':'legacy','🔄 Вернуть сохранённую версию':'saved_current','ℹ️ Версия анализа':'status'}[message.text]
        try:
            await message.answer(await action(message,target),parse_mode=None,reply_markup=buttons())
        except (Forbidden,ValueError) as exc:await message.answer(str(exc),parse_mode=None)
        except Exception:await message.answer('Переключение не удалось. Попробуйте позже.',parse_mode=None)
    @router.callback_query(F.data.startswith('ap:'))
    async def callback(cq):
        await cq.answer()
        try:
            target=cq.data[3:]
            if target not in {'legacy','saved_current','status'}:raise ValueError('Кнопка устарела.')
            text=await action(cq,target)
            await cq.bot.send_message(cq.from_user.id,text,parse_mode=None,reply_markup=buttons())
        except (Forbidden,ValueError) as exc:await cq.bot.send_message(cq.from_user.id,str(exc),parse_mode=None)
        except Exception:await cq.bot.send_message(cq.from_user.id,'Переключение не удалось. Попробуйте позже.',parse_mode=None)
