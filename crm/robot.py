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


async def overview(who) -> dict:
    check(who)
    from voice import speech
    from voice.web import _current_voice
    if os.getenv('ELEVENLABS_API_KEY') and not speech._eleven_direct:
        try:
            await speech.load_eleven_voices()
        except Exception:
            pass
    return {'up': await robot_up(), 'operators': operators(), 'test_phones': test_phones(),
            'settings': _current_voice(), 'voices': speech._eleven_direct,
            'can_save': bool(speech.SETTINGS_FILE) and DATA.is_dir(), 'readonly': who.test}


async def save_settings(who, data) -> dict:
    check(who, write=True)
    from voice import speech
    if not isinstance(data, dict):
        raise ValueError('Неверные настройки.')
    if not DATA.is_dir():
        raise ValueError('Робот не установлен на этом сервере.')
    return {'saved': speech.save_settings(data)}


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
