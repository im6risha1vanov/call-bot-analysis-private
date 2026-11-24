"""Фоновая работа кабинета робота внутри callbot-crm.

Каждые TICK_S секунд:
1. переносит новые итоги звонков из calls.jsonl робота в robot_calls и отмечает номера кампаний
   (не дозвонился — повтор через retry_minutes, пока не кончатся попытки; «не звонить» — в стоп-лист);
2. если робот свободен, заказывает следующий номер одной из идущих кампаний, у которой сейчас
   рабочее время по её дням недели, часам и часовому поясу. Линия у робота одна: звонок за звонком.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import httpx

from . import robot, robot_store

log = logging.getLogger('crm.robot')
TICK_S = 15
HOLD_MIN = 15  # заказанный номер не берём снова, пока не придёт итог (или 15 минут, если итог потерялся)
DOWN_MIN = 5  # робот не смог позвонить из-за Asterisk или связи: номер ждёт столько и пробует снова


def transient(status: int, error: str) -> bool:
    """Сбой не из-за номера: Asterisk, связь, перегрузка. Такой номер не закрываем, а повторяем позже.
    Старый робот отвечал 400 и на сбой Asterisk, поэтому смотрим ещё на текст ошибки."""
    return status >= 500 or 'asterisk' in error.lower()


def record_id(rec: dict) -> str:
    return str(rec.get('call_id') or 'log-' + hashlib.sha1(f"{rec.get('at')}|{rec.get('phone')}".encode()).hexdigest()[:20])


async def ingest(pool) -> int:
    """Новые строки calls.jsonl → robot_calls. Позицию в файле храним в robot_options."""
    path = robot.CALLS_LOG
    if not path.is_file():
        return 0
    pos = await robot_store.get_option(pool, 'ingest', {'offset': 0})
    offset = int(pos.get('offset') or 0)
    size = path.stat().st_size
    if size < offset:  # файл начали заново
        offset = 0
    if size == offset:
        return 0

    def read() -> tuple[list[str], int]:
        with path.open('rb') as f:
            f.seek(offset)
            chunk = f.read(4_000_000)
        end = chunk.rfind(b'\n') + 1  # недописанную строку оставляем на следующий раз
        return chunk[:end].decode('utf-8', 'replace').splitlines(), offset + end
    lines, new_offset = await asyncio.to_thread(read)
    count = 0
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get('mode') != 'телефон':
            continue
        await store_call(pool, rec)
        count += 1
    await robot_store.set_option(pool, 'ingest', {'offset': new_offset})
    return count


async def store_call(pool, rec: dict) -> None:
    try:
        at = datetime.fromisoformat(str(rec.get('at')))
    except ValueError:
        at = datetime.now(timezone.utc)
    campaign_id = rec.get('campaign_id') if isinstance(rec.get('campaign_id'), int) else None
    tokens = int(rec.get('cost') or 0) if rec.get('cost_unit') == 'ток.' else 0
    outcome = str(rec.get('outcome') or '')
    inserted = await pool.fetchval(
        """INSERT INTO robot_calls(call_id,owner,campaign_id,phone,operator,at,outcome,comment,duration_s,llm_tokens,tts_chars,recording,data)
           VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb) ON CONFLICT (call_id) DO NOTHING RETURNING call_id""",
        record_id(rec), str(rec.get('owner') or ''), campaign_id, str(rec.get('phone') or ''), str(rec.get('operator') or ''),
        at, outcome, str(rec.get('comment') or '')[:2000], int(rec.get('duration_s') or 0), tokens, int(rec.get('tts_chars') or 0),
        rec.get('recording') or None,
        json.dumps({'facts': rec.get('facts') or {}, 'transcript': rec.get('transcript') or [], 'transfer': bool(rec.get('transfer')),
                    'error': rec.get('error')}, ensure_ascii=False))
    if not inserted:
        return
    owner = str(rec.get('owner') or '')
    if outcome == 'do_not_call' and rec.get('phone'):
        await pool.execute("INSERT INTO robot_stoplist(owner,phone,reason) VALUES($1,$2,'просил не звонить (робот)') ON CONFLICT DO NOTHING",
                           owner, str(rec['phone']))
    target_id = rec.get('target_id')
    if not isinstance(target_id, int) or campaign_id is None:
        return
    row = await pool.fetchrow(
        """SELECT t.attempts, c.attempts AS max_attempts, c.retry_minutes FROM robot_targets t JOIN robot_campaigns c ON c.id=t.campaign_id
           WHERE t.id=$1 AND c.id=$2""", target_id, campaign_id)
    if row is None:
        return
    again = outcome == 'ndz' and row['attempts'] < row['max_attempts']
    await pool.execute('UPDATE robot_targets SET outcome=$2, done=$3, next_at=$4 WHERE id=$1', target_id, outcome, not again,
                       datetime.now(timezone.utc) + timedelta(minutes=row['retry_minutes']) if again else None)
    if not await pool.fetchval('SELECT 1 FROM robot_targets WHERE campaign_id=$1 AND NOT done', campaign_id):
        await pool.execute("UPDATE robot_campaigns SET status='done' WHERE id=$1 AND status='running'", campaign_id)


async def robot_busy() -> bool | None:
    """True — робот на линии или ждёт ответа, None — робот выключен."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f'http://127.0.0.1:{robot.api_port()}/status')
        data = r.json()
    except (httpx.HTTPError, ValueError):
        return None
    return bool(data.get('active') or data.get('pending'))


