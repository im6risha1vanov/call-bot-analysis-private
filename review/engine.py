"""Разбор звонка по результату: один запрос к модели, код только проверяет.

Модель (review/prompt.md) решает, был ли результат и где он потерян, и сама
пишет тексты менеджеру и РОПу. Код не дописывает выводов: проверяет, что
цитаты дословно есть в расшифровке у того же спикера и около того же
таймкода, убирает непроверяемые цитаты и согласует поля между собой.

Включается переменной CALL_REVIEW_MODE=v2. По умолчанию работает прежний
конвейер (оценка по 12 критериям), поэтому выкладка кода ничего не меняет,
пока режим не включён явно.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

PROMPT_VERSION = 'call_review_v2'
PROMPT = (Path(__file__).with_name('prompt.md').read_text(encoding='utf-8')
          .split('\n---\n', 1)[-1].strip())
MAX_TOKENS = int(os.getenv('CALL_REVIEW_MAX_TOKENS', '8000'))

REVIEWED = {'cold_first', 'repeat', 'gatekeeper_only'}
OUTCOMES = {'deal', 'meeting', 'callback_date', 'proposal', 'dmp_contact'}
CHANCES = {'hot', 'normal', 'none'}
LINE = re.compile(r'\[(\d+):(\d{2}) (Спикер \d+)\]\s*(.*)')
TIME_SLACK = 5


def enabled() -> bool:
    return os.getenv('CALL_REVIEW_MODE', 'legacy').strip().lower() == 'v2'


def norm(text) -> str:
    text = str(text or '').lower().replace('ё', 'е')
    return ' '.join(re.findall(r'\w+', text))


def turns(transcript: str) -> list[tuple[int, str, str]]:
    out = []
    for line in (transcript or '').splitlines():
        m = LINE.match(line.strip())
        if m:
            out.append((int(m[1]) * 60 + int(m[2]), m[3], norm(m[4])))
        elif out and line.strip():
            t, speaker, text = out[-1]
            out[-1] = (t, speaker, text + ' ' + norm(line))
    return out


def seconds(timecode) -> int | None:
    m = re.fullmatch(r'(?:\d+:)?(\d+):(\d{2})', str(timecode or '').strip())
    return int(m[1]) * 60 + int(m[2]) if m else None


def label(value) -> str | None:
    m = re.search(r'Спикер \d+', str(value or ''))
    return m[0] if m else None


def quote_ok(item: dict, data: dict, lines: list) -> bool:
    """Дословно, у того же спикера (если спикеры не склеены), рядом с таймкодом."""
    text = norm(item.get('quote'))
    at = seconds(item.get('timecode'))
    # Одно слово («Завтра») встречается где угодно: годится только с точным таймкодом.
    if not text or (len(text.split()) < 2 and at is None):
        return False
    hits = [line for line in lines if text in line[2]]
    roles = data.get('roles') or {}
    labels = {role: label(roles.get(role)) for role in ('manager', 'client')}
    speaker = labels.get(item.get('speaker'))
    # При склеенных дорожках метка не говорит, чья реплика: проверяем только текст и время.
    if speaker and not roles.get('merged') and labels['manager'] != labels['client']:
        hits = [line for line in hits if line[1] == speaker]
    if at is not None:
        hits = [line for line in hits if abs(line[0] - at) <= TIME_SLACK]
    return bool(hits)


def strip_quote(item: dict) -> dict:
    return {**item, 'quote': None, 'timecode': None}


def validate(data: dict, transcript: str) -> dict:
    """Согласует ответ модели с расшифровкой. Не придумывает новых выводов."""
    data = dict(data or {})
    lines = turns(transcript)
    kind = data.get('kind')
    if kind not in REVIEWED:
        return {**data, 'success': False, 'chance': None, 'key_moment': None, 'outcome': [],
                'manager_text': None, 'details': None, 'reviewed': False}
    evidence = [e for e in data.get('outcome_evidence') or [] if isinstance(e, dict) and quote_ok(e, data, lines)]
    # Результат засчитывается только с подтверждающей репликой: иначе это
    # предположение модели, а пропустить провал хуже, чем придраться.
    confirmed = {e.get('outcome') for e in evidence}
    outcome = [o for o in data.get('outcome') or [] if o in OUTCOMES and o in confirmed]
    # Готовый текст модели годится, только если его выводы прошли проверку;
    # иначе сообщение собирается из проверенных полей (render.manager_message).
    unverified = bool(data.get('outcome')) and not outcome
    data.update(outcome=outcome, outcome_evidence=evidence, success=bool(outcome), reviewed=True)
    if outcome:
        data.update(chance=None, key_moment=None)
    else:
        if data.get('chance') not in CHANCES:
            data['chance'] = 'normal'
        moment = data.get('key_moment') if isinstance(data.get('key_moment'), dict) else None
        if moment and moment.get('quote') and not quote_ok(moment, data, lines):
            unverified = unverified or norm(moment['quote']) in norm(data.get('manager_text'))
            moment = strip_quote(moment)
        data['key_moment'] = moment if data['chance'] != 'none' else None
    data['other_misses'] = [m if quote_ok(m, data, lines) else strip_quote(m)
                            for m in data.get('other_misses') or [] if isinstance(m, dict)][:2]
    data['positives'] = [p for p in data.get('positives') or [] if isinstance(p, dict)
                         and (not p.get('quote') or quote_ok(p, data, lines))][:2]
    data['text_unverified'] = unverified
    return data


def level(data: dict) -> str | None:
    """Совместимость со статистикой и дайджестами, которые читают astra_analysis.level."""
    if not data.get('reviewed'):
        return None
    if data.get('success'):
        return '✅'
    return '⚠️' if data.get('chance') == 'none' else '❌'


def immediate(data: dict) -> dict:
    """Кому отправить сразу. Остальное уходит в вечернюю сводку."""
    lost = data.get('reviewed') and not data.get('success') and data.get('chance') in {'hot', 'normal'}
    return {'manager': bool(lost), 'rop': bool(lost and data.get('chance') == 'hot')}


def build_input(transcript: str, meta: dict) -> str:
    head = [f"Менеджер: {meta.get('manager') or 'не указан'}",
            f"Дата звонка: {meta.get('date') or 'не указана'}",
            f"Длительность: {meta.get('duration') or 'не указана'}"]
    previous = meta.get('previous')
    if previous:
        head.append(f"Прошлый звонок этому клиенту ({previous.get('date')}): {previous.get('rop_line') or 'итог не сохранён'}")
    return '\n'.join(head) + '\n\nРАСШИФРОВКА:\n' + transcript


async def run(transcript: str, meta: dict, prompt: str | None = None) -> tuple[dict, float]:
    """prompt — полный текст из режима промта (review/prompts.py); по умолчанию PROMPT."""
    if not (transcript or '').strip():
        return validate({'kind': 'no_conversation'}, ''), 0.0
    from analysis import _call
    data, cost = await _call(prompt or PROMPT, build_input(transcript, meta), MAX_TOKENS)
    return validate(data, transcript), cost
