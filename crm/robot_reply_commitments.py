"""Conservative, unscheduled carryover of explicitly agreed callback times."""
from __future__ import annotations
import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DECLINE = re.compile(r'не звоните|не звонить|больше не звон|не беспокойте|удалите (?:мой )?номер|не надо (?:мне )?звонить|не перезванивайте', re.I)
ACCEPT = re.compile(r'^\s*(?:(?:да|ага|угу|хорошо|согласен|согласна|договорились|конечно|можно|подходит|давайте|верно)(?:[,.! ]+|$)){1,3}\s*$', re.I)
REQUEST = re.compile(r'перезвон|позвон|созвоним|свяжем|свяжитесь', re.I)
TIME = re.compile(r'\b(?:в|к)\s*(\d{1,2})(?:(?::|\.)(\d{2})|\s*час(?:а|ов)?(?:\s*(\d{1,2})\s*минут)?)?\s*(утра|дня|вечера|ночи)?\b', re.I)
DATE = re.compile(r'\b(\d{1,2})[.](\d{1,2})(?:[.](\d{4}))?\b')
WEEKDAYS = {'понедельник':0,'вторник':1,'среду':2,'среда':2,'четверг':3,'пятницу':4,'пятница':4,'субботу':5,'суббота':5,'воскресенье':6}


def parse_when(text, at, timezone):
    if not isinstance(at, datetime) or at.utcoffset() is None:
        return None
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return None
    local = at.astimezone(zone)
    value = ' '.join(str(text or '').lower().replace('ё','е').split())
    tm = TIME.search(value)
    if not tm:
        return None
    hour = int(tm.group(1)); minute = int(tm.group(2) or tm.group(3) or 0); period = tm.group(4)
    if hour > 23 or minute > 59:
        return None
    if period in ('дня','вечера') and 1 <= hour <= 11: hour += 12
    elif period == 'ночи' and hour == 12: hour = 0
    elif 1 <= hour <= 11 and period is None: return None
    if 'послезавтра' in value: date = local.date() + timedelta(days=2)
    elif 'завтра' in value: date = local.date() + timedelta(days=1)
    elif 'сегодня' in value: date = local.date()
    else:
        numeric = DATE.search(value)
        weekday = next((idx for name,idx in WEEKDAYS.items() if re.search(r'\b'+name+r'\b',value)),None)
        if numeric:
            day,month = int(numeric.group(1)),int(numeric.group(2)); year = int(numeric.group(3) or local.year)
            try: date = datetime(year,month,day).date()
            except ValueError: return None
        elif weekday is not None:
            delta = (weekday-local.weekday()) % 7
            if delta == 0: return None  # "в пятницу" on Friday is ambiguous
            date = local.date() + timedelta(days=delta)
        else: return None
    # Reject nonexistent and ambiguous local wall times, including DST transitions.
    wall = datetime(date.year,date.month,date.day,hour,minute)
    a, b = wall.replace(tzinfo=zone,fold=0), wall.replace(tzinfo=zone,fold=1)
    if a.utcoffset() != b.utcoffset(): return None
    if a <= local or a > local + timedelta(days=183): return None
    return a


def _turns(raw):
    if not isinstance(raw,list): return []
    out=[]
    for item in raw:
        if isinstance(item,(list,tuple)) and len(item)>=2: role,text=item[:2]
        elif isinstance(item,dict): role,text=item.get('who') or item.get('role'),item.get('text')
        else: continue
        role = str(role or '').lower()
        role = 'client' if role in ('client','клиент') else 'robot' if role in ('robot','робот','operator','оператор') else ''
        out.append((role,' '.join(str(text or '').split())))
    return out


