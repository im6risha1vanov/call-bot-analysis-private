"""Сделки: клиент от холодного звонка до оплаты, где и почему отвалился.

Сделка — номер клиента (10 последних цифр). Её ведёт тот же менеджер, что звонил.
Этапы (воронка Григория от 20.10):
1. База номеров — номер загружен в CRM (crm_leads); считается только в воронке;
2. Холодный звонок — разобранный разговор; результат — встреча, перезвон на дату, КП, ЛПР;
3. Презентация — разобранная запись из реестра (review/presentations.py) или отметка;
4. Telegram или почта — переписка рабочего Telegram, привязанная к клиенту (review/tgreview.py),
   или отметка «отправил материалы» (этап можно пропустить);
5. Договор — отправлен, правки, подписан;
6. Оплата — отмечает только директор.

Где сделка встала, CRM понимает сама: последний разговор без результата — отказ на
холодном звонке с причиной из разбора; клиент, остывший до отказа на презентации, —
отказ на презентации (или в переписке). Люди отмечают поздние этапы и отказ с причиной из списка.
Ничего не считается моделью заново: всё из сохранённых разборов и событий.
"""
from __future__ import annotations

import collections
import json
import re
from datetime import date, datetime, timedelta, timezone

from review import engine, kitchen as review_kitchen
from review.render import CATEGORY_LABELS, OUTCOME_LABELS
from tools import Forbidden, home_client_id, is_privileged
from . import store

STAGES = [
    ('base', 'База номеров'), ('cold', 'Холодный звонок'), ('presentation', 'Презентация'),
    ('messenger', 'Telegram или почта'), ('contract', 'Договор'), ('paid', 'Оплата')]
STAGE_INDEX = {k: i for i, (k, _) in enumerate(STAGES)}
LOST_REASONS = {
    'expensive': 'дорого', 'no_budget': 'нет бюджета', 'competitor': 'выбрали другого подрядчика',
    'no_need': 'нет потребности', 'not_dmp': 'не дошли до ЛПР', 'silent': 'пропал, не отвечает',
    'terms': 'не устроили условия договора', 'no_trust': 'не поверили в результат', 'other': 'другое'}
PRESENTATION_LOSS = {
    'no_discovery': 'не выяснили ситуацию клиента', 'generic_pitch': 'рассказ не про бизнес клиента',
    'price_no_value': 'цена без расчёта пользы', 'objection_unhandled': 'не снято возражение',
    'no_trust': 'не дали доказательств', 'no_dmp': 'не вышли на ЛПР', 'no_next_step': 'нет шага с днём',
    'pushy': 'давили на клиента', 'unanswered_question': 'вопрос без ответа', 'other': 'другое'}
# Где клиент остыл в переписке: те же причины, что на презентации, и свои для мессенджера.
CHAT_LOSS = {**PRESENTATION_LOSS, 'slow_reply': 'долго не отвечали клиенту', 'no_followup': 'клиент пропал, не напомнили о себе',
             'materials_no_talk': 'материалы без договорённости обсудить'}
EVENT_LABELS = {
    'presentation': 'Презентация проведена', 'materials': 'Отправлены материалы', 'contract_sent': 'Договор отправлен',
    'contract_edits': 'Клиент прислал правки', 'contract_signed': 'Договор подписан', 'paid': 'Оплата получена',
    'lost': 'Отказ', 'reopen': 'Снова в работе', 'note': 'Заметка'}
EVENT_STAGE = {'presentation': 'presentation', 'materials': 'messenger', 'contract_sent': 'contract',
               'contract_edits': 'contract', 'contract_signed': 'contract', 'paid': 'paid'}
MANAGER_EVENTS = {'presentation', 'materials', 'contract_sent', 'contract_edits', 'contract_signed', 'lost', 'reopen', 'note'}
CONTRACT_FIELDS = {'legal_name': 'юрлицо', 'inn': 'ИНН', 'signer': 'кто подписывает', 'service': 'услуга',
                   'package': 'пакет', 'competitors': 'конкуренты', 'region': 'регион', 'start': 'старт',
                   'delivery': 'куда отдавать номера'}