async def tick(pool, now: datetime | None = None) -> dict | None:
    """Один шаг расписания: заказать звонок следующему номеру. Возвращает заказанный номер."""
    now = now or datetime.now(timezone.utc)
    running = await pool.fetch("SELECT * FROM robot_campaigns WHERE status='running'")
    if not running:
        return None
    clients = set(await robot_store.enabled_clients(pool))
    live = [c['id'] for c in running if (not c['owner'] or c['owner'] in clients) and robot_store.in_window(c, now)]
    if not live:
        return None
    # номера из стоп-листа закрываем, не звоня
    await pool.execute(
        """UPDATE robot_targets t SET done=true, outcome='stop' FROM robot_campaigns c
           WHERE c.id=t.campaign_id AND t.campaign_id=ANY($1) AND NOT t.done
             AND EXISTS (SELECT 1 FROM robot_stoplist s WHERE s.owner=c.owner AND s.phone=t.phone)""", live)
    # попытки кончились, а итог последней так и не пришёл (робот перезапустили посреди звонка): не звоним по кругу
    await pool.execute(
        """UPDATE robot_targets t SET done=true, outcome='error' FROM robot_campaigns c
           WHERE c.id=t.campaign_id AND t.campaign_id=ANY($1) AND NOT t.done AND t.attempts>=c.attempts
             AND t.next_at<=$2""", live, now)
    target = await pool.fetchrow(
        """SELECT t.id, t.phone, c.id AS campaign_id, c.owner, c.operator FROM robot_targets t JOIN robot_campaigns c ON c.id=t.campaign_id
           WHERE t.campaign_id=ANY($1) AND NOT t.done AND (t.next_at IS NULL OR t.next_at<=$2)
           ORDER BY t.next_at NULLS FIRST, t.id LIMIT 1""", live, now)
    if target is None:
        for cid in live:
            if not await pool.fetchval('SELECT 1 FROM robot_targets WHERE campaign_id=$1 AND NOT done', cid):
                await pool.execute("UPDATE robot_campaigns SET status='done' WHERE id=$1", cid)
        return None
    busy = await robot_busy()
    if busy is None or busy:
        return None
    meta = {'phone': target['phone'], 'operator': target['operator'], 'campaign_id': target['campaign_id'],
            'owner': target['owner'], 'target_id': target['id']}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(f'http://127.0.0.1:{robot.api_port()}/dial', json=meta)
    except httpx.HTTPError:
        return None
    data = r.json() if r.headers.get('content-type', '').startswith('application/json') else {}
    if r.status_code >= 300:
        error = str(data.get('error') or r.status_code)
        log.warning('робот не позвонил %s: %s', target['phone'], error)
        if transient(r.status_code, error):  # Asterisk лежит: номер не теряем, попробуем позже
            await pool.execute('UPDATE robot_targets SET next_at=$2 WHERE id=$1', target['id'], now + timedelta(minutes=DOWN_MIN))
        else:  # номер или оператор не годятся: закрываем номер, чтобы не звонить по кругу
            await pool.execute("UPDATE robot_targets SET done=true, outcome='error' WHERE id=$1", target['id'])
        return None
    await pool.execute('UPDATE robot_targets SET attempts=attempts+1, last_at=$2, next_at=$3, call_id=$4 WHERE id=$1',
                       target['id'], now, now + timedelta(minutes=HOLD_MIN), data.get('id'))
    log.info('кампания %s: робот звонит %s', target['campaign_id'], target['phone'])
    return meta


async def loop(pool) -> None:
    while True:
        try:
            await ingest(pool)
            await tick(pool)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception('кабинет робота: сбой шага')
        await asyncio.sleep(TICK_S)


def enabled() -> bool:
    return os.getenv('ROBOT_RUNNER', '1') != '0' and robot.DATA.is_dir()