def extract(raw, at, timezone, outcome=''):
    turns=_turns(raw)
    if outcome in ('do_not_call','not_relevant') or any(role=='client' and DECLINE.search(text) for role,text in turns):
        return {'state':'withdrawn','callback_at':None,'evidence':{'reason':'отказ от контакта или неактуально'}}
    agreed = None
    for i in range(len(turns)-1):
        role,text=turns[i]; next_role,next_text=turns[i+1]
        if not REQUEST.search(text): continue
        when=parse_when(text,at,timezone)
        if not when: continue
        if role=='robot' and next_role=='client' and ACCEPT.match(next_text): pass
        elif role=='client' and next_role=='robot' and ACCEPT.match(next_text): pass
        else: continue
        # A later denial invalidates earlier agreement; don't infer consent from a question.
        if any(r=='client' and DECLINE.search(t) for r,t in turns[i+2:]): continue
        # Never retain a full transcript or contact information.
        clean = lambda t: re.sub(r'(?<!\w)(?:\+?7|8)[\s(\-]*\d(?:[\s()\-]*\d){9}(?!\d)|[\w.+-]+@[\w.-]+\.[A-Za-zА-Яа-я]{2,}', '[скрыто]', t)
        agreed = {'state':'agreed','callback_at':when,'evidence':{'proposed':clean(text)[:250], 'confirmed':clean(next_text)[:120], 'roles':[role,next_role]}}
    return agreed


async def backfill(pool, limit=100):
    rows=await pool.fetch('''SELECT r.call_id,r.owner,r.phone,r.at,r.outcome,r.data,c.timezone,p.id AS project_id
       FROM robot_calls r JOIN robot_campaigns c ON c.id=r.campaign_id AND c.owner=r.owner
       JOIN robot_reply_project_campaigns pc ON pc.campaign_id=c.id
       JOIN robot_reply_projects p ON p.id=pc.project_id AND p.owner=r.owner
       LEFT JOIN robot_reply_commitment_scans s ON s.call_id=r.call_id
       ORDER BY s.scanned_at ASC NULLS FIRST,r.at LIMIT $1''', limit)
    from .dial import normalize
    for r in rows:
        phone = normalize(r['phone'])
        if not phone:
            await pool.execute('''INSERT INTO robot_reply_commitment_scans(call_id) VALUES($1)
                ON CONFLICT(call_id) DO UPDATE SET scanned_at=now()''', r['call_id'])
            continue
        data=json.loads(r['data']) if isinstance(r['data'],str) else (r['data'] or {})
        result=extract(data.get('transcript'),r['at'],r['timezone'],r['outcome'])
        async with pool.acquire() as conn,conn.transaction():
            if result:
                await conn.execute('''INSERT INTO robot_reply_auto_commitments(project_id,phone,state,callback_at,source_call_id,source_at,evidence)
                    VALUES($1,$2,$3,$4,$5,$6,$7::jsonb) ON CONFLICT(project_id,phone) DO UPDATE SET
                    state=EXCLUDED.state,callback_at=EXCLUDED.callback_at,source_call_id=EXCLUDED.source_call_id,
                    source_at=EXCLUDED.source_at,evidence=EXCLUDED.evidence,updated_at=now()
                    WHERE (EXCLUDED.source_at,EXCLUDED.source_call_id) >
                          (robot_reply_auto_commitments.source_at,robot_reply_auto_commitments.source_call_id)
                       OR (EXCLUDED.source_at=robot_reply_auto_commitments.source_at AND EXCLUDED.source_call_id=robot_reply_auto_commitments.source_call_id)''',
                    r['project_id'],phone,result['state'],result['callback_at'],r['call_id'],r['at'],json.dumps(result['evidence'],ensure_ascii=False))
            else:
                await conn.execute("""UPDATE robot_reply_auto_commitments SET state='withdrawn',callback_at=NULL,
                    evidence='{\"reason\":\"исправленная расшифровка не подтверждает согласие\"}'::jsonb,updated_at=now()
                    WHERE project_id=$1 AND phone=$2 AND source_call_id=$3 AND state='agreed'""",r['project_id'],phone,r['call_id'])
            await conn.execute('''INSERT INTO robot_reply_commitment_scans(call_id) VALUES($1)
                ON CONFLICT(call_id) DO UPDATE SET scanned_at=now()''',r['call_id'])
    return len(rows)