# Сколько дней без движения на этапе, после чего сделка считается зависшей.
STALL_DAYS = {'cold': 7, 'presentation': 5, 'messenger': 5, 'contract': 7}
CRM_LOST = {'not_interested': 'нет потребности'}  # итог «не интересно» из CRM менеджера


def key(number) -> str | None:
    digits = re.sub(r'\D', '', str(number or ''))
    return digits[-10:] if len(digits) >= 10 else None


def loads(value):
    return json.loads(value) if isinstance(value, str) else (value or {})


def iso(value: datetime | None, tz) -> str | None:
    return value.astimezone(tz).strftime('%Y-%m-%dT%H:%M') if value else None


def show_time(day: date, time_text: str | None, tz) -> str:
    """Время презентации по реестру (там UTC) в поясе клиента; без времени — середина дня."""
    from review.presentations import registry_time
    at = registry_time(day, time_text)
    return iso(at, tz) if at else f'{day:%Y-%m-%d}T12:00'


async def has_table(pool, name: str) -> bool:
    return bool(await pool.fetchval('SELECT to_regclass($1) IS NOT NULL', name))


async def load(pool, client_id: int, ext: str | None = None) -> dict:
    """Всё, из чего складываются сделки, одним проходом по базе."""
    calls = await pool.fetch(
        """SELECT c.id, c.client_number, c.extension, c.call_started_at, c.duration_seconds,
                  r.success, r.chance, r.category, r.data
           FROM call_reviews r JOIN calls c ON c.id=r.call_id
           WHERE r.client_id=$1 AND r.kind = ANY($3::text[]) AND c.client_number IS NOT NULL
             AND ($2::text IS NULL OR c.extension=$2)
           ORDER BY c.call_started_at""", client_id, ext, sorted(engine.REVIEWED))
    shows = await pool.fetch(
        """SELECT p.id, p.day, p.time_text, p.file_id, p.extension, p.data, c.call_started_at,
                  coalesce(c.client_number, p.phone) AS client_number, p.match->>'by' AS match_by
           FROM presentations p LEFT JOIN calls c ON c.id=p.call_id
           WHERE p.client_id=$1 AND p.status='reviewed' AND coalesce(c.client_number, p.phone) IS NOT NULL
           ORDER BY p.day, p.time_text""", client_id) \
        if await has_table(pool, 'presentations') else []
    events = await pool.fetch('SELECT * FROM deal_events WHERE client_id=$1 ORDER BY created_at, id', client_id) \
        if await has_table(pool, 'deal_events') else []
    tasks = await pool.fetch(
        """SELECT client_number, kind, due_date, due_time, raw FROM callback_tasks
           WHERE client_id=$1 AND status='open' ORDER BY due_date""", client_id)
    outcomes = await pool.fetch(
        """SELECT number, extension, outcome, comment, created_at FROM crm_outcomes WHERE client_id=$1 ORDER BY created_at""",
        client_id) if await has_table(pool, 'crm_outcomes') else []
    # Отметки старой страницы «Воронка» (crm_docs) есть только у нашей компании.
    legacy = await pool.fetch("SELECT path, data, updated_at FROM crm_docs WHERE path LIKE 'pipe/%'") \
        if await has_table(pool, 'crm_docs') and client_id == await home_client_id(pool) else []
    base = await pool.fetch('SELECT number, extension, created_at FROM crm_leads WHERE client_id=$1', client_id) \
        if await has_table(pool, 'crm_leads') else []
    # Переписка рабочего Telegram, где известен номер клиента. «Не клиент» по разбору не берём,
    # если только человек сам не привязал чат.
    chats = await pool.fetch(
        """SELECT c.peer_id, c.title, c.username, c.phone, c.extension, c.analysis, c.last_message_at,
                  s.messages, s.first_at, s.last_in, s.last_out
           FROM tg_chats c
           JOIN LATERAL (SELECT count(*) AS messages, min(m.sent_at) AS first_at,
                                max(m.sent_at) FILTER (WHERE NOT m.outgoing) AS last_in,
                                max(m.sent_at) FILTER (WHERE m.outgoing) AS last_out
                         FROM tg_messages m WHERE m.client_id=c.client_id AND m.peer_id=c.peer_id) s ON true
           WHERE c.client_id=$1 AND c.phone IS NOT NULL AND s.messages > 0
             AND (c.analysis->>'kind' IS DISTINCT FROM 'not_client' OR c.analysis->'link'->>'by' = 'manual')
           ORDER BY c.last_message_at""", client_id) if await has_table(pool, 'tg_chats') else []
    names = {r['extension']: r['full_name'] for r in await pool.fetch(
        "SELECT extension, full_name FROM employees WHERE client_id=$1 AND role='manager'", client_id)}
    return {'calls': calls, 'shows': shows, 'events': events, 'tasks': tasks, 'outcomes': outcomes,
            'legacy': legacy, 'base': base, 'names': names, 'chats': chats}


