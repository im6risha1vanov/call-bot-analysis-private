"""Данные робота для «Колл-центра»: из штатной вкладки «Робот» и её журнала ``robot_calls``.

Вкладка «Робот» переносит звонки робота в таблицу ``robot_calls`` (crm/robot_runner.py) с
меткой кабинета ``owner``: ``''`` — наш кабинет, ``'#<id>'`` — кабинет клиента. Здесь
берутся только звонки кабинета этой компании и только за выбранный период по её
часовому поясу.

Определения не смешиваются:

* **попытки робота** — звонки из ``robot_calls`` (каждая строка журнала — один звонок);
* **лиды** — номера этих звонков, у которых есть подтверждённая отметка лида в CRM
  (``crm_leads``/``crm_outcomes``/``cc_leads``), а не собственный итог робота.

Итоги робота по его разговорам («Заинтересованный», «Перезвон» и т. д.) показываются
отдельным списком как итоги робота. Ни одно из этих чисел не прибавляется к попыткам
операторов и лидам листа «Прозвон лидов»: источники и определения разные.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from . import call_analysis
from .callcenter_match import phone_key

SOURCE = 'Вкладка «Робот»'
OUTCOME_LABELS = {'interested': 'Заинтересованный', 'callback': 'Перезвон', 'not_relevant': 'Неактуально',
                  'ndz': 'Не взял трубку', 'do_not_call': 'Просил не звонить'}
NOTE = ('Попытки робота — звонки из вкладки «Робот»; лиды — только подтверждённые отметки CRM по этим '
        'номерам. С попытками операторов и лидами листа «Прозвон лидов» они не складываются: '
        'источники и определения разные.')


def empty(reason: str) -> dict:
    return {'available': False, 'reason': reason, 'source': SOURCE, 'cabinet': '', 'start': '', 'end': '',
            'attempts': 0, 'leads': 0, 'phones': 0, 'phones_without_lead': 0, 'outcomes': [], 'note': NOTE}


async def slice(pool, client_id, start: date | None, end: date | None, tz: ZoneInfo,
                operator: dict | None = None) -> dict:
    """Попытки и лиды робота этой компании за период. Только чтение."""
    operator = operator or {}
    if not operator.get('configured') or not operator.get('extension'):
        return empty(operator.get('note') or 'Добавочный робота не подтверждён: срез недоступен.')
    if not await call_analysis.has_table(pool, 'robot_calls'):
        return empty('Вкладка «Робот» ещё не переносила звонки в базу: таблицы robot_calls нет.')
    owner = await call_analysis.robot_owner(pool, client_id)
    start_at = datetime.combine(start, time.min, tz) if start else None
    end_at = datetime.combine(end + timedelta(days=1), time.min, tz) if end else None
    rows = await pool.fetch(
        """SELECT call_id, phone, at, outcome, duration_s, operator, campaign_id FROM robot_calls
           WHERE owner=$1 AND ($2::timestamptz IS NULL OR at >= $2)
             AND ($3::timestamptz IS NULL OR at < $3) ORDER BY at, call_id""",
        owner, start_at, end_at)
    index, _ = await call_analysis.lead_index(pool, client_id)
    phones = {}
    for row in rows:
        key = phone_key(row['phone'])
        if key:
            phones.setdefault(key, 0)
            phones[key] += 1
    confirmed = [key for key in phones if (index.get(key) or {}).get('confirmed')]
    outcomes = Counter(str(row['outcome'] or '') for row in rows)
    answer = {
        'available': True, 'reason': '', 'source': SOURCE,
        'cabinet': 'наш кабинет' if owner == '' else 'кабинет клиента',
        'start': start.isoformat() if start else '', 'end': end.isoformat() if end else '',
        'attempts': len(rows), 'leads': len(confirmed), 'phones': len(phones),
        'phones_without_lead': len(phones) - len(confirmed),
        'outcomes': [{'key': key, 'label': OUTCOME_LABELS.get(key, key or 'без итога'), 'count': count}
                     for key, count in outcomes.most_common()],
        'note': NOTE,
    }
    if not rows:
        answer['reason'] = 'За выбранный период звонков робота нет.'
    return answer
