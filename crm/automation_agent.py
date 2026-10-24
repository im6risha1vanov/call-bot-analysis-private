"""Помощник конструктора автоматизаций: «Что вы хотите сделать?» → черновик сценария.

Модель видит только каталог (события, поля, действия, подключения компании) и отвечает JSON-ом:
{"reply": "текст человеку", "scenario": {"name": ..., "spec": {...}} | null}. Сценарий проверяет
automations.validate, как и ручной: при ошибке модель один раз получает её и исправляется.
Помощник ничего не сохраняет и не включает: черновик открывается в редакторе, дальше решает человек
(сохранить, пробный запуск, включить). Модели и цепочка запасных — те же, что у AveronGPT.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

from . import automation_blocks as blocks, automations, gpt

log = logging.getLogger(__name__)
MAX_TEXT = 1500
HISTORY = 8  # сколько прошлых реплик разговора берём
TRIES = 2  # вторая попытка — с ошибкой проверки
JSON_BLOCK = re.compile(r'\{.*\}', re.S)

RULES = """Ты помощник конструктора автоматизаций в CRM Averon. Человек пишет, что хочет автоматизировать,
ты собираешь сценарий «событие → условия → действия» ТОЛЬКО из каталога ниже. Код не пишешь и не предлагаешь.

Отвечай строго одним JSON-объектом без пояснений вокруг:
{"reply": "коротко человеку, 1–3 предложения", "scenario": {"name": "...", "spec": {"trigger": {...}, "if": [...], "actions": [...]}}}
или, если собрать нельзя или не хватает важного, {"reply": "один короткий уточняющий вопрос или что сейчас можно", "scenario": null}.

Правила:
- trigger.type только из списка событий; для schedule.daily нужен "time": "ЧЧ:ММ".
- Условие: {"field": "путь поля события", "op": одна из операций, "value": значение}; для empty/not_empty без value.
  Для полей со списком значений бери ключ из списка (например outcome_code "meeting", success "да").
- Действия: telegram.send {"account": id чата, "text"}, sheets.append {"account": id таблицы, "row": [значения столбцов]},
  email.send {"to": [адреса], "subject", "text"}, webhook.send {"url": "https://..."}.
- В тексты подставляй поля события в фигурных скобках: {deal.amount}. Других полей нет.
- Подключение бери из списка подключений по смыслу; если нужного вида нет, поставь "account": null и в reply
  скажи подключить его кнопкой «Подключить Telegram» или «Подключить Google-таблицу».
- Чего нет в каталоге, напрямую не умеем. amoCRM, Битрикс24, 1С и свои сайты принимают входящие вебхуки: для них
  собери webhook.send с пустым url и скажи взять адрес входящего вебхука в настройках той системы.
  Notion, SMS, WhatsApp и звонки входящих вебхуков не принимают: scenario null, честно скажи, что пока нельзя,
  готовые блоки появятся позже, и предложи, что можно сейчас (Telegram, таблица, почта).
- Если человек не сказал, куда отправлять, а чат один — бери его, не спрашивай. Спрашивай только то, без чего
  сценарий будет неправильным.
- Если просьба размытая («сделай что-нибудь полезное»), scenario null: предложи 2–3 варианта из примеров
  и спроси, какой нужен.
