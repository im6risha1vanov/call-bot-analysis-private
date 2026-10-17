"""Подробный разбор звонка для CRM: то же, что кнопка «Подробнее» в боте, плюс
таймкоды ошибок и расшифровка, чтобы переслушать нужное место записи.

Доступ тот же, что в боте (review.store.access): менеджер видит только свои
звонки, руководитель и владелец любые в своём отделе.
"""
from __future__ import annotations

import html

from review import engine, render
from review import store as review_store
from reports import fmt_phone

SPEAKERS = {'manager': 'Менеджер', 'client': 'Клиент'}


def moment(item: dict | None) -> dict | None:
    if not isinstance(item, dict):
        return None
    out = {
        'label': render.CATEGORY_LABELS.get(item.get('category')) if item.get('category') else None,
        'timecode': item.get('timecode') or None,
        'at': engine.seconds(item.get('timecode')),
        'speaker': SPEAKERS.get(item.get('speaker')),
        'quote': item.get('quote') or None,
        'what': item.get('what_happened') or item.get('why') or None,
        'say': item.get('say_instead') or None,
    }
    return out if any(out[k] for k in ('what', 'quote', 'label')) else None


def details_text(data: dict) -> str:
    """Текст «Подробнее» без HTML: модельный, если прошёл проверку, иначе из проверенных полей."""
    body = (data.get('details') or '').strip()
    if body and not data.get('text_unverified'):
        return body
    lines = []
    if data.get('success'):
        lines.append('Есть результат: ' + ', '.join(render.OUTCOME_LABELS.get(o, o) for o in data.get('outcome') or []) + '.')
    for item in [data.get('key_moment')] + list(data.get('other_misses') or []):
        m = moment(item)
        if m and m['what']:
            lines.append(m['what'])
    step = (data.get('client') or {}).get('next_step') or {}
    if step.get('raw'):
        lines.append('Договорённость: ' + step['raw'])
    return '\n'.join(lines)


def transcript_lines(text: str | None, roles: dict) -> list[dict]:
    """Расшифровка по репликам с секундами; спикеров называем по ролям из разбора."""
    names = {}
    if not roles.get('merged'):
        for role in ('manager', 'client'):
            label = engine.label(roles.get(role))
            if label:
                names[label] = SPEAKERS[role]
    out = []
    for raw in (text or '').splitlines():
        line = raw.strip()
        if not line:
            continue
        m = engine.LINE.match(line)
        if m:
            out.append({'at': int(m[1]) * 60 + int(m[2]), 'timecode': f'{m[1]}:{m[2]}', 'who': names.get(m[3], m[3]),
                        'text': m[4]})
        elif out:
            out[-1]['text'] += ' ' + line
    return out


async def review(pool, actor, call_id: int) -> dict:
    row = await review_store.access(pool, actor, call_id)
    data, meta = row['data'], row['meta']
    call = await pool.fetchrow('SELECT transcript, recording_id IS NOT NULL AS rec FROM calls WHERE id=$1', call_id)
    started = meta.get('call_started_at')
    client = data.get('client') or {}
    facts = [(name, client.get(key)) for name, key in (
        ('Компания', 'company'), ('Сфера', 'industry'), ('Собеседник', 'contact_name'), ('Должность', 'contact_role'),
        ('ЛПР', 'dmp_name'), ('Контакт ЛПР', 'dmp_contact'), ('Сейчас работают', 'current_solution'),
        ('Потребность', 'need'), ('Возражение', 'objection'))]
    step = client.get('next_step') or {}
    misses = [m for m in [moment(data.get('key_moment'))] + [moment(x) for x in data.get('other_misses') or []] if m]
    return {
        'id': call_id,
        'manager': meta.get('manager'), 'ext': row['extension'],
        'at': started.strftime('%d.%m.%Y %H:%M') if started else None,
        'seconds': int(meta.get('duration_seconds') or 0),
        'number': meta.get('client_number'), 'phone': fmt_phone(meta.get('client_number')),
        'reviewed': bool(data.get('reviewed')),
        'success': bool(data.get('success')), 'chance': data.get('chance'),
        'outcomes': [render.OUTCOME_LABELS.get(o, o) for o in data.get('outcome') or []],
        'evidence': [m for m in (moment({**e, 'what_happened': render.OUTCOME_LABELS.get(e.get('outcome'), e.get('outcome'))})
                                 for e in data.get('outcome_evidence') or [] if isinstance(e, dict)) if m],
        'misses': misses,
        'positives': [m for m in (moment(p) for p in data.get('positives') or []) if m],
        'summary': html.unescape(render.manager_text(data)) if data.get('reviewed') else None,
        'rop_line': data.get('rop_line'),
        'details': details_text(data),
        'facts': [{'name': n, 'value': v} for n, v in facts if v],
        'next_step': step.get('raw'),
        'rec': bool(call and call['rec']),
        'transcript': transcript_lines(call['transcript'] if call else None, data.get('roles') or {}),
    }
