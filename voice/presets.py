"""Готовые наборы робота: модель ответов (Яндекс AI Studio) и синтез (ElevenLabs или Яндекс).

Директор выбирает набор в CRM («Робот → Голос»), выбор лежит в файле настроек голоса (ключ preset)
и действует со следующего звонка. Без выбора робот работает как раньше: модель из VOICE_MODEL,
синтез из настройки model.

Цены моделей — прайс AI Studio на 21.10.2026, ₽ за 1000 токенов с НДС: обычный вход, повторный
(кешированный) вход, ответ. Кеш Яндекс включает сам и не гарантирует, поэтому оценки CRM берут
цену обычного входа. https://aistudio.yandex.ru/ru/docs/ai-studio/pricing
Синтез: у ElevenLabs v4 Turbo вдвое дешевле v4 за символ (https://elevenlabs.io/pricing/api).
"""
from __future__ import annotations

import os

LLM = {
    'yandexgpt-5.1': {'name': 'YandexGPT Pro 5.1', 'price': (0.8, 0.8, 0.8)},
}
# Синтез: у ElevenLabs цена считается от v4 (factor), у Яндекса — своя цена (rub_1k).
# SpeechKit v3 берёт 0,1626 ₽ за фразу до 250 символов; фраза робота в среднем ~60 символов,
# поэтому ≈ 2,7 ₽ за 1000 символов. https://aistudio.yandex.ru/ru/docs/speechkit/pricing
YANDEX_PHRASE_RUB, YANDEX_PHRASE_CHARS = 0.1626, 60
TTS = {
    'eleven_v4': {'name': 'ElevenLabs v4', 'factor': 1.0},
    'eleven_v4_turbo': {'name': 'ElevenLabs v4 Turbo', 'factor': 0.5},
    'yandex': {'name': 'Яндекс SpeechKit', 'rub_1k': round(YANDEX_PHRASE_RUB / YANDEX_PHRASE_CHARS * 1000, 2)},
}
# 21.10 Григорий: модель ответов везде YandexGPT (дешёвые модели в автотесте выдумывали данные),
# наборы отличаются только голосом.
PRESETS = {
    'quality': {'name': 'Качество', 'llm': 'yandexgpt-5.1', 'tts': 'eleven_v4',
                'quality': 'Как сейчас: самый живой голос, интонация держится через всю реплику.'},
    'balance': {'name': 'Баланс', 'llm': 'yandexgpt-5.1', 'tts': 'eleven_v4_turbo',
                'quality': 'Тот же голос, синтез вдвое дешевле. Интонация чуть проще, на телефоне разница небольшая.'},
    'economy': {'name': 'Эконом', 'llm': 'yandexgpt-5.1', 'tts': 'yandex',
                'quality': 'Голос Яндекса (Даша): синтез в разы дешевле, но звучит заметно роботизированнее.'},
}
DEFAULT = 'quality'


def current() -> str | None:
    """Выбранный набор: из файла настроек, для автотеста — из VOICE_PRESET."""
    from voice import speech
    name = os.getenv('VOICE_PRESET') or speech.setting('preset', '')
    return name if name in PRESETS else None


def llm_model() -> str | None:
    """Модель ответов выбранного набора; None — как в .env (не Яндекс или набор не выбран)."""
    name = current()
    if not name or os.getenv('VOICE_LLM', 'cvc').strip().lower() != 'yandex':
        return None
    return PRESETS[name]['llm']


def tts_model() -> str | None:
    """Синтез выбранного набора: модель ElevenLabs или 'yandex'; None — как в настройках голоса."""
    name = current()
    return PRESETS[name]['tts'] if name else None


def tts_rub_1k(tts: str, v4_price: float) -> float:
    """Цена 1000 символов синтеза: от цены ElevenLabs v4 или своя у Яндекса."""
    t = TTS.get(tts) or {'factor': 1.0}
    return t['rub_1k'] if 'rub_1k' in t else v4_price * t['factor']
