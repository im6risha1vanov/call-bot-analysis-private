"""
Замер скорости моделей для живого разговора: сколько ждать первых слов робота.

    python -m voice.bench claude-haiku-5-5 yandex:yandexgpt-lite yandex:yandexgpt
    python -m voice.bench yandex:list      # какие модели доступны в каталоге Яндекса

Модель без приставки идёт через прокси ai.starimg.ru (cvc), «yandex:» через YandexGPT,
«openrouter:» через OpenRouter. Каждая отвечает на одни и те же реплики клиента,
печатается время до первых слов, полное время и сам ответ.
"""

from __future__ import annotations

import asyncio
import sys
import time

import httpx

from voice.dialog import Dialog
from voice.llm import YandexBackend, make_backend
from voice.operator import load

LINES = ["Да, актуально.", "Краснодар.", "А сколько стоит кухня?"]


async def yandex_models() -> None:
    backend = YandexBackend("yandexgpt-lite")
    async with httpx.AsyncClient(timeout=20) as http:
        r = await http.get("https://llm.api.cloud.yandex.net/v1/models", headers=backend.headers())
    if r.status_code != 200:
        print(f"Yandex {r.status_code}: {r.text[:300]}")
        return
    for item in r.json().get("data", []):
        print("yandex:" + item.get("id", "?").split(f"{backend.folder}/", 1)[-1])


async def bench(spec: str) -> None:
    kind, model = spec.split(":", 1) if ":" in spec else ("cvc", spec)
    if spec == "yandex:list":
        try:
            return await yandex_models()
        except Exception as e:
            print(f"yandex:list ошибка: {e!r}")
            return
    try:
        backend = make_backend(kind, model)
    except Exception as e:
        print(f"{spec:22} ошибка: {e}")
        return
    model = spec
    dialog = Dialog(load("kitchen"), backend)
    dialog.greet()
    for line in LINES:
        started = time.monotonic()
        try:
            turn = await dialog.reply(line)
        except Exception as e:
            print(f"{model:22} ошибка: {(str(e) or repr(e))[:300]}")
            return
        total = (time.monotonic() - started) * 1000
        print(f"{model:22} первые слова {turn.first_text_ms or 0:5} мс, всё {total:5.0f} мс | {line} -> {turn.text[:90]}")
        if turn.result is not None:
            return


async def main(models: list[str]) -> None:
    for model in models:
        await bench(model)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or ["claude-haiku-5-5", "gemini-3.6-flash", "gpt-6.1-sol"]))