- Пиши безлично, без рода: «Сценарий готов», «Собран черновик», а не «Создал» или «Настроил».
- Название сценария короткое, по-русски. Тексты сообщений короткие и понятные.
- В reply не пересказывай JSON, скажи по-человечески, что получилось, и что дальше: проверить в редакторе,
  сохранить и сделать пробный запуск."""


def catalog_text(cat: dict, accounts: list[dict]) -> str:
    labels, choices = cat['labels'], cat['choices']
    lines = ['События (trigger.type: название; поля):']
    for key, t in cat['triggers'].items():
        fields = ', '.join(f'{f} ({labels.get(f, f)})' for f in t['fields'])
        lines.append(f'- {key}: {t["title"]}; поля: {fields}')
    lines.append('Операции условий: ' + ', '.join(f'{k} ({v})' for k, v in cat['op_labels'].items()))
    lines.append('Поля со списком значений: ' + '; '.join(
        f'{f}: ' + ', '.join(f'{k} = {v}' for k, v in opts.items()) for f, opts in choices.items()))
    lines.append('Действия: ' + '; '.join(f'{k} ({v["title"]})' for k, v in cat['actions'].items()))
    ready = [a for a in accounts if a['status'] == 'ready']
    lines.append('Подключения компании: ' + ('; '.join(f'id {a["id"]}: {a["kind"]}, «{a["title"]}»' for a in ready)
                                              if ready else 'пока нет ни одного'))
    if not cat['email_ready']:
        lines.append('Почта CRM не настроена: письма собрать можно, но уйдут они только после настройки.')
    lines.append('Примеры готовых сценариев:')
    for t in cat['templates'][:6]:
        lines.append(f'- {t["title"]}: ' + json.dumps(t['spec'], ensure_ascii=False))
    return '\n'.join(lines)


def parse(text: str) -> dict:
    """JSON из ответа модели; модель ответила просто текстом — это реплика без сценария."""
    m = JSON_BLOCK.search(text or '')
    if m:
        try:
            data = json.loads(m[0])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return {'reply': (text or '').strip(), 'scenario': None}


def check(scenario, accounts: dict[int, dict]) -> tuple[dict, list[str]]:
    """Проверенный черновик и виды подключений, которых не хватает. ValueError — исправлять модели."""
    if not isinstance(scenario, dict) or not isinstance(scenario.get('spec'), dict):
        raise ValueError('scenario должен быть объектом с name и spec.')
    spec = json.loads(json.dumps(scenario['spec']))
    missing, stand_in = [], dict(accounts)
    for a in spec.get('actions') or []:
        kind = blocks.ACTIONS.get(a.get('type') if isinstance(a, dict) else None, {}).get('account')
        if not kind:
            continue
        acc = accounts.get(a.get('account')) if isinstance(a.get('account'), int) else None
        if acc is None or acc['kind'] != kind or acc['status'] != 'ready':
            same = [x for x in accounts.values() if x['kind'] == kind and x['status'] == 'ready']
            if same:
                a['account'] = same[0]['id']
            else:  # подключения ещё нет: проверяем с заглушкой, в черновике пусто
                missing.append(kind)
                stand_in[-1 - len(missing)] = {'id': -1 - len(missing), 'kind': kind, 'title': '', 'status': 'ready'}
                a['account'] = -1 - len(missing)
    no_url = set()
    for i, a in enumerate(spec.get('actions') or []):
        if isinstance(a, dict) and a.get('type') == 'webhook.send' and not a.get('url'):
            no_url.add(i)
            a['url'] = 'https://example.ru/hook'  # адрес человек впишет в редакторе
    clean = automations.validate(spec, stand_in)
    for i, a in enumerate(clean['actions']):
        if a.get('account') is not None and a['account'] < 0:
            a['account'] = None
        if i in no_url:
            a['url'] = ''
    name = str(scenario.get('name') or '').strip()[:120] or automations.TRIGGERS[clean['trigger']['type']]['title']
    return {'name': name, 'spec': clean}, sorted(set(missing))


async def ask(pool, client_id: int, text: str, history: list | None = None) -> dict:
    text = str(text or '').strip()
    if not text:
        raise ValueError('Напишите, что хотите сделать.')
    if len(text) > MAX_TEXT:
        raise ValueError('Слишком длинно, опишите короче.')
    overview = await automations.overview(pool, client_id)
    accounts = {a['id']: a for a in overview['accounts']}
    system = RULES + '\n\n' + catalog_text(overview['catalog'], overview['accounts'])
    turns = []
    for h in (history or [])[-HISTORY:]:
        if isinstance(h, dict) and h.get('role') in ('user', 'assistant') and str(h.get('text') or '').strip():
            turns.append({'role': h['role'], 'text': str(h['text'])[:MAX_TEXT]})
    turns.append({'role': 'user', 'text': text})
    spent, data, draft, missing, error = 0.0, {}, None, [], None
    for _ in range(TRIES):
        try:
            answer, _model, rub = await asyncio.wait_for(gpt.think(pool, None, system, turns, []), gpt.TIMEOUT * 2)
        except (gpt.Unavailable, asyncio.TimeoutError) as e:
            log.warning('помощник автоматизаций не ответил: %r', e)
            return {'reply': 'Сейчас не получилось ответить: модель не отвечает. Попробуйте через минуту.', 'draft': None}
        spent += rub
        data = parse(answer)
        if not data.get('scenario'):
            break
        try:
            draft, missing = check(data['scenario'], accounts)
            error = None
            break
        except ValueError as e:
            error = str(e)
            turns += [{'role': 'assistant', 'text': answer},
                      {'role': 'user', 'text': f'Проверка сценария не прошла: {error} Исправь и ответь тем же JSON.'}]
    if spent:
        await gpt.add_spend(pool, f'automations:{client_id}', spent)
    reply = str(data.get('reply') or '').strip()[:1500]
    if error:
        reply = 'Не получилось собрать сценарий без ошибок. Опишите по-другому или начните с шаблона ниже.'
    out = {'reply': reply or ('Готово, проверьте черновик.' if draft else 'Опишите подробнее, что должно происходить.'),
           'draft': draft, 'missing': missing}
    if draft:
        out['text'] = automations.describe(draft['spec'], {k: v for k, v in accounts.items()})
    return out