def legacy_events(rows) -> list[dict]:
    """Отметки со старой страницы «Воронка продаж» (crm_docs pipe/<номер>) как события."""
    out = []
    kinds = {'contract': 'contract_sent', 'paid': 'paid', 'lost': 'lost'}
    for r in rows:
        data = loads(r['data'])
        kind = kinds.get(data.get('stage'))
        phone = key(r['path'].split('/', 1)[1])
        if kind and phone:
            out.append({'id': None, 'phone': phone, 'kind': kind, 'reason': 'other' if kind == 'lost' else None,
                        'note': data.get('note') or None, 'amount': data.get('sum') or None, 'extension': None,
                        'author_name': 'старая воронка', 'created_at': r['updated_at']})
    return out


def build(raw: dict, tz, now: datetime) -> list[dict]:
    """Сделки из звонков, презентаций и событий. Порядок: свежие сверху."""
    by = collections.defaultdict(lambda: {'calls': [], 'shows': [], 'events': [], 'outcomes': [], 'chats': []})
    for r in raw['calls']:
        if review_kitchen.handles(r['extension']):
            continue
        k = key(r['client_number'])
        if k:
            by[k]['calls'].append(r)
    for r in raw['shows']:
        k = key(r['client_number'])
        if k:
            by[k]['shows'].append(r)
    for r in raw.get('chats', []):
        k = key(r['phone'])
        if k:
            by[k]['chats'].append(r)
    for e in [*map(dict, raw['events']), *legacy_events(raw['legacy'])]:
        if e['phone'] in by or e['kind'] != 'note':
            by[e['phone']]['events'].append(e)
    for r in raw['outcomes']:
        k = key(r['number'])
        if k in by:
            by[k]['outcomes'].append(r)
    tasks = {}
    for t in raw['tasks']:
        k = key(t['client_number'])
        if k and k not in tasks:
            tasks[k] = t
    base = {key(r['number']) for r in raw['base']}
    deals = [deal(k, v, tasks.get(k), k in base, raw['names'], tz, now) for k, v in by.items()]
    deals = [d for d in deals if d]
    deals.sort(key=lambda d: d['last_at'] or '', reverse=True)
    return deals


