"""Готовые наборы робота: модель ответов (Яндекс AI Studio) и модель синтеза ElevenLabs.

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
    'aliceai-llm': {'name': 'Alice AI LLM', 'price': (0.5, 0.5, 1.2)},
    'aliceai-llm-flash': {'name': 'Alice AI LLM Flash', 'price': (0.1, 0.025, 0.2)},
}
TTS = {
    'eleven_v4': {'name': 'ElevenLabs v4', 'factor': 1.0},
    'eleven_v4_turbo': {'name': 'ElevenLabs v4 Turbo', 'factor': 0.5},
}
PRESETS = {
    'quality': {'name': 'Качество', 'llm': 'yandexgpt-5.1', 'tts': 'eleven_v4',
                'quality': 'Как сейчас: самая точная модель Яндекса и самый живой голос.'},
    'balance': {'name': 'Баланс', 'llm': 'aliceai-llm', 'tts': 'eleven_v4_turbo',
                'quality': 'Новая большая модель Яндекса, держит анкету почти как Pro. Голос тот же, '
                           'интонация чуть проще.'},
    'economy': {'name': 'Эконом', 'llm': 'aliceai-llm-flash', 'tts': 'eleven_v4_turbo',
                'quality': 'Быстрая маленькая модель: может чаще сбиваться с анкеты и путать итог звонка. '
                           'Голос как в «Балансе».'},
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
    name = current()
    return PRESETS[name]['tts'] if name else None
