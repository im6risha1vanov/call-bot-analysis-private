"""Режим промта в CRM: то же, что кнопка «✏️ Режим промта» в боте.

Хранение, версии и применение общие с ботом (review/prompts.py), поэтому
сохранение на сайте сразу действует и в боте, и наоборот. Править может тот, кто
вошёл по своей личной ссылке директора, привязанной к его Telegram, и есть в
PROMPT_EDITORS. Черновик живёт на странице, а не в prompt_drafts: иначе бот
принимал бы обычные сообщения этого человека за правки промта.

Сохранить можно только текст, по которому уже собран пример: сервер выдаёт к
примеру подпись этого текста и проверяет её при сохранении. Разбор звонка для всех,
кроме того, сохраняется только после проверки на звонках (review/prompt_check.py),
у неё своя подпись.
"""
from __future__ import annotations

import difflib
import hashlib
import hmac
import secrets

from tools import Forbidden
from review import prompt_check, prompts
from . import journal, telegram

_KEY = secrets.token_bytes(32)  # подпись примера; после перезапуска пример просто собирают заново
SOURCE = {'personal': 'ваша личная версия', 'common': 'общая версия', 'default': 'исходная версия'}
HINTS = {
    'review': 'Личная версия меняет только полную копию разбора, которая приходит вам (один дополнительный запрос '
              'к модели на звонок). Версия «для всех» меняет разборы всех звонков, в том числе менеджерам. '
              'Формат ответа модели правится только в коде.',
}


def editor(who) -> int:
    """Telegram ID редактора или Forbidden с понятной причиной."""
    if who.role != 'director' or who.actor is None:
        raise Forbidden('Раздел директора.')
    tg = who.actor.telegram_user_id
    if not tg:
        raise Forbidden('Промты правятся под вашим Telegram. Создайте себе в «Доступах» учётную запись руководителя, '
                        'привязанную к вам, и войдите под ней.')
    if not prompts.is_editor(tg):
        raise Forbidden('Режим промта вам не подключён.')
    return tg


def kind_of(value) -> str:
    if value not in prompts.KINDS:
        raise ValueError('Неизвестный промт.')
    return value


def sign(tg: int, kind: str, body: str) -> str:
    return hmac.new(_KEY, f'{tg}|{kind}|{hashlib.sha256(body.encode()).hexdigest()}'.encode(), 'sha256').hexdigest()


def clean_body(body) -> str:
    body = str(body or '').strip()
    if not body or len(body) > prompts.MAX_BODY:
        raise ValueError('Пустой или слишком длинный текст промта.')
    return body


def diff(old: str, new: str, limit: int = 40) -> list[dict]:
    out = []
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm='', n=0):
        if line.startswith(('---', '+++', '@@')) or not line[1:].strip():
            continue
        out.append({'op': line[0], 'text': line[1:].strip()})
    return out[:limit]


async def names(pool, client_id: int) -> dict[int, str]:
    rows = await pool.fetch('SELECT telegram_user_id, full_name FROM employees WHERE client_id=$1 AND telegram_user_id IS NOT NULL',
                            client_id)
    return {r['telegram_user_id']: r['full_name'] or str(r['telegram_user_id']) for r in rows}


def when(value) -> str | None:
    return value.strftime('%Y-%m-%dT%H:%M:%S%z') if value else None


async def overview(pool, who) -> dict:
    tg = editor(who)
    await prompts.seed(pool, who.actor.client_id)
    kinds = []
    for kind in prompts.KINDS:
        _, source, version = await prompts.resolve(pool, kind, tg, who.actor.client_id)
        kinds.append({'kind': kind, 'title': prompts.TITLES[kind], 'source': SOURCE[source], 'version': version})
    return {'kinds': kinds}


async def state(pool, who, kind: str) -> dict:
    tg = editor(who)
    kind = kind_of(kind)
    body, source, version = await prompts.resolve(pool, kind, tg, who.actor.client_id)
    mine = await prompts.active(pool, kind, tg, who.actor.client_id)
    common = await prompts.active(pool, kind, 0, who.actor.client_id)
    people = await names(pool, who.actor.client_id)
    versions = [{
        'id': v['id'], 'at': when(v['created_at']), 'scope': 'для всех' if v['owner_tg'] == 0 else 'личная',
        'who': people.get(v['created_by'], 'бот') if v['created_by'] else 'бот', 'note': v['note'] or '',
        'mine': bool(mine and mine['id'] == v['id']), 'common': bool(common and common['id'] == v['id']),
    } for v in await prompts.versions(pool, kind, tg, limit=20, company=who.actor.client_id)]
    return {'kind': kind, 'title': prompts.TITLES[kind], 'body': body, 'source': SOURCE[source], 'version': version,
            'personal': mine is not None, 'hint': HINTS.get(kind), 'versions': versions,
            'check_calls': prompt_check.CALLS if kind == 'review' else None}