def deal(k: str, src: dict, task, in_base: bool, names: dict, tz, now: datetime) -> dict | None:
    calls, shows, chats = src['calls'], src['shows'], src.get('chats', [])
    events = sorted(src['events'], key=lambda e: e['created_at'])
    if not (calls or shows or events or chats):
        return None
    timeline = []
    company, number, ext = None, None, None
    cold_ok = False
    reached = 'cold' if calls else 'base'
    for r in calls:
        data = loads(r['data'])
        company = (data.get('client') or {}).get('company') or company
        number, ext = r['client_number'], r['extension']
        cold_ok = cold_ok or bool(r['success'])
        moment = data.get('key_moment') or {}
        timeline.append({'at': iso(r['call_started_at'], tz), 'type': 'call', 'id': r['id'], 'ok': bool(r['success']),
                         'title': 'Звонок' + (': ' + ', '.join(OUTCOME_LABELS.get(o, o) for o in data.get('outcome') or [])
                                             if r['success'] else ''),
                         'text': data.get('rop_line'), 'lost': CATEGORY_LABELS.get(r['category']) if not r['success'] else None,
                         'say': moment.get('say_instead')})
    for r in src['outcomes']:
        cold_ok = cold_ok or r['outcome'] in ('meeting', 'proposal', 'callback', 'interested')
    reviews = []  # (когда, что, разбор) презентаций и переписок: по самому свежему — совет и температура
    for r in shows:
        data = loads(r['data'])
        contract = data.get('contract') or {}
        company = contract.get('company') or company
        number, ext = r['client_number'], r['extension'] or ext
        reached = 'presentation'
        moment = data.get('key_moment') or {}
        # Звонок Mango — сама презентация; иначе (по смыслу, по телефону) время берём из реестра.
        at = (show_time(r['day'], r['time_text'], tz) if r['day'] and (r['match_by'] in ('context', 'manual', 'registry')
                                                                      or not r['call_started_at'])
              else iso(r['call_started_at'], tz))
        timeline.append({'at': at, 'type': 'presentation', 'id': r['id'],
                         'title': 'Презентация', 'text': data.get('rop_line'), 'temperature': data.get('temperature'),
                         'lost': PRESENTATION_LOSS.get(moment.get('category')) if moment else None,
                         'say': moment.get('say_instead'), 'file_id': r['file_id']})
        reviews.append((at, 'presentation', data))
    waiting = None  # клиент написал в Telegram последним: когда
    for r in chats:
        data = loads(r['analysis'])
        reviewed = bool(data.get('reviewed'))
        moment = (data.get('key_moment') or {}) if reviewed else {}
        company = company or ((data.get('contract') or {}).get('company') if reviewed else None)
        number, ext = number or r['phone'], ext or r['extension']
        if STAGE_INDEX[reached] < STAGE_INDEX['messenger']:
            reached = 'messenger'
        at = iso(r['last_message_at'], tz)
        if r['last_in'] and (not r['last_out'] or r['last_in'] > r['last_out']):
            waiting = max(waiting or '', iso(r['last_in'], tz))
        timeline.append({'at': at, 'type': 'chat', 'id': r['peer_id'],
                         'title': 'Переписка в Telegram', 'messages': r['messages'],
                         'since': iso(r['first_at'], tz), 'chat': r['title'] or (f"@{r['username']}" if r['username'] else None),
                         'text': data.get('rop_line') if reviewed else None,
                         'temperature': data.get('temperature') if reviewed else None,
                         'lost': CHAT_LOSS.get(moment.get('category')) if moment else None, 'say': moment.get('say_instead'),
                         'linked': (data.get('link') or {}).get('by')})
        if reviewed:
            reviews.append((at, 'chat', data))
    latest = max(reviews, key=lambda x: x[0] or '') if reviews else None
    last_review = latest[2] if latest else None
    status, lost = 'active', None
    manual_stage = None
    amount = None
    for e in events:
        at = e['created_at']
        timeline.append({'at': iso(at, tz), 'type': 'event', 'id': e['id'], 'kind': e['kind'],
                         'title': EVENT_LABELS.get(e['kind'], e['kind']) + (f" · {LOST_REASONS.get(e['reason'], e['reason'])}"
                                                                           if e['kind'] == 'lost' and e['reason'] else ''),
                         'text': e['note'], 'amount': float(e['amount']) if e['amount'] is not None else None,
                         'by': e.get('author_name')})
        ext = ext or e.get('extension')
        if e['kind'] in EVENT_STAGE:
            stage = EVENT_STAGE[e['kind']]
            if manual_stage is None or STAGE_INDEX[stage] >= STAGE_INDEX[manual_stage]:
                manual_stage = stage
            if e['kind'] == 'paid':
                amount = float(e['amount']) if e['amount'] is not None else amount
            status, lost = ('paid' if e['kind'] == 'paid' else 'active'), None
        elif e['kind'] == 'lost':
            status = 'lost'
            lost = {'reason': LOST_REASONS.get(e['reason'], e['reason'] or 'другое'), 'note': e['note'], 'auto': False,
                    'at': iso(at, tz)}
        elif e['kind'] == 'reopen':
            status, lost = 'active', None
    if manual_stage and STAGE_INDEX[manual_stage] > STAGE_INDEX[reached]:
        reached = manual_stage
    if status == 'paid':
        reached = 'paid'
    timeline.sort(key=lambda x: x['at'] or '')
    last = timeline[-1] if timeline else None
    last_at = last['at'] if last else None

    # Отказ, который CRM видит сама: только если люди ничего не отметили позже.
    later_marks = any(e['kind'] in EVENT_STAGE or e['kind'] in ('lost', 'reopen') for e in events)
    if status == 'active' and not later_marks:
        if reached == 'cold' and calls and not task:
            last_call = calls[-1]
            no_result = not last_call['success'] and not any(
                o['outcome'] in ('meeting', 'proposal', 'callback', 'interested') and o['created_at'] > last_call['call_started_at']
                for o in src['outcomes'])
            refused = next((o for o in reversed(src['outcomes']) if o['outcome'] in CRM_LOST), None)
            if refused and refused['created_at'] >= last_call['call_started_at']:
                status, lost = 'lost', {'reason': CRM_LOST[refused['outcome']], 'note': refused['comment'], 'auto': True,
                                        'at': iso(refused['created_at'], tz)}
            elif no_result:
                status = 'lost'
                lost = {'reason': CATEGORY_LABELS.get(last_call['category']) or ('шанса не было' if last_call['chance'] == 'none' else 'без результата'),
                        'note': None, 'auto': True,
                        'at': iso(last_call['call_started_at'], tz),
                        'chance': last_call['chance']}
        elif reached in ('presentation', 'messenger') and last_review and last_review.get('temperature') == 'lost':
            moment = last_review.get('key_moment') or {}
            status = 'lost'
            lost = {'reason': CHAT_LOSS.get(moment.get('category')) or
                    ('отказ в переписке' if latest[1] == 'chat' else 'отказ на презентации'),
                    'note': moment.get('what_happened'), 'auto': True, 'at': last_at}

    # Следующий шаг и совет.
    advice, due = None, None
    if task:
        due = task['due_date'].isoformat()
        advice = f"{'Встреча' if task['kind'] == 'meeting' else 'Перезвон'} {task['due_date']:%d.%m}" + \
                 (f" в {task['due_time']:%H:%M}" if task['due_time'] else '') + (f": {task['raw']}" if task['raw'] else '')
    missing = []
    if last_review:
        missing = [CONTRACT_FIELDS[f] for f in (last_review.get('contract') or {}).get('missing') or [] if f in CONTRACT_FIELDS]
        advice = last_review.get('next_step_advice') or advice
    if status == 'active' and not advice:
        if reached == 'contract':
            advice = 'Узнать, когда клиент подпишет договор, и что мешает.'
        elif reached == 'messenger':
            advice = 'Созвониться и обсудить материалы, назначить день решения.'
        elif reached == 'presentation':
            advice = 'Договориться о дне решения и отправить договор.'
        elif cold_ok:
            advice = 'Провести презентацию: позвонить и договориться о времени.'
    stalled_days = None
    if status == 'active' and last_at:
        idle = (now.astimezone(tz).date() - date.fromisoformat(last_at[:10])).days
        limit = STALL_DAYS.get(reached)
        overdue = due and due < now.astimezone(tz).date().isoformat()
        if overdue or (limit and idle > limit):
            stalled_days = idle
    return {
        'phone': k, 'number': number or k, 'company': company, 'ext': ext, 'manager': names.get(ext) or (f'Доб. {ext}' if ext else ''),
        'stage': reached, 'stage_name': dict(STAGES)[reached], 'stage_no': STAGE_INDEX[reached] + 1,
        'in_base': in_base, 'cold_ok': cold_ok, 'status': status, 'lost': lost, 'amount': amount,
        'first_at': timeline[0]['at'] if timeline else None, 'last_at': last_at, 'due': due,
        'advice': advice if status == 'active' else None, 'missing': missing, 'stalled_days': stalled_days,
        'temperature': (last_review or {}).get('temperature'), 'timeline': timeline,
        'waiting': waiting if status == 'active' and waiting and waiting >= (last_at or '') else None,
    }


