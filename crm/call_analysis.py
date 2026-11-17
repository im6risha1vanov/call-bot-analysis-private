"""Разбор звонков операторов колл-центра и робота: воронка, покрытие и сверка с CRM.

Экран «Колл-центр» уже показывает клиентов, номера и суммы. Здесь отдельная
задача: по выбранному оператору (Ольга, Оксана, робот) взять звонки за период
по Москве, достать расшифровку и разложить разговоры по этапам найма.

Важные границы:

* Журнал звонков операторов — таблица ``calls`` (Mango). Для робота — его
  собственный журнал ``robot_calls``. Кампании робота и строки Google Таблиц
  не выдаются за звонки Mango и не смешиваются с ними.
* Лид в CRM — только запись, которую отметил человек (``crm_leads`` /
  ``cc_leads`` / ``crm_outcomes``). Ответ модели, итог робота и запись
  кампании лидом не считаются.
* Неизвестная атрибуция (номер не связан ни с одной CRM-записью) считается
  отдельно.
* Расшифровка и запросы к Mango/Deepgram идут в фоновом цикле, а не в
  HTTP-запросе. Повторный запуск за тот же период возвращает тот же прогон.
* Telegram получает только сводку по числам; ни телефонов, ни полных
  расшифровок, ни контактов в неё не попадает. Адресат — действующая личная
  привязка владельца учётной записи, запустившей разбор, а не chat_id из
  браузера, компании или общей группы.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from . import call_analysis_schema, callcenter_monitor, callcenter_quality

log = logging.getLogger(__name__)

def _moscow():
    """Пояс Москвы. На Windows без пакета tzdata берём фиксированный UTC+3:
    с 2014 года перехода на летнее время в Москве нет."""
    try:
        return ZoneInfo('Europe/Moscow')
    except Exception:  # noqa: BLE001 — нет системной базы часовых поясов
        return timezone(timedelta(hours=3), 'MSK')


MOSCOW = _moscow()
MAX_DAYS = 31
POLL_SECONDS = 3
ROBOT_EXTENSION = '13'
OKSANA_EXTENSION = '15'
ROBOT_SOURCE = 'robot_calls'
MANGO_SOURCE = 'calls'
HH_SCRIPT = 'hh_ru'
CONFIRMED_LEAD_STATUSES = ('interested', 'proposal', 'meeting')

STAGES = [
    ('attempt', 'Попытка'),
    ('live_human', 'Ответ живого человека'),
    ('qualification', 'Содержательная квалификация по найму'),
    ('need', 'Потребность в найме подтверждена'),
    ('hh_criteria', 'Критерии hh.ru: нет регистрации и кабинета работодателя'),
    ('specialist', 'Согласие на звонок специалиста'),
    ('crm_lead', 'Фактический лид в CRM'),
]
STAGE_NAMES = dict(STAGES)
REASONS = {
    'converted': ('crm', 'Разговор дошёл до лида в CRM'),
    'crm_lead_not_marked': ('crm', 'Квалификация есть, лид в CRM не отмечен'),
    'unknown_attribution': ('unknown', 'Номер не связан с CRM: атрибуция неизвестна'),
    'no_phone': ('unknown', 'В звонке нет номера: связать с CRM нельзя'),
    'no_conversation': ('quality', 'Нет разговора: короткий звонок или не взяли трубку'),
    'answering_machine': ('quality', 'Автоответчик или голосовое меню'),
    'audio_missing': ('technical', 'Записи звонка нет'),
    'transcript_failed': ('technical', 'Расшифровка не получена'),
    'uncertain_transcript': ('technical', 'Неуверенная расшифровка: нужна ручная проверка'),
    'scenario_mismatch': ('unknown', 'Сценарий не совпадает со скриптом hh.ru: этапы не оцениваются'),
    'client_not_hiring': ('client', 'Клиент не подтвердил найм'),
    'no_need': ('client', 'Потребность в найме не подтверждена'),
    'criteria_not_met': ('client', 'Критерии hh.ru не выполнены'),
    'criteria_unconfirmed': ('unknown', 'Критерии hh.ru не подтверждены: нужна ручная проверка'),
    'no_specialist_consent': ('unknown', 'Согласие на звонок специалиста не подтверждено'),
    'missed_qualification': ('operator', 'Возможный пропуск: клиент говорил о найме, квалификации нет'),
}


# ------------------------------------------------------------------ операторы


def _env(env=None) -> dict:
    return os.environ if env is None else env


def _get(row, key, default=None):
    try:
        return row[key]
    except (KeyError, TypeError):
        return getattr(row, key, default)


def _norm(value) -> str:
    return re.sub(r'\s+', ' ', str(value or '').lower().replace('ё', 'е')).strip()


def _operator_map(raw: str | None) -> dict:
    """CALL_ANALYSIS_OPERATORS: JSON-словарь или строка «olga=14,oksana=15»."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        data = {}
        for part in re.split(r'[,;]', raw):
            key, sep, value = part.partition('=')
            if sep and key.strip():
                data[key.strip().lower()] = value.strip()
    if not isinstance(data, dict):
        return {}
    out = {}
    for key, value in data.items():
        if isinstance(value, dict):
            ext = value.get('extension') or value.get('ext')
            item = {'extension': str(ext).strip() if ext not in (None, '') else None,
                    'name': value.get('name')}
        else:
            item = {'extension': str(value).strip(), 'name': None}
        if item['extension']:
            out[str(key).strip().lower()] = item
    return out


def _employee_extension(employees, needle: str, exclude: set[str]) -> tuple[str | None, str]:
    """Единственный активный менеджер с таким именем — допустимое сопоставление."""
    found = {}
    for person in employees or []:
        if _norm(_get(person, 'role')) != 'manager' or _get(person, 'archived_at'):
            continue
        extension = str(_get(person, 'extension') or '')
        name = _norm(_get(person, 'full_name'))
        if extension and extension not in exclude and needle in name:
            found[extension] = _get(person, 'full_name') or extension
    if len(found) == 1:
        extension, name = next(iter(found.items()))
        return extension, f'сотрудники CRM: {name}'
    return None, ''


def operator_specs(env=None, employees=None) -> list[dict]:
    """Три оператора. Подтверждены только 15 (Оксана) и 13 (робот).

    Добавочный Ольги код не подтверждает: его берём из явной настройки
    ``CALL_ANALYSIS_OLGA_EXT``, из единственного ``KITCHEN_EXTENSIONS`` или из
    единственной активной «Ольги» в сотрудниках. Иначе — понятное пустое
    состояние, а не догадка.
    """
    env = _env(env)
    explicit = _operator_map(env.get('CALL_ANALYSIS_OPERATORS'))
    specs = [
        {'id': 'olga', 'name': 'Ольга', 'kind': 'operator', 'journal': MANGO_SOURCE,
         'extension': None, 'configured': False, 'source': '',
         'note': 'Добавочный Ольги не подтверждён. Задайте CALL_ANALYSIS_OLGA_EXT, CALL_ANALYSIS_OPERATORS или один добавочный в KITCHEN_EXTENSIONS.',
         'script': str(env.get('CALL_ANALYSIS_SCRIPT') or HH_SCRIPT)},
        {'id': 'oksana', 'name': 'Оксана Кирнева', 'short': 'Оксана', 'kind': 'operator', 'journal': MANGO_SOURCE,
         'extension': OKSANA_EXTENSION, 'configured': True,
         'source': 'подтверждено владельцем: добавочный 15',
         'note': '', 'script': str(env.get('CALL_ANALYSIS_SCRIPT') or HH_SCRIPT)},
        {'id': 'robot', 'name': 'Робот', 'kind': 'robot', 'journal': MANGO_SOURCE, 'catalog': ROBOT_SOURCE,
         'extension': ROBOT_EXTENSION, 'configured': True,
         'source': 'код: ROBOT_EXTENSION / ROBOT_EXTENSIONS',
         'note': '', 'script': str(env.get('CALL_ANALYSIS_ROBOT_SCRIPT') or '')},
    ]
    by_id = {item['id']: item for item in specs}

    def set_extension(operator_id: str, extension, source: str) -> None:
        item = by_id[operator_id]
        item.update(extension=str(extension), configured=True, source=source, note='')

    for operator_id, item in explicit.items():
        if operator_id in by_id:
            set_extension(operator_id, item['extension'], 'CALL_ANALYSIS_OPERATORS')
            if item.get('name'):
                by_id[operator_id]['name'] = item['name']

    if env.get('CALL_ANALYSIS_OLGA_EXT') and not by_id['olga']['configured']:
        set_extension('olga', str(env['CALL_ANALYSIS_OLGA_EXT']).strip(), 'CALL_ANALYSIS_OLGA_EXT')

    if not by_id['olga']['configured']:
        kitchen = {e.strip() for e in str(env.get('KITCHEN_EXTENSIONS') or '').split(',') if e.strip()}
        kitchen -= {ROBOT_EXTENSION, OKSANA_EXTENSION}
        if len(kitchen) == 1:
            set_extension('olga', next(iter(kitchen)), 'KITCHEN_EXTENSIONS')

    if not by_id['olga']['configured']:
        extension, source = _employee_extension(employees, 'ольга', {ROBOT_EXTENSION, OKSANA_EXTENSION})
        if extension:
            set_extension('olga', extension, source)

    for item in specs:
        item['display'] = item.get('short') or item['name']
        item['countable'] = bool(item['configured'])
    return specs


