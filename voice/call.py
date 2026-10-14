"""
Один звонок робота: слушает клиента, отвечает голосом, ловит перебивания и
молчание, в конце пишет итог. Транспорт снаружи: сейчас браузер (voice/web.py),
потом SIP Mango. Сессии нужны только две функции: отправить звук и отправить событие.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Awaitable, Callable

from voice import results, speech
from voice.dialog import SILENCE, Dialog

log = logging.getLogger("voice.call")

SILENCE_AFTER_S = 7.0      # сколько ждать ответа клиента после реплики робота
BARGE_IN_WORDS = 2         # столько слов клиента поверх робота останавливают робота

SendAudio = Callable[[bytes], Awaitable[None]]
SendEvent = Callable[[dict], Awaitable[None]]


class CallSession:
    def __init__(self, dialog: Dialog, send_audio: SendAudio, send_event: SendEvent, *,
                 voice_enabled: bool = True, mode: str = "браузер", phone: str = "",
                 synthesize=speech.synthesize_pcm, save=results.save, voice: speech.Voice | None = None) -> None:
        self.dialog = dialog
        self.send_audio = send_audio
        self.send_event = send_event
        self.voice_enabled = voice_enabled
        self.mode = mode
        self.phone = phone
        self.synthesize = synthesize
        op = dialog.op
        # VOICE_VOICE на сервере перекрывает голос из анкеты: так голос меняется без правки кода
        self.voice = voice or speech.Voice(os.getenv("VOICE_VOICE") or op.voice,
                                           "" if os.getenv("VOICE_VOICE") else op.voice_role, float(op.voice_speed))
        self.save = save
        self.phrases: asyncio.Queue[str | None] = asyncio.Queue()
        self.reply_task: asyncio.Task | None = None
        self.tts_task: asyncio.Task | None = None
        self.silence_task: asyncio.Task | None = None
        self.speaking_until = 0.0
        self.generation = 0      # растёт при перебивании, старые фразы не озвучиваются
        self.ended = asyncio.Event()   # итог больше не меняется
        self.done = asyncio.Event()    # итог сохранён и отправлен
        self.saved: dict | None = None

    # ------------------------------------------------------------------ жизненный цикл

    async def start(self, first_words: str = "Алло") -> None:
        self.tts_task = asyncio.create_task(self._tts_worker())
        greeting = self.dialog.greet(first_words)
        await self.send_event({"type": "transcript", "role": "client", "text": first_words})
        await self.send_event({"type": "transcript", "role": "robot", "text": greeting})
        await self._say(greeting)
        self._arm_silence()

    async def hangup(self, reason: str = "клиент положил трубку") -> None:
        if self.ended.is_set():
            return
        for task in (self.reply_task, self.silence_task):
            if task and not task.done():
                task.cancel()
        await self._finish(reason)

    async def _finish(self, reason: str = "") -> None:
        if self.ended.is_set():
            return
        self.ended.set()
        await self.phrases.put(None)
        if self.tts_task:
            try:
                await asyncio.wait_for(self.tts_task, timeout=30)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self.tts_task.cancel()
        self.saved = await self.save(self.dialog, phone=self.phone, mode=self.mode)
        await self.send_event({"type": "result", "reason": reason, **{k: self.saved[k] for k in (
            "outcome", "transfer", "comment", "facts", "duration_s", "cost", "cost_unit")},
            "labels": {key: text.split(":")[0] for key, text in self.dialog.op.fields.items()}})
        self.done.set()

    # ------------------------------------------------------------------ клиент

    async def heard(self, text: str, final: bool) -> None:
        """Распознанная речь клиента: промежуточная (final=False) или законченная реплика."""
        if self.ended.is_set() or not text.strip():
            return
        self._cancel_silence()
        if not final:
            await self.send_event({"type": "interim", "text": text})
            if self.is_speaking() and len(text.split()) >= BARGE_IN_WORDS:
                await self._barge_in()
            return
        if self.is_speaking():
            await self._barge_in()
        await self.send_event({"type": "transcript", "role": "client", "text": text})
        self.reply_task = asyncio.create_task(self._respond(text))

    def is_speaking(self) -> bool:
        busy = self.reply_task is not None and not self.reply_task.done()
        return busy or time.monotonic() < self.speaking_until

    async def _barge_in(self) -> None:
        self.generation += 1
        if self.reply_task and not self.reply_task.done():
            self.reply_task.cancel()
            try:
                await self.reply_task
            except (asyncio.CancelledError, Exception):
                pass
        while not self.phrases.empty():
            self.phrases.get_nowait()
        self.speaking_until = 0.0
        await self.send_event({"type": "clear"})

    # ------------------------------------------------------------------ робот

    async def _respond(self, text: str) -> None:
        splitter = speech.PhraseSplitter()
        generation = self.generation

        async def on_text(chunk: str) -> None:
            for phrase in splitter.feed(chunk):
                await self._say(phrase, generation)

        try:
            turn = await self.dialog.reply(text, on_text)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("ошибка ответа модели")
            await self.send_event({"type": "error", "text": f"модель: {e}"})
            return
        for phrase in splitter.flush():
            await self._say(phrase, generation)
        if turn.fallback:
            await self._say(turn.text, generation)  # прощание из анкеты: модель сама ничего не сказала
        if turn.text:
            await self.send_event({"type": "transcript", "role": "robot", "text": turn.text,
                                   "first_text_ms": turn.first_text_ms})
        if turn.result is not None:
            await self._finish("итог записан")
        else:
            self._arm_silence()

    async def _say(self, phrase: str, generation: int | None = None) -> None:
        if phrase.strip():
            await self.phrases.put(f"{self.generation if generation is None else generation}\x00{phrase}")

    async def _tts_worker(self) -> None:
        while True:
            item = await self.phrases.get()
            if item is None:
                return
            generation, phrase = item.split("\x00", 1)
            if int(generation) != self.generation or not self.voice_enabled:
                continue
            try:
                pcm = await self.synthesize(phrase, self.voice)
            except Exception as e:
                log.warning("синтез не удался: %s", e)
                await self.send_event({"type": "error", "text": f"синтез: {e}"})
                continue
            if int(generation) != self.generation:
                continue
            now = time.monotonic()
            self.speaking_until = max(now, self.speaking_until) + len(pcm) / speech.TTS_BYTES_PER_SECOND
            await self.send_audio(pcm)

    # ------------------------------------------------------------------ молчание

    def _arm_silence(self) -> None:
        self._cancel_silence()
        self.silence_task = asyncio.create_task(self._silence_watch())

    def _cancel_silence(self) -> None:
        if self.silence_task and not self.silence_task.done() and self.silence_task is not asyncio.current_task():
            self.silence_task.cancel()

    async def _silence_watch(self) -> None:
        # ждём, пока робот договорит, потом SILENCE_AFTER_S тишины
        while True:
            wait = self.speaking_until - time.monotonic()
            if wait <= 0 and not (self.reply_task and not self.reply_task.done()):
                break
            await asyncio.sleep(max(wait, 0.2))
        await asyncio.sleep(SILENCE_AFTER_S)
        if self.ended.is_set():
            return
        await self.send_event({"type": "transcript", "role": "client", "text": SILENCE})
        self.reply_task = asyncio.create_task(self._respond(SILENCE))