def funnel(deals: list[dict], base_count: int) -> dict:
    """Сколько клиентов дошли до каждого этапа и где отвалились (с причинами)."""
    reached = {k: 0 for k, _ in STAGES}
    reached['base'] = base_count
    for d in deals:
        i = STAGE_INDEX[d['stage']]
        for k, _ in STAGES[1:i + 1]:
            reached[k] += 1
    lost = collections.defaultdict(collections.Counter)
    for d in deals:
        if d['status'] == 'lost':
            lost[d['stage']][d['lost']['reason']] += 1
    cold_results = sum(1 for d in deals if d['cold_ok'])
    return {
        'stages': [{'key': k, 'name': n, 'reached': reached[k],
                    'lost': sum(lost[k].values()), 'reasons': lost[k].most_common(5)} for k, n in STAGES],
        'cold_results': cold_results,
        'active': sum(1 for d in deals if d['status'] == 'active'),
        'stalled': sum(1 for d in deals if d['stalled_days'] is not None),
        'paid': sum(1 for d in deals if d['status'] == 'paid'),
        'paid_sum': sum(d['amount'] or 0 for d in deals if d['status'] == 'paid'),
    }


async def listing(pool, actor, ext: str | None = None, days: int | None = None, now: datetime | None = None) -> dict:
    """Сделки для страницы. Менеджер видит только свои; директор — всех или одного менеджера."""
    now = now or datetime.now(timezone.utc)
    if not is_privileged(actor):
        ext = actor.extension
    tz = await store.zone(pool, actor.client_id)
    raw = await load(pool, actor.client_id)
    deals = build(raw, tz, now)
    if ext:
        deals = [d for d in deals if d['ext'] == ext]
    if days:
        since = (now - timedelta(days=days)).astimezone(tz).strftime('%Y-%m-%dT%H:%M')
        deals = [d for d in deals if (d['first_at'] or '') >= since]
    base = len({key(r['number']) for r in raw['base'] if (not ext or r['extension'] == ext)
                and (not days or r['created_at'] >= now - timedelta(days=days))} - {None})
    managers = sorted({(d['ext'], d['manager']) for d in build(raw, tz, now) if d['ext']}, key=lambda x: x[1])
    return {'deals': [{**d, 'timeline': None} for d in deals], 'funnel': funnel(deals, base),
            'unlinked': await unlinked(pool, actor, ext), 'unlinked_chats': await unlinked_chats(pool, actor, ext),
            'stages': [{'key': k, 'name': n} for k, n in STAGES], 'reasons': LOST_REASONS,
            'managers': [{'ext': e, 'name': n} for e, n in managers],
            'can_pay': can_pay(actor), 'today': now.astimezone(tz).date().isoformat()}