def operator_by_id(specs: list[dict], operator_id: str) -> dict:
    for item in specs:
        if item['id'] == operator_id:
            return item
    raise ValueError('Неизвестный оператор.')


# ------------------------------------------------------------------ период


def _as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or '')[:10])
    except ValueError as exc:
        raise ValueError('Неверная дата периода.') from exc


def period_bounds(start, end, tz=MOSCOW) -> tuple[datetime, datetime]:
    """Начало и исключающий конец периода по Москве (конец включительно для даты)."""
    first, last = _as_date(start), _as_date(end)
    if last < first:
        raise ValueError('Конец периода раньше начала.')
    if (last - first).days + 1 > MAX_DAYS:
        raise ValueError(f'Период — не больше {MAX_DAYS} дней.')
    start_local = datetime.combine(first, time.min, tzinfo=tz)
    end_local = datetime.combine(last + timedelta(days=1), time.min, tzinfo=tz)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def period_label(start, end) -> str:
    first, last = _as_date(start), _as_date(end)
    return f'{first:%d.%m.%Y}–{last:%d.%m.%Y}'


def dedup_key(client_id: int, operator_id: str, extension, start, end) -> str:
    return f'{int(client_id)}:{operator_id}:{extension or ""}:{_as_date(start)}:{_as_date(end)}'


# ------------------------------------------------------------------ доступ


def can_manage(who) -> bool:
    """Разбор и выгрузка — только директор/руководитель по правам из сессии."""
    actor = getattr(who, 'actor', None)
    return bool(who and getattr(who, 'role', None) == 'director' and actor is not None
                and getattr(actor, 'role', None) in ('owner', 'head')
                and not getattr(who, 'test', False) and not getattr(who, 'support', False))


def company_of(who) -> int | None:
    company = getattr(who, 'company', None)
    if company is not None:
        return company
    actor = getattr(who, 'actor', None)
    return getattr(actor, 'client_id', None)


# ------------------------------------------------------------------ расшифровка


SPEAKER_RE = re.compile(r'^\[(\d{1,2}):(\d{2})\s+([^\]]+)\]\s*(.*)$')
PLAIN_SPEAKER_RE = re.compile(r'^(Робот|Клиент|Менеджер|Оператор|Спикер\s*\d+):\s*(.*)$', re.I)
HIRING = re.compile(r'\b(найм\w*|нанима\w*|подбор\w*|ваканс\w*|кандидат\w*|соискател\w*|персонал\w*|сотрудник\w*|резюме|hh\.?ru|хедхантер|штат\w*|должност\w*)\b')
ASK = re.compile(r'\b(сколько|какие|какой|какая|кто|кого|ищет|ищете|нужн\w*|требует\w*|подбира\w*|зарплат\w*|оклад\w*|опыт\w*|город\w*|удаленк\w*|офис\w*)\b')
NEED = re.compile(r'\b(нужен|нужна|нужно|нужны|требуется|требуются|ищем|ищет|нанимаем|нанимает|набираем|планируем нанять|есть вакансия|открыта вакансия|подбираем)\b')
NEG_NEED = re.compile(r'\b(не нужен|не нужна|не нужно|не нужны|не требуется|нет ваканс\w*|не ищем|не нанима\w*|не подбира\w*)\b')
REG_NO = re.compile(r'\b(нет регистрации|не зарегистрирован\w*|без регистрации|не регистрировал\w*|нет аккаунта)\b')
CAB_NO = re.compile(r'\b(нет кабинета|нет личного кабинета|без кабинета|кабинет\w* не|не создан\w* кабинет|нет аккаунта работодателя|не пользуемся hh|не размещаем)\b')
CONSENT = re.compile(r'\b(да|конечно|согласен|согласна|договорились|пусть звонит|пусть позвонит|передавайте|можно|хорошо)\b')
SPECIALIST_CALL = re.compile(r'\b(специалист\w*|позвонит|перезвонит|звонит|созвон\w*|переда\w*)\b')
ANSWERING = re.compile(r'\b(автоответчик|голосовое меню|оставьте сообщение|абонент недоступен|не отвечает|секретарь)\b')


def _clean_quote(value, limit: int = 180) -> str:
    return re.sub(r'\s+', ' ', str(value or '')).strip()[:limit]


