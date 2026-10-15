"""
Один звонок робота: слушает клиента, отвечает голосом, ловит перебивания и
молчание, в конце пишет итог. Транспорт снаружи: сейчас браузер (voice/web.py),
потом SIP Mango. Сессии нужны только две функции: отправить звук и отправить событие.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from typing import Awaitable, Callable

from voice import llm, results, speech
from voice.dialog import SILENCE, Dialog

log = logging.getLogger("voice.call")

SILENCE_AFTER_S = 7.0      # сколько ждать ответа клиента после реплики робота
BARGE_IN_WORDS = 2         # столько слов клиента поверх робота останавливают робота
# пока робот не сказал ни звука, «алло» клиента означает «я тут», а не ответ: ждём приветствия
HELLO_WORDS = {"алло", "ало", "алё", "але", "да", "слушаю", "здравствуйте", "добрый день"}
# одно слово поверх речи робота — обычно шум, музыка или «угу» клиента: робота не останавливаем.
# Исключения: слова, которыми клиент правда просит замолчать.
STOP_WORDS = {"стоп", "нет", "подождите", "погодите", "стойте", "извините", "простите", "минуту", "секунду"}
# «эффект думания»: если модель не начала отвечать за FILLER_AFTER_S, робот говорит «так…», «угу…»
FILLERS = [w.strip() for w in os.getenv("VOICE_FILLERS", "Так…|Угу…|Ага…|Так-так…").split("|") if w.strip()]
FILLER_AFTER_S = int(os.getenv("VOICE_FILLER_AFTER_MS", "400")) / 1000
FILLER_CHANCE = float(os.getenv("VOICE_FILLER_CHANCE", "0.8"))  # не на каждой реплике, чтобы не звучало заученно

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
        speed = float(os.getenv("VOICE_SPEED") or op.voice_speed)  # темп речи: 1.1 быстрее, 0.9 медленнее
        own = speech.Voice(op.voice, op.voice_role, speed)
        self.voice = voice or (speech.Voice(os.environ["VOICE_VOICE"], "", speed)
                               if os.getenv("VOICE_VOICE") else own)
        self.fallback_voice = own if self.voice.name != own.name else None  # если выбранный голос не работает
        self.save = save
        self.phrases: asyncio.Queue[str | None] = asyncio.Queue()
        self.reply_task: asyncio.Task | None = None
        self.tts_task: asyncio.Task | None = None
        self.silence_task: asyncio.Task | None = None
        self.speaking_until = 0.0
        self.pending = 0         # фраз в очереди или в синтезе: робот уже «говорит», хотя звука ещё нет
        self.audio_sent = False
        self.client_spoke = False  # клиент уже сказал что-то по делу (не «алло» поверх приветствия)
        self.generation = 0      # растёт при перебивании, старые фразы не озвучиваются
        self.ended = asyncio.Event()   # итог больше не меняется
        self.done = asyncio.Event()    # итог сохранён и отправлен
        self.saved: dict | None = None
        self.heard_at = 0.0      # когда клиент закончил реплику: для замера, как быстро робот ответил
        self.last_filler = ""
        self.quick_said = ""      # быстрый ответ, который ещё звучит: перебили — убрать из истории
        self.tts_chain: list[str] = []  # id синтезов ElevenLabs этого звонка: следующая фраза продолжает их
        self.said_before = ""     # прошлая фраза робота: по ней ElevenLabs держит ту же интонацию
        self.reply_spoke = False  # робот уже начал озвучивать текущий ответ (для поиска пауз посреди ответа)

    # ------------------------------------------------------------------ жизненный цикл

    async def start(self, first_words: str = "Алло") -> None:
        self.tts_task = asyncio.create_task(self._tts_worker())
        if self.voice_enabled and self.synthesize is speech.synthesize_pcm:
            asyncio.create_task(self._warm_up_voice())
        greeting = self.dialog.greet(first_words)
        await self.send_event({"type": "transcript", "role": "client", "text": first_words})
        await self.send_event({"type": "transcript", "role": "robot", "text": greeting})
        await self._say(greeting)
        self._arm_silence()
        backend = getattr(self.dialog, "backend", None)
        if backend is not None:
            asyncio.create_task(llm.warm_up(backend))

    async def hangup(self, reason: str = "клиент положил трубку") -> None:
        if self.ended.is_set():
            return
        for task in (self.reply_task, self.silence_task):
            if task and not task.done():
                task.cancel()
        if hasattr(self.dialog, "conclude"):
            try:
                await asyncio.wait_for(self.dialog.conclude(reason), 10)
            except asyncio.TimeoutError:
                log.warning("итог после обрыва не успел записаться")
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
        # «алло» клиента, пока приветствие ещё не прозвучало или звучит, это не ответ: не перебиваем
        if final and not self.client_spoke and self.is_speaking() and text.strip(" .,!?…").lower() in HELLO_WORDS:
            log.info("клиент сказал «%s» поверх приветствия, продолжаем", text)
            return
        if self.audio_sent and self.is_speaking() and not self._real_interruption(text):
            log.info("поверх речи робота: «%s», не перебивание (шум или поддакивание)", text)
            return
        self._cancel_silence()
        if not final:
            await self.send_event({"type": "interim", "text": text})
            if self.audio_sent and self.is_speaking() and len(text.split()) >= BARGE_IN_WORDS:
                await self._barge_in(text)
            return
        if self.audio_sent and self.is_speaking():  # приветствие, которое ещё не прозвучало, не сбрасываем
            await self._barge_in(text)
        self.client_spoke = True
        self.heard_at = time.monotonic()
        await self.send_event({"type": "transcript", "role": "client", "text": text})
        self.reply_task = asyncio.create_task(self._respond(text))

    def _real_interruption(self, text: str) -> bool:
        words = speech._words(text)
        if self.quick_said:  # быстрый ответ на «да», а клиент договаривает: слушаем его
            return True
        if len(words) >= BARGE_IN_WORDS or any(w in STOP_WORDS for w in words):
            return True
        # робот вот-вот договорит: короткий ответ («да», «нет») — это ответ, а не шум
        thinking = self.reply_task is not None and not self.reply_task.done()
        return not thinking and self.speaking_until - time.monotonic() < 1.0

    def is_speaking(self) -> bool:
        busy = self.reply_task is not None and not self.reply_task.done()
        return busy or self.pending > 0 or time.monotonic() < self.speaking_until

    async def _barge_in(self, text: str = "") -> None:
        log.info("BARGE_IN: клиент перебил робота: «%s»", text)
        if self.quick_said and self.dialog.undo_quick(self.quick_said):
            log.info("быстрый ответ отменён: клиент не договорил")
        self.quick_said = ""
        self.generation += 1
        if self.reply_task and not self.reply_task.done():
            self.reply_task.cancel()
            try:
                await self.reply_task
            except (asyncio.CancelledError, Exception):
                pass
        while not self.phrases.empty():
            if self.phrases.get_nowait() is not None:
                self.pending -= 1
        self.speaking_until = 0.0
        await self.send_event({"type": "clear"})

    # ------------------------------------------------------------------ робот

    async def _respond(self, text: str) -> None:
        quick = getattr(self.dialog, "quick_reply", None)
        fast = quick(text) if quick and text != SILENCE else None
        if fast:  # ожидаемый ответ клиента: заготовка из анкеты, без ожидания модели
            log.info("быстрый ответ без модели: «%s»", fast)
            self.quick_said = fast
            await self._say(fast)
            await self.send_event({"type": "transcript", "role": "robot", "text": fast, "first_text_ms": 0})
            self._arm_silence()
            return
        self.quick_said = ""
        splitter = speech.PhraseSplitter()
        generation = self.generation
        self.reply_spoke = False
        started = asyncio.Event()  # модель уже дала первую фразу: «так…» не нужно
        filler = asyncio.create_task(self._filler(generation, started)) if text != SILENCE else None

        async def on_text(chunk: str) -> None:
            for phrase in splitter.feed(chunk):
                started.set()
                await self._say(phrase, generation)

        try:
            turn = await self.dialog.reply(text, on_text)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("ошибка ответа модели")
            await self.send_event({"type": "error", "text": f"модель: {e}"})
            return
        finally:
            started.set()
            if filler is not None:
                filler.cancel()
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

    async def _filler(self, generation: int, started: asyncio.Event) -> None:
        after = speech.setting("filler_after_ms", FILLER_AFTER_S * 1000) / 1000
        try:
            await asyncio.wait_for(started.wait(), after)
            return
        except asyncio.TimeoutError:
            pass
        if not FILLERS or not speech.setting("fillers", True) or generation != self.generation or self.pending or random.random() > FILLER_CHANCE:
            return
        word = random.choice([w for w in FILLERS if w != self.last_filler] or FILLERS)
        self.last_filler = word
        log.info("модель думает дольше %d мс: «%s»", after * 1000, word)
        await self._say(word, generation)

    async def _warm_up_voice(self) -> None:
        """Соединение с синтезом и «так…», «угу…» в кэше заранее: в разговоре они звучат без задержки."""
        await speech.warm_up_tts(self.voice)
        for word in FILLERS + [item["reply"] for item in self.dialog.op.quick]:
            try:
                await self.synthesize(word, self.voice)
            except Exception as e:
                log.info("заготовка «%s» не синтезирована: %s", word, e)
                return

    async def _say(self, phrase: str, generation: int | None = None) -> None:
        if phrase.strip():
            self.pending += 1
            await self.phrases.put(f"{self.generation if generation is None else generation}\x00{phrase}")

    async def _tts_worker(self) -> None:
        while True:
            item = await self.phrases.get()
            if item is None:
                return
            try:
                await self._speak(item)
            finally:
                self.pending = max(self.pending - 1, 0)

    async def _speak(self, item: str) -> None:
        generation, phrase = item.split("\x00", 1)
        if int(generation) != self.generation or not self.voice_enabled:
            return
        started = time.monotonic()
        speech.previous_phrase.set(self.said_before)
        speech.tts_chain.set(self.tts_chain)
        self.said_before = "" if phrase in FILLERS else phrase
        try:
            pcm = await self.synthesize(phrase, self.voice)
        except Exception as e:
            log.warning("синтез не удался: %s", e)
            await self.send_event({"type": "error", "text": f"синтез: {e}"})
            if self.fallback_voice is None:
                return
            # до конца звонка говорим голосом из анкеты, чтобы клиент не слушал тишину
            self.voice, self.fallback_voice = self.fallback_voice, None
            try:
                pcm = await self.synthesize(phrase, self.voice)
            except Exception as e2:
                log.warning("запасной голос тоже не сработал: %s", e2)
                return
        log.info("синтез %d мс, %d симв.: %s", (time.monotonic() - started) * 1000, len(phrase), self.voice.name)
        if int(generation) != self.generation:
            return
        now = time.monotonic()
        self.speaking_until = max(now, self.speaking_until) + len(pcm) / speech.TTS_BYTES_PER_SECOND
        self.audio_sent = True
        self.reply_spoke = phrase not in FILLERS
        await self.send_audio(pcm)
        if self.heard_at:
            log.info("ответ клиенту зазвучал через %d мс после его реплики", (now - self.heard_at) * 1000)
            self.heard_at = 0.0

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
            if wait <= 0 and not self.pending and not (self.reply_task and not self.reply_task.done()):
                break
            await asyncio.sleep(max(wait, 0.2))
        await asyncio.sleep(SILENCE_AFTER_S)
        if self.ended.is_set():
            return
        await self.send_event({"type": "transcript", "role": "client", "text": SILENCE})
        self.reply_task = asyncio.create_task(self._respond(SILENCE))