async def one(pool, actor, phone: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    k = key(phone)
    if not k:
        raise ValueError('Неверный номер.')
    tz = await store.zone(pool, actor.client_id)
    found = next((d for d in build(await load(pool, actor.client_id), tz, now) if d['phone'] == k), None)
    if not found:
        raise ValueError('Такой сделки нет.')
    if not is_privileged(actor) and found['ext'] != actor.extension:
        raise Forbidden('Это сделка другого менеджера.')
    return {**found, 'reasons': LOST_REASONS, 'events': EVENT_LABELS, 'can_pay': can_pay(actor)}


async def unlinked(pool, actor, ext: str | None = None) -> list[dict]:
    """Разобранные презентации без клиента: звонка в Mango нет, телефона в реестре нет. Их привязывают вручную."""
    if not await has_table(pool, 'presentations'):
        return []
    if not is_privileged(actor):
        ext = actor.extension
    tz = await store.zone(pool, actor.client_id)
    rows = await pool.fetch(
        """SELECT p.id, p.day, p.time_text, p.extension, p.manager_name, p.file_id, p.client_contact, p.data,
                  (SELECT full_name FROM employees e WHERE e.client_id=p.client_id AND e.extension=p.extension
                     AND e.role='manager' LIMIT 1) AS manager
           FROM presentations p
           WHERE p.client_id=$1 AND p.status='reviewed' AND p.call_id IS NULL AND p.phone IS NULL
             AND ($2::text IS NULL OR p.extension=$2)
           ORDER BY p.day DESC NULLS LAST, p.time_text DESC""", actor.client_id, ext)
    out = []
    for r in rows:
        data = loads(r['data'])
        moment = data.get('key_moment') or {}
        out.append({'id': r['id'], 'at': show_time(r['day'], r['time_text'], tz) if r['day'] else None,
                    'manager': r['manager'] or r['manager_name'], 'company': (data.get('contract') or {}).get('company'),
                    'contact': r['client_contact'], 'summary': data.get('rop_line') or moment.get('what_happened'),
                    'next': data.get('next_step_advice'), 'file_id': r['file_id']})
    return out


async def link_presentation(pool, actor, presentation_id: int, phone: str, name: str | None) -> dict:
    """«Привязать к клиенту»: номер клиента для презентации, которая шла не через Mango."""
    from review.presentations import call_by_phone, phone_digits
    digits = phone_digits(phone)
    if not digits:
        raise ValueError('Введите номер клиента, например +7 999 123-45-67.')
    row = await pool.fetchrow('SELECT id, extension, day FROM presentations WHERE id=$1 AND client_id=$2',
                              presentation_id, actor.client_id)
    if not row:
        raise ValueError('Такой презентации нет.')
    if not is_privileged(actor) and row['extension'] != actor.extension:
        raise Forbidden('Это презентация другого менеджера.')
    tz = await store.zone(pool, actor.client_id)
    call_id = await call_by_phone(pool, actor.client_id, digits, row['day'], tz)
    await pool.execute(
        """UPDATE presentations SET phone=$2, call_id=$3, linked_by=$4, linked_at=now(), updated_at=now(),
             match=coalesce(match, '{}'::jsonb) || jsonb_build_object('by', 'manual', 'phone', $2::text)
           WHERE id=$1""", presentation_id, digits, call_id, name or 'CRM')
    return {'phone': key(digits), 'call_found': call_id is not None}


async def unlinked_chats(pool, actor, ext: str | None = None) -> list[dict]:
    """Переписки Telegram без номера клиента. Чаты через Telegram Business ничьи (общий рабочий аккаунт),
    поэтому их видят директор и руководитель; менеджер — только чаты из своей папки (Telethon)."""
    if not await has_table(pool, 'tg_chats'):
        return []
    if not is_privileged(actor):
        ext = actor.extension
    tz = await store.zone(pool, actor.client_id)
    rows = await pool.fetch(
        """SELECT c.peer_id, c.title, c.username, c.extension, c.analysis, c.last_message_at, s.messages, s.texts
           FROM tg_chats c
           JOIN LATERAL (SELECT count(*) AS messages,
                                array_agg(m.text ORDER BY m.sent_at) FILTER (WHERE NOT m.outgoing AND m.text IS NOT NULL) AS texts
                         FROM tg_messages m WHERE m.client_id=c.client_id AND m.peer_id=c.peer_id) s ON true
           WHERE c.client_id=$1 AND c.phone IS NULL AND c.kind='user' AND s.messages > 0
             AND c.analysis->>'kind' IS DISTINCT FROM 'not_client'
             AND ($2::text IS NULL OR c.extension=$2 OR c.extension IS NULL AND $3)
           ORDER BY c.last_message_at DESC NULLS LAST LIMIT 200""", actor.client_id, ext, is_privileged(actor))
    from review.tgreview import phones_in
    out = []
    for r in rows:
        data = loads(r['analysis'])
        texts = r['texts'] or []
        out.append({'peer_id': r['peer_id'], 'title': r['title'] or (f"@{r['username']}" if r['username'] else 'Без имени'),
                    'username': r['username'], 'at': iso(r['last_message_at'], tz), 'messages': r['messages'],
                    'company': (data.get('contract') or {}).get('company'), 'summary': data.get('rop_line'),
                    'last': (texts[-1][:200] if texts else None), 'phones': phones_in(texts)[:3]})
    return out


async def chat_access(pool, actor, peer_id: int):
    row = await pool.fetchrow('SELECT * FROM tg_chats WHERE client_id=$1 AND peer_id=$2', actor.client_id, peer_id) \
        if await has_table(pool, 'tg_chats') else None
    if not row:
        raise ValueError('Такой переписки нет.')
    if not is_privileged(actor):
        if row['phone']:
            await one(pool, actor, row['phone'])  # сделка своя, иначе Forbidden
        elif row['extension'] != actor.extension:
            raise Forbidden('Это переписка другого менеджера.')
    return row


async def chat_messages(pool, actor, peer_id: int, limit: int = 300) -> dict:
    """Переписка для карточки сделки: последние сообщения, старые сверху."""
    row = await chat_access(pool, actor, peer_id)
    tz = await store.zone(pool, actor.client_id)
    rows = await pool.fetch(
        """SELECT sent_at, outgoing, text, media FROM tg_messages WHERE client_id=$1 AND peer_id=$2
           ORDER BY sent_at DESC, message_id DESC LIMIT $3""", actor.client_id, peer_id, limit)
    data = loads(row['analysis'])
    return {'peer_id': peer_id, 'title': row['title'] or (f"@{row['username']}" if row['username'] else 'Без имени'),
            'username': row['username'], 'phone': key(row['phone']), 'linked': (data.get('link') or {}).get('by'),
            'details': data.get('details') if data.get('reviewed') else None,
            'messages': [{'at': iso(m['sent_at'], tz), 'out': m['outgoing'], 'text': m['text'], 'media': m['media']}
                         for m in reversed(rows)]}


async def link_chat(pool, actor, peer_id: int, phone: str, name: str | None) -> dict:
    """«Привязать к клиенту» для переписки: номер клиента, чат попадает в его сделку. Можно перепривязать."""
    from review.presentations import call_by_phone, phone_digits
    digits = phone_digits(phone)
    if not digits:
        raise ValueError('Введите номер клиента, например +7 999 123-45-67.')
    await chat_access(pool, actor, peer_id)
    tz = await store.zone(pool, actor.client_id)
    call_id = await call_by_phone(pool, actor.client_id, digits, None, tz)
    if call_id and not is_privileged(actor):
        owner = await pool.fetchval('SELECT extension FROM calls WHERE id=$1', call_id)
        if owner and owner != actor.extension:
            raise Forbidden('Это клиент другого менеджера.')
    await pool.execute(
        """UPDATE tg_chats SET phone=$3, call_id=$4, updated_at=now(),
             analysis=coalesce(analysis, '{}'::jsonb) || jsonb_build_object('link',
               jsonb_build_object('by', 'manual', 'name', $5::text, 'at', now()))
           WHERE client_id=$1 AND peer_id=$2""", actor.client_id, peer_id, digits, call_id, name or 'CRM')
    return {'phone': key(digits), 'call_found': call_id is not None}


def can_pay(actor) -> bool:
    """Оплату отмечает директор (владелец), не менеджер и не руководитель отдела."""
    return actor.role == 'owner'


async def add_event(pool, actor, phone: str, kind: str, note=None, reason=None, amount=None, name: str | None = None) -> dict:
    k = key(phone)
    if not k:
        raise ValueError('Неверный номер.')
    if kind not in EVENT_LABELS:
        raise ValueError('Неизвестное действие.')
    if kind == 'paid' and not can_pay(actor):
        raise Forbidden('Оплату отмечает директор.')
    if not is_privileged(actor) and kind not in MANAGER_EVENTS:
        raise Forbidden('Это действие недоступно.')
    current = await one(pool, actor, k)  # заодно проверяет, что сделка есть и она своя
    note = (str(note).strip()[:2000] or None) if note else None
    if kind == 'lost':
        if reason not in LOST_REASONS:
            raise ValueError('Выберите причину отказа.')
        if reason == 'other' and not note:
            raise ValueError('Напишите, почему отказ.')
    else:
        reason = None
    if kind in ('contract_edits', 'note') and not note:
        raise ValueError('Напишите, что именно.')
    value = None
    if kind == 'paid':
        try:
            value = round(float(str(amount).replace(' ', '').replace(',', '.')), 2)
        except (TypeError, ValueError):
            raise ValueError('Укажите сумму оплаты.')
        if value <= 0:
            raise ValueError('Укажите сумму оплаты.')
    await pool.execute(
        """INSERT INTO deal_events(client_id, phone, kind, reason, note, amount, extension, author_tg, author_name)
           VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
        actor.client_id, k, kind, reason, note, value, current['ext'], actor.telegram_user_id, name)
    return await one(pool, actor, k)