def transcript_text(value) -> str:
    """Расшифровка из calls.transcript или из robot_calls.data.transcript."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        value = [value]
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, dict):
                who, text = item.get('who'), item.get('text')
                if text:
                    lines.append(f'{who}: {text}' if who else str(text))
            elif item:
                lines.append(str(item))
        return '\n'.join(lines).strip()
    return ''


def split_lines(value) -> list[dict]:
    out = []
    for raw in transcript_text(value).splitlines():
        line = raw.strip()
        if not line:
            continue
        found = SPEAKER_RE.match(line)
        if found:
            out.append({'at': int(found.group(1)) * 60 + int(found.group(2)), 'speaker': found.group(3).strip(),
                        'text': found.group(4).strip()})
            continue
        plain = PLAIN_SPEAKER_RE.match(line)
        if plain:
            out.append({'at': None, 'speaker': plain.group(1).strip(), 'text': plain.group(2).strip()})
        elif out:
            out[-1]['text'] = (out[-1]['text'] + ' ' + line).strip()
        else:
            out.append({'at': None, 'speaker': None, 'text': line})
    return out


def _role_of(label) -> str | None:
    text = _norm(label)
    if 'клиент' in text or 'абонент' in text:
        return 'client'
    if 'робот' in text or 'менеджер' in text or 'оператор' in text:
        return 'operator'
    return None


def _speaker_roles(lines: list[dict], roles: dict | None) -> tuple[dict, bool]:
    """Кто оператор, кто клиент. Без явных ролей первый говорящий — оператор."""
    mapping = {}
    for label, role in (roles or {}).items():
        role = _role_of(role) or _role_of(label)
        if role:
            mapping[label] = role
    labels = {line.get('speaker') for line in lines if line.get('speaker')}
    if labels and all(_role_of(label) for label in labels):
        return {label: _role_of(label) for label in labels}, True
    order = []
    for line in lines:
        label = line.get('speaker')
        if label and label not in order:
            order.append(label)
    if roles:
        known = {line.get('speaker') for line in lines if line.get('speaker')}
        if known and all(label in mapping for label in known):
            return mapping, True
    if len(order) >= 2:
        mapping = {order[0]: 'operator', order[1]: 'client'}
        for label in order[2:]:
            mapping[label] = 'client'
        return mapping, True
    return {}, False


def _stage(state: str, quote=None, at=None, detail=None) -> dict:
    return {'state': state, 'quote': _clean_quote(quote) or None, 'at': at, 'detail': detail}


def _find(lines: list[dict], pattern: re.Pattern) -> dict | None:
    for line in lines:
        if pattern.search(_norm(line.get('text'))):
            return line
    return None


def classify_transcript(transcript, *, duration: int = 0, confidence: float | None = None,
                        kind: str = 'operator', scenario: str = HH_SCRIPT,
                        roles: dict | None = None) -> dict:
    """Консервативно разложить расшифровку по этапам. Слабые данные не додумываем."""
    text = transcript_text(transcript)
    duration = int(duration or 0)
    lines = split_lines(text)
    low_confidence = confidence is not None and float(confidence) < 0.55
    roles_map, roles_known = _speaker_roles(lines, roles)
    operator_lines = [line for line in lines if roles_map.get(line.get('speaker')) == 'operator']
    client_lines = [line for line in lines if roles_map.get(line.get('speaker')) == 'client']
    all_text = _norm(text)
    client_text = _norm(' '.join(line['text'] for line in client_lines))
    hiring_topic = bool(HIRING.search(all_text))
    scenario_hh = _norm(scenario) in {HH_SCRIPT, 'hh', 'hh.ru', 'hiring', 'найм'}
    answering = bool(ANSWERING.search(all_text)) if all_text else False

    stages = {'attempt': _stage('yes')}
    if not text:
        stages['live_human'] = _stage('no' if duration < 8 else 'unknown',
                                      detail='Нет разговора' if duration < 8 else 'Расшифровки нет')
    elif low_confidence:
        stages['live_human'] = _stage('unknown', detail='Неуверенная расшифровка')
    elif answering:
        stages['live_human'] = _stage('no', quote=_find(lines, ANSWERING)['text'], at=_find(lines, ANSWERING)['at'],
                                      detail='Автоответчик или голосовое меню')
    elif duration and duration < 8:
        stages['live_human'] = _stage('no', detail='Слишком короткий разговор')
    else:
        stages['live_human'] = _stage('yes', quote=lines[0]['text'] if lines else None,
                                      at=lines[0]['at'] if lines else None)

    live_ok = stages['live_human']['state'] == 'yes'
    if not live_ok or not roles_known or low_confidence:
        reason = 'Нет расшифровки' if not text else 'Неуверенная расшифровка' if low_confidence else 'Не удалось разделить говорящих'
        for key in ('qualification', 'need', 'hh_criteria', 'specialist'):
            stages[key] = _stage('unknown', detail=reason)
    else:
        qualification = hiring_topic and (bool(ASK.search(all_text)) or len(client_text) >= 20)
        stages['qualification'] = _stage('yes' if qualification else 'no',
                                         quote=(_find(client_lines, HIRING) or _find(operator_lines, HIRING) or {}).get('text'),
                                         at=(_find(client_lines, HIRING) or _find(operator_lines, HIRING) or {}).get('at'))

        need_line = None
        for line in client_lines:
            value = _norm(line['text'])
            if NEED.search(value) and not NEG_NEED.search(value):
                need_line = line
                break
        if need_line is None and client_lines and operator_lines:
            # «Вам нужен сотрудник?» — «Да»: согласие в следующей реплике клиента.
            for index, line in enumerate(lines):
                if roles_map.get(line.get('speaker')) != 'client' or index == 0:
                    continue
                value = _norm(line['text'])
                if REG_NO.search(value) or CAB_NO.search(value):
                    continue
                previous = lines[index - 1]
                if re.match(r'^(да|конечно|верно|ага|угу)\b', value) \
                        and roles_map.get(previous.get('speaker')) == 'operator' \
                        and NEED.search(_norm(previous['text'])):
                    need_line = line
                    break
        need = need_line is not None
        stages['need'] = _stage('yes' if need else 'no', quote=need_line['text'] if need_line else None,
                                at=need_line['at'] if need_line else None)
        if need and stages['qualification']['state'] != 'yes':
            stages['qualification'] = _stage('yes', quote=need_line['text'], at=need_line['at'],
                                             detail='Потребность в найме уже содержательна')

        if not scenario_hh:
            stages['hh_criteria'] = _stage('not_applicable',
                                           detail=f'Сценарий «{scenario or "неизвестен"}» не совпадает со скриптом hh.ru')
        else:
            reg = REG_NO.search(client_text)
            cab = CAB_NO.search(client_text)
            hh_line = next((line for line in client_lines if REG_NO.search(_norm(line['text'])) or CAB_NO.search(_norm(line['text']))), None)
            state = 'yes' if reg and cab else 'no' if (reg or cab) else 'unknown'
            detail = None if state == 'yes' else 'Клиент не подтвердил оба условия'
            stages['hh_criteria'] = _stage(state, quote=hh_line['text'] if hh_line else None,
                                           at=hh_line['at'] if hh_line else None, detail=detail)

        consent_line = None
        for line in client_lines:
            value = _norm(line['text'])
            if CONSENT.search(value) and SPECIALIST_CALL.search(value):
                consent_line = line
                break
        if consent_line is None:
            for index, line in enumerate(lines):
                if roles_map.get(line.get('speaker')) != 'client' or index == 0:
                    continue
                previous = lines[index - 1]
                if re.match(r'^(да|конечно|согласен|согласна|договорились|хорошо)\b', _norm(line['text'])) \
                        and roles_map.get(previous.get('speaker')) == 'operator' \
                        and SPECIALIST_CALL.search(_norm(previous['text'])):
                    consent_line = line
                    break
        stages['specialist'] = _stage('yes' if consent_line else 'no',
                                      quote=consent_line['text'] if consent_line else None,
                                      at=consent_line['at'] if consent_line else None)

    stages['crm_lead'] = _stage('unknown', detail='Лид в CRM проверяется отдельно')
    return {'stages': stages, 'roles_known': roles_known, 'low_confidence': low_confidence,
            'speaker_uncertain': not roles_known, 'hiring_topic': hiring_topic,
            'scenario_hh_compatible': scenario_hh, 'scenario': scenario or 'unknown'}


def apply_lead(result: dict, lead: dict | None, has_reference: bool = True) -> dict:
    """Добавить фактический лид CRM. Лид — только отметка человека, не модель."""
    lead = lead or None
    if lead is None:
        state = 'unknown'
        detail = 'Номер не связан с CRM: атрибуция неизвестна' if has_reference else 'В CRM нет записей для сверки: атрибуция неизвестна'
    else:
        state = 'yes' if lead.get('confirmed') else 'no'
        detail = 'Лид отмечен человеком в CRM' if lead.get('confirmed') else 'Есть карточка CRM, но лид не отмечен'
    result['stages']['crm_lead'] = _stage(state, detail=detail)
    result['lead'] = lead
    result['unknown_attribution'] = bool(lead is None or not lead.get('base'))
    return result


def reason_for(item: dict) -> dict:
    """Почему разговор оборвался. Слабые данные не превращаются в обвинение."""
    stages = item.get('stages') or {}
    status = item.get('status')
    if status == 'audio_missing':
        code = 'audio_missing'
    elif status == 'transcript_failed':
        code = 'transcript_failed'
    elif item.get('low_confidence'):
        code = 'uncertain_transcript'
    elif not item.get('phone'):
        code = 'no_phone'
    elif stages.get('crm_lead', {}).get('state') == 'yes':
        code = 'converted'
    elif item.get('stages', {}).get('live_human', {}).get('state') == 'no':
        code = 'answering_machine' if item.get('answering_machine') else 'no_conversation'
    elif stages.get('live_human', {}).get('state') != 'yes':
        code = 'no_conversation'
    elif item.get('speaker_uncertain'):
        code = 'uncertain_transcript'
    elif item.get('scenario_hh_compatible') is False:
        code = 'scenario_mismatch'
    elif stages.get('qualification', {}).get('state') != 'yes':
        code = 'missed_qualification' if item.get('hiring_topic') else 'client_not_hiring'
    elif stages.get('need', {}).get('state') != 'yes':
        code = 'no_need'
    elif stages.get('hh_criteria', {}).get('state') == 'no':
        code = 'criteria_not_met'
    elif stages.get('hh_criteria', {}).get('state') == 'unknown':
        code = 'criteria_unconfirmed'
    elif stages.get('specialist', {}).get('state') != 'yes':
        code = 'no_specialist_consent'
    elif stages.get('crm_lead', {}).get('state') == 'unknown':
        code = 'unknown_attribution'
    else:
        code = 'crm_lead_not_marked'
    group, label = REASONS[code]
    return {'code': code, 'group': group, 'label': label}


# ------------------------------------------------------------------ сводка


def summarize(items: list[dict], *, operator: dict | None = None, period: str = '') -> dict:
    total = len(items)
    stages = []
    for key, name in STAGES:
        reached = sum(1 for item in items if (item.get('stages') or {}).get(key, {}).get('state') == 'yes')
        unknown = sum(1 for item in items if (item.get('stages') or {}).get(key, {}).get('state') == 'unknown')
        not_applicable = sum(1 for item in items if (item.get('stages') or {}).get(key, {}).get('state') == 'not_applicable')
        stages.append({'key': key, 'name': name, 'reached': reached, 'unknown': unknown,
                       'not_applicable': not_applicable,
                       'share': round(reached / total * 100, 1) if total else 0})
    reasons = {}
    for item in items:
        code = (item.get('break') or reason_for(item)).get('code')
        if code not in REASONS:
            code = 'unknown_attribution' if item.get('unknown_attribution') else 'no_conversation'
        reasons[code] = reasons.get(code, 0) + 1
    reason_rows = [{'code': code, 'group': REASONS[code][0], 'label': REASONS[code][1], 'count': count}
                   for code, count in sorted(reasons.items(), key=lambda pair: (-pair[1], pair[0]))]
    sources = {}
    for item in items:
        source = item.get('transcript_source') or 'none'
        sources[source] = sources.get(source, 0) + 1
    coverage = {
        'calls': total,
        'phones': sum(1 for item in items if item.get('phone')),
        'existing_transcripts': sources.get('existing', 0),
        'transcribed': sources.get('deepgram', 0),
        'robot_transcripts': sources.get('robot', 0),
        'no_transcript': sources.get('none', 0),
        'audio_missing': sum(1 for item in items if item.get('status') == 'audio_missing'),
        'transcript_failed': sum(1 for item in items if item.get('status') == 'transcript_failed'),
        'low_confidence': sum(1 for item in items if item.get('low_confidence')),
        'unknown_attribution': sum(1 for item in items if item.get('unknown_attribution')),
        'unknown_stages': stages[1]['unknown'] if len(stages) > 1 else 0,
    }
    crm = sum(1 for item in items if (item.get('stages') or {}).get('crm_lead', {}).get('state') == 'yes')
    predicted = sum(1 for item in items if (item.get('stages') or {}).get('specialist', {}).get('state') == 'yes')
    both = sum(1 for item in items if (item.get('stages') or {}).get('crm_lead', {}).get('state') == 'yes'
               and (item.get('stages') or {}).get('specialist', {}).get('state') == 'yes')
    comparison = None
    if crm:
        comparison = {'crm': crm, 'analysis': predicted, 'both': both,
                      'crm_only': crm - both, 'analysis_only': predicted - both}
    return {
        'operator': operator or {},
        'period': period,
        'total': total,
        'stages': stages,
        'reasons': reason_rows,
        'coverage': coverage,
        'crm_leads': crm,
        'predicted_leads': predicted,
        'comparison': comparison,
        'comparison_note': None if comparison else 'В CRM нет отмеченных лидов за этот период: сравнение недоступно.',
        'model_leads_ignored': sum(1 for item in items if item.get('model_lead_hint')),
        'unknown_attribution': coverage['unknown_attribution'],
        'empty': total == 0,
    }


def telegram_text(summary: dict) -> str:
    """Только числа. Ни телефонов, ни контактов, ни расшифровок."""
    operator = (summary.get('operator') or {}).get('display') or (summary.get('operator') or {}).get('name') or 'оператор'
    lines = [f'Разбор звонков · {operator}', f'Период: {summary.get("period") or "—"} (Москва)',
             f'Звонков: {summary.get("total", 0)}']
    for stage in summary.get('stages', [])[1:]:
        note = f' (неизвестно {stage["unknown"]})' if stage.get('unknown') else ''
        if stage.get('not_applicable'):
            note += f' (не применимо {stage["not_applicable"]})'
        lines.append(f'{stage["name"]}: {stage["reached"]}{note}')
    coverage = summary.get('coverage') or {}
    lines.append('Покрытие: расшифровок ' + str(
        (coverage.get('existing_transcripts') or 0) + (coverage.get('transcribed') or 0) + (coverage.get('robot_transcripts') or 0))
        + f', без записи {coverage.get("audio_missing", 0)}, ошибок расшифровки {coverage.get("transcript_failed", 0)}')
    if coverage.get('unknown_attribution'):
        lines.append(f'Неизвестная атрибуция: {coverage["unknown_attribution"]}')
    if summary.get('reasons'):
        lines.append('Причины: ' + '; '.join(f'{row["label"]} — {row["count"]}' for row in summary['reasons'][:3]))
    if summary.get('comparison'):
        lines.append('Сверка с CRM: лидов ' + str(summary['comparison']['crm']) + ', совпало ' + str(summary['comparison']['both']))
    else:
        lines.append('Сверка с CRM недоступна: отмеченных лидов нет')
    lines.append('Без контактов и полных записей.')
    return '\n'.join(lines)


# ------------------------------------------------------------------ база


async def _has_table(pool, name: str) -> bool:
    try:
        return bool(await pool.fetchval('SELECT to_regclass($1) IS NOT NULL', name))
    except Exception:  # noqa: BLE001 — тестовая/старая база без таблицы
        return False


def _phone_key(value) -> str | None:
    digits = re.sub(r'\D', '', str(value or ''))
    return digits[-10:] if len(digits) >= 10 else (digits or None)


def _json(value, default):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


async def _employees(pool, client_id: int) -> list:
    try:
        return await pool.fetch(
            """SELECT extension, full_name, role, archived_at FROM employees
               WHERE client_id=$1 ORDER BY extension""", client_id)
    except Exception:  # noqa: BLE001
        return []


async def operators(pool, client_id: int) -> list[dict]:
    return operator_specs(os.environ, await _employees(pool, client_id))


async def author_name(pool, who) -> str:
    actor = getattr(who, 'actor', None)
    client_id = company_of(who)
    invite_id = getattr(who, 'invite_id', None)
    if invite_id:
        name = await pool.fetchval('SELECT coalesce(nullif(name,\'\'), login) FROM crm_invites WHERE id=$1 AND client_id=$2',
                                   invite_id, client_id)
        if name:
            return str(name)
    if actor is not None:
        name = await pool.fetchval(
            """SELECT full_name FROM employees WHERE client_id=$1
               AND (extension=$2 OR telegram_user_id=$3) ORDER BY extension LIMIT 1""",
            client_id, getattr(actor, 'extension', None), getattr(actor, 'telegram_user_id', None) or None)
        if name:
            return str(name)
    return 'Директор'


async def telegram_target(pool, who) -> int | None:
    """Личная действующая привязка владельца сессии. Никаких chat_id из запроса."""
    if not can_manage(who):
        return None
    actor = who.actor
    telegram_user_id = getattr(actor, 'telegram_user_id', None)
    if not telegram_user_id:
        return None
    client_id = company_of(who)
    found = await pool.fetchval(
        """SELECT 1 FROM employees WHERE client_id=$1 AND telegram_user_id=$2
           AND role IN ('owner','head') AND archived_at IS NULL""", client_id, telegram_user_id)
    return int(telegram_user_id) if found else None


async def run_telegram_target(pool, run) -> int | None:
    """Привязка того, кто запустил разбор; текущая сессия её не подменяет."""
    telegram_user_id = run.get('author_tg') if hasattr(run, 'get') else run['author_tg']
    if not telegram_user_id:
        return None
    found = await pool.fetchval(
        """SELECT 1 FROM employees WHERE client_id=$1 AND telegram_user_id=$2
           AND role IN ('owner','head') AND archived_at IS NULL""", run['client_id'], telegram_user_id)
    return int(telegram_user_id) if found else None


async def _robot_owner(pool, client_id: int) -> str:
    configured = os.getenv('CRM_HOME_COMPANY')
    home = int(configured) if configured else await pool.fetchval(
        'SELECT id FROM clients WHERE processing_enabled ORDER BY id LIMIT 1')
    return '' if client_id == home else f'#{client_id}'


async def _robot_catalog(pool, client_id: int, start_at: datetime, end_at: datetime) -> dict[str, list]:
    """Записи кабинета робота — только для расшифровки и сценария, не для счёта звонков.

    Связь с журналом Mango подтверждается телефоном и ближайшим временем (±10 минут).
    Запись кампании сама по себе не становится звонком или лидом.
    """
    if not await _has_table(pool, 'robot_calls'):
        return {}
    owner = await _robot_owner(pool, client_id)
    rows = await pool.fetch(
        """SELECT call_id, phone, at, coalesce(duration_s,0) AS duration, operator, outcome, data, recording
           FROM robot_calls WHERE owner=$1 AND at >= $2 AND at < $3 ORDER BY at, call_id""",
        owner, start_at - timedelta(minutes=10), end_at + timedelta(minutes=10))
    out = {}
    for row in rows:
        key = _phone_key(row['phone'])
        if key:
            out.setdefault(key, []).append(dict(row))
    return out


def _robot_match(index: dict[str, list], phone, at):
    key = _phone_key(phone)
    if not key or at is None:
        return None
    candidates = index.get(key) or []
    if not candidates:
        return None
    best = min(candidates, key=lambda row: abs((row['at'] - at).total_seconds()))
    return best if abs((best['at'] - at).total_seconds()) <= 600 else None


async def _load_calls(pool, client_id: int, operator: dict, start_at: datetime, end_at: datetime) -> list[dict]:
    """Звонки оператора из журнала Mango. Для робота кабинет только дополняет запись."""
    rows = await pool.fetch(
        """SELECT id, client_number, call_started_at, coalesce(duration_seconds,0) AS duration,
                  transcript, recording_id, status
           FROM calls WHERE client_id=$1 AND extension=$2 AND direction=2
             AND call_started_at >= $3 AND call_started_at < $4 ORDER BY call_started_at, id""",
        client_id, operator.get('extension'), start_at, end_at)
    catalog = await _robot_catalog(pool, client_id, start_at, end_at) if operator.get('kind') == 'robot' else {}
    if operator.get('kind') == 'robot' and not rows:
        # Журнал Mango не содержит звонков робота: показываем его собственный журнал,
        # явно помечая источник. Кампания сама по себе лидом не становится.
        out = []
        for entries in catalog.values():
            for row in entries:
                if not (start_at <= row['at'] < end_at):
                    continue
                data = _json(row['data'], {})
                out.append({'ref': f"robot:{row['call_id']}", 'call_id': row['call_id'], 'phone': row['phone'],
                            'at': row['at'], 'duration': int(row['duration'] or 0),
                            'transcript': transcript_text(data.get('transcript')), 'recording_id': None,
                            'recording_path': data.get('recording') or row.get('recording'),
                            'status': 'robot', 'scenario': str(row['operator'] or ''),
                            'model_lead_hint': str(row['outcome'] or '') in ('interested', 'lead'),
                            'journal': ROBOT_SOURCE})
        return sorted(out, key=lambda item: (item['at'], str(item['call_id'])))
    out = []
    for row in rows:
        item = {'ref': f"calls:{row['id']}", 'call_id': row['id'], 'phone': row['client_number'],
                'at': row['call_started_at'], 'duration': int(row['duration'] or 0),
                'transcript': transcript_text(row['transcript']), 'recording_id': row['recording_id'],
                'recording_path': None, 'status': row['status'],
                'scenario': str(operator.get('script') or HH_SCRIPT) if operator.get('kind') != 'robot' else '',
                'model_lead_hint': False, 'journal': MANGO_SOURCE}
        if operator.get('kind') == 'robot':
            match = _robot_match(catalog, row['client_number'], row['call_started_at'])
            if match is not None:
                data = _json(match['data'], {})
                item['scenario'] = str(match['operator'] or '')
                item['model_lead_hint'] = str(match['outcome'] or '') in ('interested', 'lead')
                item['transcript'] = transcript_text(data.get('transcript')) or item['transcript']
                item['recording_path'] = data.get('recording') or match.get('recording')
        out.append(item)
    return out


async def _lead_index(pool, client_id: int) -> tuple[dict, bool]:
    """Карточки и человеческие отметки CRM по номеру. Модельные итоги сюда не входят."""
    index: dict[str, dict] = {}
    reference = False

    def add(number, *, base=True, confirmed=False, source='', ref=None):
        key = _phone_key(number)
        if not key:
            return
        item = index.setdefault(key, {'base': False, 'confirmed': False, 'sources': [], 'refs': []})
        item['base'] = item['base'] or base
        item['confirmed'] = item['confirmed'] or confirmed
        if source and source not in item['sources']:
            item['sources'].append(source)
        if ref is not None and len(item['refs']) < 5:
            item['refs'].append(ref)

    if await _has_table(pool, 'crm_leads'):
        for row in await pool.fetch('SELECT id, number, status FROM crm_leads WHERE client_id=$1', client_id):
            status = _norm(row['status'])
            add(row['number'], confirmed=status in CONFIRMED_LEAD_STATUSES, source='crm_leads', ref=row['id'])
            reference = True
    if await _has_table(pool, 'crm_outcomes'):
        for row in await pool.fetch('SELECT number, outcome FROM crm_outcomes WHERE client_id=$1', client_id):
            status = _norm(row['outcome'])
            add(row['number'], confirmed=status in CONFIRMED_LEAD_STATUSES, source='crm_outcomes')
            reference = True
    if await _has_table(pool, 'cc_leads'):
        for row in await pool.fetch('SELECT id, phone, attempts FROM cc_leads WHERE client_id=$1', client_id):
            attempts = _json(row['attempts'], [])
            confirmed = any(_norm(item.get('r')) == 'заинтересованный' for item in attempts if isinstance(item, dict))
            add(row['phone'], confirmed=confirmed, source='cc_leads', ref=row['id'])
            reference = True
    return index, reference


# Публичные имена этих же проверок для других разделов CRM: без копий и без второй трактовки.
has_table = _has_table
robot_owner = _robot_owner
lead_index = _lead_index


async def counts(pool, who, operator_id: str, start, end) -> dict:
    """Звонки и связанные с CRM лиды выбранного оператора за период."""
    if not can_manage(who):
        raise PermissionError('Раздел директора.')
    client_id = company_of(who)
    specs = await operators(pool, client_id)
    operator = operator_by_id(specs, operator_id)
    first, last = _as_date(start), _as_date(end)
    start_at, end_at = period_bounds(first, last)
    payload = {'operator': operator, 'configured': operator['configured'], 'period': period_label(first, last),
               'start': first.isoformat(), 'end': last.isoformat(), 'source': operator.get('journal'),
               'calls': 0, 'leads': 0, 'known_attribution': 0, 'unknown_attribution': 0, 'empty': ''}
    if not operator['configured']:
        payload['empty'] = operator['note']
        return payload
    calls = await _load_calls(pool, client_id, operator, start_at, end_at)
    if calls and all(str(item['ref']).startswith('robot:') for item in calls):
        payload['source'] = ROBOT_SOURCE
    index, _ = await _lead_index(pool, client_id)
    keys = {_phone_key(call['phone']) for call in calls if _phone_key(call['phone'])}
    payload['calls'] = len(calls)
    payload['leads'] = sum(1 for key in keys if (index.get(key) or {}).get('confirmed'))
    payload['known_attribution'] = sum(1 for key in keys if index.get(key))
    payload['unknown_attribution'] = sum(1 for key in keys if not index.get(key))
    if not calls:
        payload['empty'] = 'За выбранный период звонков этого оператора нет.'
    return payload


def _public_operator(operator: dict) -> dict:
    return {key: operator.get(key) for key in ('id', 'name', 'display', 'kind', 'journal', 'configured', 'source', 'note')}


async def overview(pool, who) -> dict:
    if not can_manage(who):
        raise PermissionError('Раздел директора.')
    await call_analysis_schema.ensure(pool)
    client_id = company_of(who)
    specs = await operators(pool, client_id)
    rows = await pool.fetch(
        """SELECT id, operator, extension, kind, source, period_start, period_end, status, stage, dedup_key,
                  total_calls, processed, saved_at, telegram_at, created_at, finished_at
           FROM call_analysis_runs WHERE client_id=$1 ORDER BY created_at DESC LIMIT 30""", client_id) \
        if await _has_table(pool, 'call_analysis_runs') else []
    target = await telegram_target(pool, who)
    today = datetime.now(MOSCOW).date()
    return {'operators': [_public_operator(item) for item in specs],
            'runs': [{'id': row['id'], 'operator': row['operator'], 'extension': row['extension'],
                      'kind': row['kind'], 'source': row['source'], 'status': row['status'], 'stage': row['stage'],
                      'period': period_label(row['period_start'], row['period_end']),
                      'total': row['total_calls'], 'processed': row['processed'],
                      'automatic': callcenter_monitor.script_for(row['dedup_key']) is not None,
                      'saved': bool(row['saved_at']), 'telegram': bool(row['telegram_at']),
                      'created_at': row['created_at'].isoformat(),
                      'finished_at': row['finished_at'].isoformat() if row['finished_at'] else None} for row in rows],
            'can_manage': True, 'can_telegram': bool(target),
            'default_start': (today - timedelta(days=6)).isoformat(), 'default_end': today.isoformat(),
            'max_days': MAX_DAYS}


async def start(pool, who, data: dict) -> dict:
    if not can_manage(who):
        raise PermissionError('Раздел директора.')
    await call_analysis_schema.ensure(pool)
    client_id = company_of(who)
    if client_id is None:
        raise ValueError('Компания не определена.')
    operator_id = str((data or {}).get('operator') or '').strip()
    specs = await operators(pool, client_id)
    operator = operator_by_id(specs, operator_id)
    if not operator['configured']:
        raise ValueError(operator['note'])
    first, last = _as_date((data or {}).get('start')), _as_date((data or {}).get('end'))
    start_at, end_at = period_bounds(first, last)
    key = dedup_key(client_id, operator['id'], operator['extension'], first, last)
    name = await author_name(pool, who)
    existing = await pool.fetchrow(
        'SELECT id, status FROM call_analysis_runs WHERE client_id=$1 AND dedup_key=$2', client_id, key)
    if existing:
        if existing['status'] == 'failed':  # повтор после сбоя: те же пункты, без дублей
            await pool.execute(
                """UPDATE call_analysis_runs SET status='queued', stage='Повтор после сбоя', error=NULL,
                     finished_at=NULL WHERE id=$1""", existing['id'])
            await pool.execute(
                """UPDATE call_analysis_items SET status='pending', error=NULL WHERE run_id=$1 AND status='failed'""",
                existing['id'])
            await pool.execute(
                """UPDATE call_analysis_runs SET processed=(SELECT count(*) FROM call_analysis_items
                     WHERE run_id=$1 AND status NOT IN ('pending','failed')) WHERE id=$1""", existing['id'])
            return {'id': existing['id'], 'reused': True, 'retried': True, 'status': 'queued'}
        return {'id': existing['id'], 'reused': True, 'status': existing['status']}
    calls = await _load_calls(pool, client_id, operator, start_at, end_at)
    source = ROBOT_SOURCE if calls and all(str(item['ref']).startswith('robot:') for item in calls) else operator['journal']
    actor = who.actor
    inserted = await pool.fetchval(
        """INSERT INTO call_analysis_runs(client_id, operator, extension, kind, source, period_start, period_end,
             period_start_at, period_end_at, dedup_key, status, stage, total_calls, author_tg, author_ext, author_name)
           VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'queued','Ждёт обработки',$11,$12,$13,$14)
           ON CONFLICT (client_id, dedup_key) DO NOTHING RETURNING id""",
        client_id, operator['id'], operator['extension'], operator['kind'], source,
        first, last, start_at, end_at, key, len(calls), getattr(actor, 'telegram_user_id', None) or None,
        getattr(actor, 'extension', None), name)
    if inserted is None:  # параллельный клик: берём уже созданный прогон
        row = await pool.fetchrow('SELECT id, status FROM call_analysis_runs WHERE client_id=$1 AND dedup_key=$2', client_id, key)
        return {'id': row['id'], 'reused': True, 'status': row['status']}
    for call in calls:
        await pool.execute(
            """INSERT INTO call_analysis_items(run_id, client_id, call_ref, call_id, phone, at, duration_s, status)
               VALUES($1,$2,$3,$4,$5,$6,$7,'pending') ON CONFLICT (run_id, call_ref) DO NOTHING""",
            inserted, client_id, call['ref'], call['call_id'] if isinstance(call['call_id'], int) else None,
            call['phone'], call['at'], call['duration'])
    if not calls:
        summary = summarize([], operator=_public_operator(operator), period=period_label(first, last))
        await pool.execute(
            """UPDATE call_analysis_runs SET status='done', stage='Звонков за период нет', result=$2::jsonb,
                 finished_at=now() WHERE id=$1""", inserted, json.dumps(summary, ensure_ascii=False))
    return {'id': inserted, 'reused': False, 'status': 'done' if not calls else 'queued'}


async def _load_one_call(pool, run, item) -> dict | None:
    ref = str(item['call_ref'])
    if ref.startswith('robot:') and await _has_table(pool, 'robot_calls'):
        owner = await _robot_owner(pool, run['client_id'])
        row = await pool.fetchrow(
            """SELECT call_id, phone, at, coalesce(duration_s,0) AS duration, operator, outcome, data, recording
               FROM robot_calls WHERE call_id=$1 AND owner=$2""", ref[6:], owner)
        if row is None:
            return None
        data = _json(row['data'], {})
        return {'phone': row['phone'], 'transcript': transcript_text(data.get('transcript')),
                'duration': int(row['duration'] or 0), 'recording_id': None,
                'recording_path': data.get('recording') or row.get('recording'),
                'scenario': str(row['operator'] or ''),
                'model_lead_hint': str(row['outcome'] or '') in ('interested', 'lead'),
                'call_id': row['call_id'], 'journal': ROBOT_SOURCE}
    try:
        call_id = int(ref.split(':', 1)[1])
    except (IndexError, ValueError):
        return None
    row = await pool.fetchrow(
        """SELECT id, client_number, call_started_at, coalesce(duration_seconds,0) AS duration,
                  transcript, recording_id, status
           FROM calls WHERE id=$1 AND client_id=$2""", call_id, run['client_id'])
    if row is None:
        return None
    call = {'phone': row['client_number'], 'transcript': transcript_text(row['transcript']),
            'duration': int(row['duration'] or 0), 'recording_id': row['recording_id'], 'recording_path': None,
            'scenario': str(run['operator'] if run['kind'] == 'robot' else (os.getenv('CALL_ANALYSIS_SCRIPT') or HH_SCRIPT)),
            'model_lead_hint': False, 'call_id': row['id'], 'journal': MANGO_SOURCE}
    if run['kind'] == 'robot':
        call['scenario'] = ''
        start = row['call_started_at'] - timedelta(minutes=10) if row['call_started_at'] else None
        end = row['call_started_at'] + timedelta(minutes=10) if row['call_started_at'] else None
        if start is not None:
            catalog = await _robot_catalog(pool, run['client_id'], start, end)
            match = _robot_match(catalog, row['client_number'], row['call_started_at'])
            if match is not None:
                data = _json(match['data'], {})
                call['scenario'] = str(match['operator'] or '')
                call['model_lead_hint'] = str(match['outcome'] or '') in ('interested', 'lead')
                call['transcript'] = transcript_text(data.get('transcript')) or call['transcript']
                call['recording_path'] = data.get('recording') or match.get('recording')
    return call


async def _transcribe(pool, run, call: dict, item) -> tuple[str, float | None, str]:
    """Достать расшифровку: сохранённую, из журнала робота или штатным Deepgram."""
    if call.get('transcript'):
        return str(call['transcript']), None, 'robot' if run['kind'] == 'robot' else 'existing'
    try:
        if run['kind'] == 'robot' and call.get('recording_path'):
            from .deepgram import transcribe
            text, _ = await transcribe(str(call['recording_path']))
            return text, None, 'deepgram'
        if run['kind'] == 'operator' and call.get('recording_id'):
            from crypto_util import decrypt
            import mango_client
            from .deepgram import transcribe_detailed
            client = await pool.fetchrow(
                'SELECT vpbx_api_key_enc, vpbx_api_salt_enc FROM clients WHERE id=$1', run['client_id'])
            audio = await mango_client.fetch_recording(decrypt(client['vpbx_api_key_enc']),
                                                       decrypt(client['vpbx_api_salt_enc']), call['recording_id'])
            text, _duration, confidence = await transcribe_detailed(audio)
            await pool.execute('UPDATE calls SET transcript=$2 WHERE id=$1 AND (transcript IS NULL OR transcript=\'\')',
                               call['call_id'], text)
            return text, confidence, 'deepgram'
    except Exception as exc:  # noqa: BLE001 — недоступное аудио видно отдельно, прогон не падает
        log.warning('разбор звонков: расшифровка %s не получена: %s', item['call_ref'], type(exc).__name__)
        return '', None, 'transcript_failed'
    return '', None, 'none'


async def _process_item(pool, run, item, lead_index: dict, has_reference: bool) -> dict:
    call = await _load_one_call(pool, run, item)
    if call is None:
        return {'status': 'missing_call', 'transcript_source': 'none', 'break': None, 'error': 'Звонок не найден'}
    text, confidence, source = await _transcribe(pool, run, call, item)
    status = 'pending'
    if source == 'transcript_failed':
        status = 'transcript_failed'
    elif not text:
        if (call.get('duration') or 0) < 8:
            status = 'done'  # короткий звонок: это качество базы, а не сбой записи
        else:
            status = 'audio_missing' if not call.get('recording_id') and not call.get('recording_path') else 'transcript_failed'
    else:
        status = 'done'
    monitored_script = callcenter_monitor.script_for(run['dedup_key'])
    if monitored_script:
        if confidence is None and source == 'existing':
            confidence = item['transcript_confidence']
        check = callcenter_quality.assess(split_lines(text), monitored_script, call['duration'],
                                          confidence is not None and confidence < 0.55)
        return {'status': status, 'phone': call.get('phone'), 'duration_s': call['duration'],
                'transcript_source': source, 'transcript_confidence': confidence,
                'stages': {'script_check': check}}
    scenario = str(call.get('scenario') or '')
    roles = {'Робот': 'operator', 'Клиент': 'client'} if run['kind'] == 'robot' else None
    result = classify_transcript(text, duration=call.get('duration') or 0, confidence=confidence,
                                 kind=run['kind'], scenario=scenario, roles=roles)
    apply_lead(result, lead_index.get(_phone_key(call.get('phone'))), has_reference)
    item_for_reason = {'status': status, 'phone': call.get('phone'), 'stages': result['stages'],
                       'low_confidence': result['low_confidence'], 'speaker_uncertain': result['speaker_uncertain'],
                       'hiring_topic': result['hiring_topic'],
                       'scenario_hh_compatible': result['scenario_hh_compatible'],
                       'answering_machine': (result['stages'].get('live_human') or {}).get('state') == 'no'
                       and 'автоответ' in _norm(result['stages'].get('live_human', {}).get('detail'))}
    reason = reason_for(item_for_reason)
    evidence = {key: value for key, value in result['stages'].items() if value.get('quote')}
    return {'status': status, 'phone': call.get('phone'), 'at': call.get('at'),
            'duration_s': call.get('duration'), 'transcript_source': source,
            'transcript_confidence': confidence, 'transcript_excerpt': _clean_quote(text, 500) or None,
            'stages': result['stages'], 'evidence': evidence, 'lead': result.get('lead'),
            'unknown_attribution': result.get('unknown_attribution', True),
            'low_confidence': result.get('low_confidence', False), 'speaker_uncertain': result.get('speaker_uncertain', True),
            'hiring_topic': result.get('hiring_topic', False),
            'scenario_hh_compatible': result.get('scenario_hh_compatible', True),
            'scenario': result.get('scenario'), 'break': reason, 'model_lead_hint': call.get('model_lead_hint', False),
            'error': None}


async def process(pool, run) -> None:
    run_id = run['id']
    monitored_script = callcenter_monitor.script_for(run['dedup_key'])
    await pool.execute("UPDATE call_analysis_runs SET status='running', started_at=coalesce(started_at,now()), stage='Читаю звонки' WHERE id=$1", run_id)
    lead_index, has_reference = ({}, False) if monitored_script else await _lead_index(pool, run['client_id'])
    items = await pool.fetch('SELECT * FROM call_analysis_items WHERE run_id=$1 AND status=$2 ORDER BY id', run_id, 'pending')
    processed = int(run['processed'] or 0)
    total = int(run['total_calls'] or 0)
    for item in items:
        try:
            result = await _process_item(pool, run, item, lead_index, has_reference)
        except Exception as exc:  # noqa: BLE001 — один звонок не должен ронять весь разбор
            log.exception('разбор звонков: элемент %s', item['id'])
            result = {'status': 'failed', 'transcript_source': 'none', 'break': None,
                      'error': f'{type(exc).__name__}: {str(exc)[:200]}'}
        await pool.execute(
            """UPDATE call_analysis_items SET status=$2, phone=coalesce($3,phone), at=coalesce($4,at),
                 duration_s=coalesce($5,duration_s), transcript_source=$6, transcript_confidence=$7,
                 transcript_excerpt=$8, stages=$9::jsonb, evidence=$10::jsonb, lead_source=$11, lead_id=$12,
                 lead_label=$13, unknown_attribution=$14, break_group=$15, break_code=$16, break_detail=$17,
                 model_lead_hint=$18, error=$19, processed_at=now() WHERE id=$1""",
            item['id'], result.get('status') or 'failed', result.get('phone'), result.get('at'),
            result.get('duration_s'), result.get('transcript_source'), result.get('transcript_confidence'),
            result.get('transcript_excerpt'), json.dumps(result.get('stages') or {}, ensure_ascii=False),
            json.dumps(result.get('evidence') or {}, ensure_ascii=False),
            ((result.get('lead') or {}).get('sources') or [None])[0],
            str(((result.get('lead') or {}).get('refs') or [None])[0]) if (result.get('lead') or {}).get('refs') else None,
            None, result.get('unknown_attribution', True),
            (result.get('break') or {}).get('group'), (result.get('break') or {}).get('code'),
            (result.get('break') or {}).get('label'), result.get('model_lead_hint', False), result.get('error'))
        processed += 1
        await pool.execute('UPDATE call_analysis_runs SET processed=$2, stage=$3 WHERE id=$1', run_id, processed,
                           f'Обработано {processed} из {total}' if total else 'Готово')
    rows = await pool.fetch('SELECT stages, evidence, status, transcript_source, transcript_confidence, phone, duration_s, '
                            'unknown_attribution, break_code, model_lead_hint FROM call_analysis_items WHERE run_id=$1', run_id)
    items_summary = []
    for row in rows:
        stages = _json(row['stages'], {})
        code = row['break_code']
        break_item = {'code': code, 'group': REASONS[code][0], 'label': REASONS[code][1]} if code in REASONS else None
        items_summary.append({'stages': stages, 'status': row['status'], 'transcript_source': row['transcript_source'],
                              'low_confidence': (row['transcript_confidence'] is not None and float(row['transcript_confidence']) < 0.55),
                              'phone': row['phone'], 'unknown_attribution': row['unknown_attribution'],
                              'model_lead_hint': row['model_lead_hint'], 'break': break_item,
                              'hiring_topic': any((stages.get(key) or {}).get('state') == 'yes'
                                                  for key in ('qualification', 'need'))})
    specs = await operators(pool, run['client_id'])
    try:
        operator = _public_operator(operator_by_id(specs, run['operator']))
    except ValueError:
        operator = {'id': run['operator'], 'name': run['operator'], 'display': run['operator']}
    period = period_label(run['period_start'], run['period_end'])
    if monitored_script:
        summary = {'operator': operator, 'period': period, 'total': len(rows), 'empty': not rows,
                   'quality': callcenter_quality.summarize([
                       {'phone': row['phone'], 'duration_s': row['duration_s'],
                        'script_check': _json(row['stages'], {}).get('script_check')} for row in rows
                   ], monitored_script, _phone_key)}
    else:
        summary = summarize(items_summary, operator=operator, period=period)
    await pool.execute(
        """UPDATE call_analysis_runs SET status='done', stage=$2, processed=$3, total_calls=$3,
             result=$4::jsonb, finished_at=now(), saved_at=CASE WHEN $5 THEN now() ELSE saved_at END WHERE id=$1""",
        run_id, f'Готово: звонков {summary["total"]}', summary['total'],
        json.dumps(summary, ensure_ascii=False), bool(monitored_script))


async def take(pool):
    return await pool.fetchrow(
        """UPDATE call_analysis_runs SET status='running', stage='Запуск', started_at=coalesce(started_at,now())
           WHERE id=(SELECT id FROM call_analysis_runs WHERE status='queued' ORDER BY created_at LIMIT 1
                     FOR UPDATE SKIP LOCKED) RETURNING *""")


async def loop(pool) -> None:
    """Фоновый разбор. После перезапуска продолжает незавершённые прогоны."""
    recovered, warned = False, False
    next_scan = 0.0
    while True:
        row = None
        try:
            await call_analysis_schema.ensure(pool)
            if not await _has_table(pool, 'call_analysis_runs'):
                if not warned:
                    log.warning('разбор звонков: нет таблиц, жду миграцию 045_call_analysis.sql')
                    warned = True
                await asyncio.sleep(60)
                continue
            warned = False
            if not recovered:
                await pool.execute("""UPDATE call_analysis_runs SET status='queued', stage='Продолжаю после перезапуска'
                                      WHERE status='running'""")
                recovered = True
            now = asyncio.get_running_loop().time()
            if now >= next_scan:
                next_scan = now + callcenter_monitor.INTERVAL
                try:
                    await callcenter_monitor.enqueue(pool)
                except Exception:
                    log.exception('авторазбор колл-центра: постановка звонков в очередь')
            row = await take(pool)
            if row is not None:
                await process(pool, row)
        except asyncio.CancelledError:
            if row is not None:
                await pool.execute("""UPDATE call_analysis_runs SET status='queued', stage='Продолжу после перезапуска'
                                      WHERE id=$1 AND status='running'""", row['id'])
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception('разбор звонков: очередь')
            if row is not None:
                await pool.execute(
                    """UPDATE call_analysis_runs SET status='failed', error=$2, stage='Ошибка', finished_at=now()
                       WHERE id=$1 AND status='running'""", row['id'], f'{type(exc).__name__}: {str(exc)[:200]}')
        await asyncio.sleep(POLL_SECONDS)


async def one(pool, who, run_id: int) -> dict:
    if not can_manage(who):
        raise PermissionError('Раздел директора.')
    await call_analysis_schema.ensure(pool)
    client_id = company_of(who)
    row = await pool.fetchrow('SELECT * FROM call_analysis_runs WHERE id=$1 AND client_id=$2', run_id, client_id)
    if row is None:
        raise ValueError('Такого разбора нет.')
    items = await pool.fetch('SELECT * FROM call_analysis_items WHERE run_id=$1 ORDER BY id LIMIT 500', run_id)
    result = _json(row['result'], None)
    specs = await operators(pool, client_id)
    try:
        operator = _public_operator(operator_by_id(specs, row['operator']))
    except ValueError:
        operator = {'id': row['operator'], 'name': row['operator'], 'display': row['operator']}
    if result is None:
        result = summarize([], operator=operator, period=period_label(row['period_start'], row['period_end']))
        result['empty'] = True
    target = await run_telegram_target(pool, row)
    return {'id': row['id'], 'operator': operator, 'status': row['status'], 'stage': row['stage'],
            'automatic': callcenter_monitor.script_for(row['dedup_key']) is not None,
            'source': row['source'], 'error': row['error'], 'period': period_label(row['period_start'], row['period_end']),
            'start': row['period_start'].isoformat(), 'end': row['period_end'].isoformat(),
            'total': row['total_calls'], 'processed': row['processed'], 'result': result,
            'saved': bool(row['saved_at']), 'saved_at': row['saved_at'].isoformat() if row['saved_at'] else None,
            'telegram': bool(row['telegram_at']), 'telegram_at': row['telegram_at'].isoformat() if row['telegram_at'] else None,
            'can_telegram': bool(target), 'author': row['author_name'],
            'created_at': row['created_at'].isoformat(), 'finished_at': row['finished_at'].isoformat() if row['finished_at'] else None,
            'items': [{'id': item['id'], 'call_id': item['call_id'], 'phone': item['phone'], 'at': item['at'].isoformat() if item['at'] else None,
                       'status': item['status'], 'source': item['transcript_source'],
                       'low_confidence': item['transcript_confidence'] is not None and float(item['transcript_confidence']) < 0.55,
                       'stages': _json(item['stages'], {}), 'break': {'code': item['break_code'],
                       'group': item['break_group'], 'label': item['break_detail']} if item['break_code'] else None,
                       'unknown_attribution': item['unknown_attribution'], 'error': item['error']}
                      for item in items]}


async def save(pool, who, run_id: int) -> dict:
    if not can_manage(who):
        raise PermissionError('Раздел директора.')
    await call_analysis_schema.ensure(pool)
    client_id = company_of(who)
    row = await pool.fetchrow('SELECT result, status FROM call_analysis_runs WHERE id=$1 AND client_id=$2', run_id, client_id)
    if row is None:
        raise ValueError('Такого разбора нет.')
    if row['status'] != 'done':
        raise ValueError('Разбор ещё не закончен.')
    await pool.execute('UPDATE call_analysis_runs SET saved_at=now(), saved_by=$2 WHERE id=$1', run_id,
                       await author_name(pool, who))
    return {'id': run_id, 'saved': True}


async def send_telegram(pool, who, run_id: int, sender=None) -> dict:
    """Явная отправка сводки. Адресат — только личная привязка владельца сессии."""
    if not can_manage(who):
        raise PermissionError('Раздел директора.')
    await call_analysis_schema.ensure(pool)
    client_id = company_of(who)
    row = await pool.fetchrow('SELECT * FROM call_analysis_runs WHERE id=$1 AND client_id=$2', run_id, client_id)
    if row is None:
        raise ValueError('Такого разбора нет.')
    if row['status'] != 'done':
        raise ValueError('Разбор ещё не закончен.')
    target = await run_telegram_target(pool, row)
    if target is None:
        raise PermissionError('У учётной записи, запустившей разбор, нет действующей личной привязки Telegram. '
                              'Кнопка отправки недоступна.')
    if row['telegram_at']:
        return {'sent': True, 'already': True, 'at': row['telegram_at'].isoformat()}
    summary = _json(row['result'], {})
    text = telegram_text(summary)
    sender = sender or _send_telegram_message
    try:
        ok = await sender(target, text)
    except Exception as exc:  # noqa: BLE001 — сбой Telegram не должен выглядеть успехом
        log.warning('разбор звонков: Telegram не ответил: %s', type(exc).__name__)
        ok = False
    if not ok:
        await pool.execute('UPDATE call_analysis_runs SET telegram_error=$2 WHERE id=$1', run_id, 'Telegram не принял сообщение')
        raise ValueError('Telegram не принял сообщение. Проверьте привязку и попробуйте позже.')
    await pool.execute(
        """UPDATE call_analysis_runs SET telegram_at=now(), telegram_by=$2, telegram_error=NULL WHERE id=$1""",
        run_id, target)
    return {'sent': True, 'already': False}


async def _send_telegram_message(chat_id: int, text: str) -> bool:
    from . import telegram
    return await telegram.send(chat_id, text)
