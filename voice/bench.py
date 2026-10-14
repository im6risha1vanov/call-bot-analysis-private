"""
Замер скорости моделей для живого разговора: сколько ждать первых слов робота.

    python -m voice.bench claude-haiku-5-5 gemini-3.6-flash gpt-6.1-sol

Каждая модель отвечает на одни и те же реплики клиента (через прокси ai.starimg.ru,
VOICE_LLM=cvc), печатается время до первых слов, полное время и сам ответ.
"""

from __future__ import annotations

import asyncio
import sys
import time

from voice.dialog import Dialog
from voice.llm import make_backend
from voice.operator import load

LINES = ["Да, актуально.", "Краснодар.", "А сколько стоит кухня?"]


async def bench(model: str) -> None:
    dialog = Dialog(load("kitchen"), make_backend("cvc", model))
    dialog.greet()
    for line in LINES:
        started = time.monotonic()
        try:
            turn = await dialog.reply(line)
        except Exception as e:
            print(f"{model:22} ошибка: {str(e)[:200]}")
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
