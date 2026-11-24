"""Голосовой робот в CRM директора: звонок роботом, голос и манера речи, итоги звонков.

Сам робот работает отдельно (voice.sip, сервис callbot-voice-sip из
scripts/voice_test.sh sip) и принимает заказ звонка на 127.0.0.1:VOICE_SIP_API_PORT.
Голос и манеру он читает из файла настроек перед каждым звонком; CRM пишет в
тот же файл, что и страница /voice, поэтому перезапуск не нужен. Итоги звонков
робот дописывает в JSONL (VOICE_LOG), отсюда CRM их показывает.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from collections import deque
from pathlib import Path

import httpx

from tools import Forbidden
from .dial import normalize

DATA = Path(os.getenv('VOICE_DATA_DIR', '/opt/callbot-voice-data'))
# До импорта voice.speech: он читает пути при загрузке.
os.environ.setdefault('VOICE_SETTINGS_FILE', str(DATA / 'voice-settings.json'))
os.environ.setdefault('VOICE_TTS_CACHE', str(DATA / 'tts-cache'))
CALLS_LOG = Path(os.getenv('VOICE_LOG_FILE', str(DATA / 'calls.jsonl')))
DIAL_GAP_S = 20  # не чаще одного звонка в 20 секунд: случайный двойной клик не звонит дважды
OUTCOMES = {'interested': 'Заинтересованный', 'callback': 'Перезвон', 'not_relevant': 'Неактуально', 'ndz': 'Не взял трубку',
            'do_not_call': 'Просил не звонить'}
_last_dial = {'at': 0.0}
_tasks: set = set()  # фоновые пересчёты цен: ссылка, чтобы задачу не собрал сборщик мусора


def check(who, write: bool = False) -> None:
    if who.role != 'director':
        raise Forbidden('Раздел директора.')
    if write and who.test:
        raise Forbidden('Тестовый вход только для просмотра.')


def test_phones() -> list[str]:
    return [p.strip() for p in os.getenv('VOICE_TEST_PHONES', '79042031766').split(',') if p.strip()]


def api_port() -> int:
    return int(os.getenv('VOICE_SIP_API_PORT', '8092'))


async def robot_up() -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection('127.0.0.1', api_port()), 2)
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    return True


def operators() -> list[dict]:
    """Операторы нашего кабинета: встроенные и заведённые директором в кабинете робота."""
    from voice.operator import OperatorError, all_ids, load
    out = []
    for op_id in all_ids():
        try:
            op = load(op_id)
        except OperatorError:
            continue
        if not op.owner:
            out.append({'id': op.id, 'name': op.name, 'company': op.company})
    return out


def preset_list(costs: dict, v4_price: float) -> list[dict]:
    """Наборы робота для выбора в CRM: что внутри, цена синтеза, оценка минуты и чем отличаются."""
    from voice import presets
    return [{'key': key, 'name': ps['name'], 'llm': presets.LLM[ps['llm']]['name'], 'tts': presets.TTS[ps['tts']]['name'],
             'tts_price': round(presets.tts_rub_1k(ps['tts'], v4_price), 2) if v4_price or ps['tts'] == 'yandex' else None,
             'cost_min': costs.get(key), 'quality': ps['quality']}
            for key, ps in presets.PRESETS.items()]


async def overview(who, pool=None) -> dict:
    check(who)
    from voice import presets, speech
    from voice.web import _current_voice
    from . import robot_store
    if os.getenv('ELEVENLABS_API_KEY') and not speech._eleven_direct:
        try:
            await speech.load_eleven_voices()
        except Exception:
            pass
    return {'up': await robot_up(), 'operators': operators(), 'test_phones': test_phones(),
            'settings': _current_voice(), 'voices': speech._eleven_direct,
            'can_save': bool(speech.SETTINGS_FILE) and DATA.is_dir(), 'readonly': who.test,
            'presets': preset_list((await robot_store.economics(pool))['presets'] if pool is not None else {},
                                   (await robot_store.prices(pool))['eleven_1k_chars'] if pool is not None else 0),
            'preset': presets.current() or presets.DEFAULT}


async def save_settings(who, data, pool=None) -> dict:
    check(who, write=True)
    from voice import speech
    if not isinstance(data, dict):
        raise ValueError('Неверные настройки.')
    if not DATA.is_dir():
        raise ValueError('Робот не установлен на этом сервере.')
    before = speech.live_settings().get('preset')
    saved = speech.save_settings(data)
    if pool is not None and saved.get('preset') != before:  # другой набор: цены модели и синтеза пересчитать сразу
        from . import robot_prices
        task = asyncio.get_running_loop().create_task(robot_prices.refresh(pool))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    return {'saved': saved}


async def preview(who, data) -> tuple[bytes, int]:
    """Фраза несохранёнными настройками страницы; ответ — сырой PCM 16 бит и частота."""
    check(who)
    from voice import speech
    settings = speech.clean_settings(data.get('settings') if isinstance(data.get('settings'), dict) else {})
    text = str(data.get('text') or '').strip()[:400]
    if not settings.get('voice_id') or not text:
        raise ValueError('Нужны голос и текст.')
    speech.settings_override.set(settings)
    voice = speech.Voice('11:' + settings['voice_id'], '', settings.get('speed', 1.0))
    try:
        pcm = await speech.synthesize_pcm(text, voice)
    except Exception as exc:
        raise ValueError(f'Синтез не получился: {str(exc)[:200]}') from exc
    return pcm, speech.TTS_RATE


async def voices_search(who, query: str, gender: str, use_case: str, page) -> dict:
    """Поиск русских голосов в библиотеке ElevenLabs."""
    check(who)
    from voice import speech
    try:
        return await speech.search_library(query or '', gender or '', use_case or '', int(page or 0))
    except (speech.ElevenError, httpx.HTTPError) as exc:
        raise ValueError(f'Поиск голосов не получился: {exc}') from exc


async def voices_add(who, data) -> dict:
    """Голос из библиотеки в аккаунт ElevenLabs; после этого его можно прослушать и сохранить для звонков."""
    check(who, write=True)
    from voice import speech
    try:
        voice_id = await speech.add_library_voice(str(data.get('owner') or ''), str(data.get('voice_id') or ''),
                                                  str(data.get('name') or ''))
    except (speech.ElevenError, httpx.HTTPError) as exc:
        raise ValueError(f'Голос не добавился: {exc}') from exc
    return {'voice_id': voice_id, 'voices': speech._eleven_direct}


async def voices_preview(who, url: str) -> tuple[bytes, str]:
    check(who)
    from voice import speech
    try:
        return await speech.library_preview(str(url or ''))
    except (speech.ElevenError, httpx.HTTPError) as exc:
        raise ValueError(str(exc)) from exc


CLONE_MAX_BYTES = 20 * 1024 * 1024


async def voices_clone(who, name: str, files: list[tuple[str, bytes]]) -> dict:
    """Клон голоса по записям одного человека (только с его согласия)."""
    check(who, write=True)
    from voice import speech
    if not files:
        raise ValueError('Приложите записи голоса.')
    try:
        voice_id = await speech.clone_voice(name, files, 'Клон из CRM')
    except (speech.ElevenError, httpx.HTTPError) as exc:
        raise ValueError(f'Клон не получился: {exc}') from exc
    return {'voice_id': voice_id, 'voices': speech._eleven_direct}


async def call(who, phone, operator) -> dict:
    check(who, write=True)
    number = normalize(phone)
    if not number:
        raise ValueError('Неверный номер.')
    known = {o['id'] for o in operators()}
    operator = str(operator or 'kitchen')
    if operator not in known:
        raise ValueError('Нет такого сценария робота.')
    if time.monotonic() - _last_dial['at'] < DIAL_GAP_S:
        raise ValueError('Робот только что звонил. Подождите 20 секунд.')
    _last_dial['at'] = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(f'http://127.0.0.1:{api_port()}/dial', json={'phone': number, 'operator': operator})
    except httpx.HTTPError:
        _last_dial['at'] = 0.0
        raise ValueError('Робот сейчас выключен. Включается на сервере: bash scripts/voice_test.sh sip')
    data = r.json() if r.headers.get('content-type', '').startswith('application/json') else {}
    if r.status_code >= 300:
        _last_dial['at'] = 0.0
        raise ValueError('Робот не позвонил: ' + str(data.get('error') or r.status_code))
    return {'id': data.get('id'), 'number': number}


def _calls_tail(limit: int) -> list[dict]:
    if not CALLS_LOG.is_file():
        return []
    with CALLS_LOG.open(encoding='utf-8') as f:
        lines = deque(f, maxlen=limit * 3)
    out = []
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get('mode') == 'телефон':  # браузерные тесты и автотесты не показываем
            out.append(rec)
        if len(out) >= limit:
            break
    return out


async def calls(who, limit: int = 30) -> list[dict]:
    check(who)
    rows = await asyncio.to_thread(_calls_tail, limit)
    return [{
        'at': r.get('at'), 'phone': r.get('phone'), 'operator': r.get('operator'),
        'outcome': OUTCOMES.get(r.get('outcome'), r.get('outcome') or ''), 'good': r.get('outcome') in ('interested', 'callback'),
        'transfer': bool(r.get('transfer')), 'comment': r.get('comment') or '', 'seconds': r.get('duration_s'),
        'facts': r.get('facts') or {}, 'error': r.get('error'),
        'transcript': [{'who': 'Робот' if who_ == 'robot' else 'Клиент' if who_ == 'client' else who_, 'text': text}
                       for who_, text in (r.get('transcript') or [])],
    } for r in rows]