async def edit(pool, who, kind: str, body, history, ask) -> dict:
    """Шаг правки обычными словами: модель меняет текст, сервер считает, что поменялось."""
    tg = editor(who)
    kind, body = kind_of(kind), clean_body(body)
    ask = str(ask or '').strip()
    if not ask or len(ask) > 2000:
        raise ValueError('Напишите, что поменять, до 2000 символов.')
    history = [{'ask': str(h.get('ask', ''))[:500], 'reply': str(h.get('reply', ''))[:500]}
               for h in (history if isinstance(history, list) else []) if isinstance(h, dict)][-12:]
    result, cost = await prompts.edit(kind, body, history, ask)
    await prompts.add_spend(pool, who.actor.client_id, cost)
    new = result['prompt']
    return {'reply': result['reply'], 'prompt': new, 'changes': result['changes'] or (ask[:200] if new else None),
            'diff': diff(body, new) if new else []}


async def preview(pool, who, kind: str, body) -> dict:
    """Пример по тексту: разбор последнего звонка или отчёт, как в боте. До минуты."""
    tg = editor(who)
    kind, body = kind_of(kind), clean_body(body)
    from review.prompt_ui import preview as run_preview
    text, problem, call_id = await run_preview(pool, None, who.actor, kind, body)
    return {'html': journal.html(text) if text else None, 'problem': problem, 'call_id': call_id,
            'token': sign(tg, kind, body)}


async def check(pool, who, kind: str, body) -> dict:
    """Проверка разбора на последних звонках: старый общий текст против нового. 2–4 минуты."""
    tg = editor(who)
    kind, body = kind_of(kind), clean_body(body)
    if kind != 'review':
        raise ValueError('Проверка на звонках есть только у разбора звонка.')
    report = await prompt_check.check(pool, who.actor.client_id, body)
    report['verdict'], report['level'] = prompt_check.verdict(report), prompt_check.level(report)
    report['token'] = None if report['broken'] else sign(tg, 'check:' + kind, body)
    return report


async def tell_others(pool, who, kind: str, version: int, note: str | None) -> None:
    """Как в боте: остальным редакторам — что общий промт поменялся."""
    tg = who.actor.telegram_user_id
    name = (await names(pool, who.actor.client_id)).get(tg, 'Коллега')
    for chat in prompts.editors():
        if chat == tg:
            continue
        mine = await prompts.active(pool, kind, chat, who.actor.client_id)
        tail = ' У вас своя личная версия, она остаётся; общую можно включить в «Сохранения».' if mine else ''
        await telegram.send(chat, f'{name} изменил промт «{prompts.TITLES[kind]}» для всех '
                                  f'(сохранение #{version}, в CRM): {note or "без описания"}.{tail}')


async def save(pool, who, kind: str, body, note, scope: str, token, check_token=None) -> dict:
    tg = editor(who)
    kind, body = kind_of(kind), clean_body(body)
    if scope not in ('me', 'all'):
        raise ValueError('Неизвестно, для кого сохранить.')
    if not token or not hmac.compare_digest(str(token), sign(tg, kind, body)):
        raise ValueError('Сначала посмотрите пример по этому тексту.')
    if kind == 'review' and scope == 'all' and not (
            check_token and hmac.compare_digest(str(check_token), sign(tg, 'check:' + kind, body))):
        raise ValueError(f'Разбор для всех сохраняется после проверки на {prompt_check.CALLS} звонках.')
    note = str(note or '').strip()[:300] or None
    version = await prompts.save(pool, kind, body, note, tg, scope, who.actor.client_id)
    if scope == 'all':
        await tell_others(pool, who, kind, version, note)
    return {'version': version}


async def version_body(pool, who, version_id: int) -> dict:
    tg = editor(who)
    row = await prompts.version(pool, version_id, who.actor.client_id)
    if row is None or row['owner_tg'] not in (0, tg):
        raise ValueError('Сохранение не найдено.')
    return {'id': row['id'], 'kind': row['kind'], 'body': row['body'], 'note': row['note'] or ''}


async def use(pool, who, version_id: int, scope: str) -> dict:
    tg = editor(who)
    row = await prompts.version(pool, version_id, who.actor.client_id)
    if row is None:
        raise ValueError('Сохранение не найдено.')
    await prompts.activate(pool, row['kind'], version_id, tg, scope, who.actor.client_id)
    if scope == 'all':
        await tell_others(pool, who, row['kind'], version_id, f'возврат к сохранению #{version_id}')
    return {'kind': row['kind']}


async def drop_personal(pool, who, kind: str) -> None:
    await prompts.drop_personal(pool, kind_of(kind), editor(who), who.actor.client_id)
