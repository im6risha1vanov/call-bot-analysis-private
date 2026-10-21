"""
Робот по телефону: Mango → Asterisk → AudioSocket → этот сервис.

Asterisk на том же сервере зарегистрирован в Mango как сотрудник «Робот»
(scripts/voice_sip_setup.sh). Звонок заказывается через ARI: Asterisk набирает
номер через Mango, а когда клиент снимает трубку, отдаёт звук звонка сюда по
протоколу AudioSocket (TCP, PCM 8 кГц 16 бит моно, кадры по 20 мс).

Запуск сервиса (на сервере это делает scripts/voice_test.sh sip):
    python -m voice.sip serve
Позвонить:
    python -m voice.sip dial 79991234567 [--operator kitchen]

Без согласия абонента роботом звонить нельзя (с 1.09.2025). Для тестов звони на свои номера.
"""

from __future__ import annotations

import argparse
import array
import asyncio
import json
import logging
import os
import sys
import time
import uuid as uuidlib
import wave
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from voice import results, speech
from voice.call import CallSession
from voice.dialog import CallResult, Dialog
from voice.operator import load

log = logging.getLogger("voice.sip")

RATE = 8000                  # телефония
FRAME_BYTES = RATE * 2 // 50  # 20 мс
KIND_HANGUP, KIND_UUID, KIND_AUDIO, KIND_ERROR = 0x00, 0x01, 0x10, 0xFF

AUDIOSOCKET_HOST = os.getenv("VOICE_AUDIOSOCKET_HOST", "127.0.0.1")
AUDIOSOCKET_PORT = int(os.getenv("VOICE_AUDIOSOCKET_PORT", "9092"))
API_PORT = int(os.getenv("VOICE_SIP_API_PORT", "8092"))
ARI_URL = os.getenv("VOICE_ARI_URL", "http://127.0.0.1:8088/ari")
ARI_USER = os.getenv("VOICE_ARI_USER", "voicebot")
TRUNK = os.getenv("VOICE_SIP_TRUNK", "mango")
RING_TIMEOUT_S = int(os.getenv("VOICE_RING_TIMEOUT", "40"))
FIRST_WORDS_WAIT_S = 2.5     # сколько ждать «Алло», прежде чем поздороваться самому
# Записи разговоров: WAV 8 кГц моно (робот и клиент вместе), хранятся RECORDING_DAYS дней
RECORDINGS = Path(os.getenv("VOICE_RECORDINGS") or Path(os.getenv("VOICE_LOG", "voice_calls.jsonl")).resolve().parent / "recordings")
RECORDING_DAYS = float(os.getenv("VOICE_RECORDING_DAYS", "10"))
# Фон на линии: без него в паузах звучит «мёртвая» цифровая тишина и слышно, как робот пропадает между фразами.
# Уровень bg_noise со страницы голоса (0 — выключен). VOICE_BG_FILE — свой WAV 8 кГц моно (шум офиса), иначе мягкий шум.
BG_NOISE = float(os.getenv("VOICE_BG_NOISE", "0.3"))
BG_PEAK = 900  # амплитуда фона при уровне 1.0 (речь робота доходит до 10–20 тыс.)


# ------------------------------------------------------------------ звук


def downsample(pcm: bytes, src: int, dst: int) -> bytes:
    """48 кГц → 8 кГц: среднее по окну (грубый фильтр от наложения частот), иначе линейно."""
    if src == dst or not pcm:
        return pcm
    if src % dst:
        return speech.resample(pcm, src, dst)
    k = src // dst
    x = array.array("h", pcm[: len(pcm) // (2 * k) * 2 * k])
    out = array.array("h", (sum(x[i:i + k]) // k for i in range(0, len(x), k)))
    return out.tobytes()


def mix(a: bytes, b: bytes) -> bytes:
    """Две дорожки PCM 16 бит в одну: сумма с ограничением, короткая дополняется тишиной."""
    x, y = array.array("h", a[: len(a) // 2 * 2]), array.array("h", b[: len(b) // 2 * 2])
    if len(x) < len(y):
        x, y = y, x
    out = array.array("h", x)
    for i, v in enumerate(y):
        out[i] = max(-32768, min(32767, out[i] + v))
    return out.tobytes()


_bg_cache: dict = {}


def background() -> array.array:
    """Петля фона на 20 с: свой WAV или «комнатный» шум (низкочастотный, без шипения), нормированный к 1.0."""
    if "loop" in _bg_cache:
        return _bg_cache["loop"]
    loop = None
    path = os.getenv("VOICE_BG_FILE", "")
    if path:
        try:
            with wave.open(path) as w:
                if w.getframerate() == RATE and w.getnchannels() == 1 and w.getsampwidth() == 2:
                    loop = array.array("h", w.readframes(w.getnframes()))
                else:
                    log.warning("фон %s: нужен WAV 8 кГц моно 16 бит", path)
        except (OSError, wave.Error) as e:
            log.warning("фон %s не прочитан: %s", path, e)
    if loop:
        peak = max(1, max(abs(v) for v in loop))
        values = [v / peak for v in loop]
    else:
        import random
        rnd = random.Random(7)
        values, low, slow = [], 0.0, 0.0
        for _ in range(RATE * 20):
            white = rnd.uniform(-1, 1)
            low = 0.9 * low + 0.1 * white          # срезаем шипение
            slow = 0.9995 * slow + 0.0005 * rnd.uniform(-1, 1)  # медленно «дышит», как живое помещение
            values.append(low * (0.8 + 40 * abs(slow)))
        peak = max(abs(v) for v in values) or 1
        values = [v / peak for v in values]
    _bg_cache["loop"] = array.array("f", values)
    return _bg_cache["loop"]


def add_background(chunk: bytes, pos: int, level: float) -> tuple[bytes, int]:
    """Подмешивает фон к кадру (или к тишине). Возвращает кадр и новую позицию в петле."""
    loop = background()
    x = array.array("h", chunk)
    amp = level * BG_PEAK
    n = len(loop)
    for i in range(len(x)):
        x[i] = max(-32768, min(32767, x[i] + int(loop[(pos + i) % n] * amp)))
    return x.tobytes(), (pos + len(x)) % n


def save_recording(name: str, robot: bytes, client: bytes, directory: Path | None = None) -> Path | None:
    directory = directory or RECORDINGS
    pcm = mix(robot, client)
    if not pcm:
        return None
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return path


def clean_recordings(directory: Path | None = None, days: float | None = None, now: float | None = None) -> int:
    """Удаляет записи старше срока хранения. Возвращает, сколько удалено."""
    directory = directory or RECORDINGS
    limit = (now or time.time()) - (RECORDING_DAYS if days is None else days) * 86400
    removed = 0
    if directory.is_dir():
        for path in directory.glob("*.wav"):
            try:
                if path.stat().st_mtime < limit:
                    path.unlink()
                    removed += 1
            except OSError:
                pass
    return removed


def frame(kind: int, payload: bytes = b"") -> bytes:
    return bytes([kind]) + len(payload).to_bytes(2, "big") + payload


async def read_frame(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    head = await reader.readexactly(3)
    size = int.from_bytes(head[1:3], "big")
    return head[0], (await reader.readexactly(size) if size else b"")


# ------------------------------------------------------------------ заказанные звонки


@dataclass
class Pending:
    phone: str
    operator: str
    meta: dict = field(default_factory=dict)  # от CRM: кампания, чей звонок; попадает в итог звонка
    created: float = field(default_factory=time.monotonic)
    connected: asyncio.Event = field(default_factory=asyncio.Event)


PENDING: dict[str, Pending] = {}
ACTIVE = [0]  # звонков на линии сейчас


async def ari_originate(call_id: str, phone: str, *, http: httpx.AsyncClient | None = None) -> dict:
    params = {
        "endpoint": f"PJSIP/{phone}@{TRUNK}",
        "context": "voicebot", "extension": "s", "priority": "1",
        "timeout": str(RING_TIMEOUT_S),
    }
    caller_id = os.getenv("VOICE_CALLER_ID", "").strip()
    if caller_id:
        params["callerId"] = caller_id
    auth = (ARI_USER, os.environ["VOICE_ARI_PASSWORD"])
    async with (http or httpx.AsyncClient(timeout=10)) as client:
        r = await client.post(ARI_URL + "/channels", params=params, auth=auth, json={"variables": {"VOICE_ID": call_id}})
    if r.status_code >= 300:
        raise RuntimeError(f"Asterisk ARI {r.status_code}: {r.text[:300]}")
    return r.json()


async def dial(phone: str, operator: str = "kitchen", *, meta: dict | None = None, originate=ari_originate) -> str:
    phone = "".join(ch for ch in phone if ch.isdigit())
    if len(phone) < 10:
        raise ValueError("номер слишком короткий")
    op = load(operator)  # анкета должна читаться до звонка
    asyncio.create_task(_warm_up_greeting(op))
    call_id = str(uuidlib.uuid4())
    pending = PENDING[call_id] = Pending(phone, operator, dict(meta or {}))
    await originate(call_id, phone)
    asyncio.create_task(_no_answer_watch(call_id, pending))
    log.info("звоню %s, id %s", phone, call_id)
    return call_id


async def _warm_up_greeting(op) -> None:
    """Пока идут гудки, синтезируем приветствие и заготовки: после «алло» они звучат сразу, без 2 с синтеза."""
    speed = float(os.getenv("VOICE_SPEED") or op.voice_speed)
    voice = speech.Voice(os.getenv("VOICE_VOICE") or op.voice, "" if os.getenv("VOICE_VOICE") else op.voice_role, speed)
    from voice.call import FILLERS
    for text in [op.greeting] + FILLERS + [item["reply"] for item in op.quick]:
        try:
            await speech.synthesize_pcm(text, voice)
        except Exception as e:
            log.info("заготовка «%s» не синтезирована заранее: %s", text[:30], e)
            return


async def _no_answer_watch(call_id: str, pending: Pending) -> None:
    """Клиент не взял трубку: AudioSocket так и не подключился. Пишем НДЗ."""
    try:
        await asyncio.wait_for(pending.connected.wait(), RING_TIMEOUT_S + 15)
    except asyncio.TimeoutError:
        PENDING.pop(call_id, None)
        dialog = Dialog(load(pending.operator), backend=_NoModel())
        dialog.result = CallResult("ndz", "Не взял трубку")
        await results.save(dialog, phone=pending.phone, mode="телефон", extra={"call_id": call_id, **pending.meta})
        log.info("%s не ответил", pending.phone)


class _NoModel:
    """Заглушка модели для записи НДЗ без разговора."""
    model = "-"
    unit = ""


# ------------------------------------------------------------------ один звонок


class PhoneCall:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, *,
                 make_dialog=None, make_asr=None, synthesize=speech.synthesize_pcm, save=results.save) -> None:
        self.reader, self.writer = reader, writer
        self.make_dialog = make_dialog or (lambda op: Dialog(op))
        self.make_asr = make_asr
        self.synthesize, self.save = synthesize, save
        self.out = bytearray()            # звук робота, ещё не отправленный в Asterisk
        self.late = 0                     # сколько раз кадр звука ушёл с опозданием
        self.ran_dry = 0.0                # когда кончился звук робота
        self.lock = asyncio.Lock()
        self.first_words: asyncio.Future | None = None
        self.session: CallSession | None = None
        self.closed = False
        self.rec_robot = bytearray()      # запись: что сказал робот, по кадрам (тишина, когда молчит)
        self.rec_client = bytearray()     # запись: что пришло от клиента
        self.recording = True
        self.bg_pos = 0

    async def send_audio(self, pcm: bytes) -> None:
        self.out += downsample(pcm, speech.TTS_RATE, RATE)

    async def send_event(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "clear":
            self.out.clear()  # клиент перебил: недоговорённое не проигрываем
        elif kind in ("transcript", "result", "error"):
            log.info("звонок: %s", json.dumps(event, ensure_ascii=False))

    async def write(self, data: bytes) -> None:
        if self.closed:
            return
        async with self.lock:
            self.writer.write(data)
            await self.writer.drain()

    async def player(self) -> None:
        """Отдаёт звук робота кадрами по 20 мс в реальном времени."""
        start = time.monotonic()
        n = 0
        level = max(0.0, min(1.0, float(speech.setting("bg_noise", BG_NOISE))))
        while not self.closed:
            had_speech = bool(self.out)
            chunk_sent = had_speech or level > 0
            if self.out:
                chunk = bytes(self.out[:FRAME_BYTES]).ljust(FRAME_BYTES, b"\0")
                del self.out[:FRAME_BYTES]
                if not self.out:
                    self.ran_dry = time.monotonic()
            else:
                chunk = bytes(FRAME_BYTES)
            if level > 0:  # фон идёт всё время, и под речью, и в паузах
                chunk, self.bg_pos = add_background(chunk, self.bg_pos, level)
            if chunk_sent:
                await self.write(frame(KIND_AUDIO, chunk))
                if self.recording:
                    self.rec_robot += chunk
            if not had_speech and self.ran_dry and self.session is not None and self.session.reply_spoke and (
                    self.session.pending or self.session.reply_task and not self.session.reply_task.done()):
                # звук кончился, а следующая фраза ещё не готова (модель пишет или синтез): пауза посреди ответа
                log.warning("AUDIO_GAP: пауза посреди ответа робота, следующая фраза не готова")
                self.ran_dry = 0.0
            if not chunk_sent and self.recording:
                self.rec_robot += bytes(FRAME_BYTES)
            n += 1
            delay = start + n * 0.02 - time.monotonic()
            if delay < -0.06 and self.out:  # кадр ушёл позже срока: клиент слышит запинку
                self.late += 1
                if self.late <= 20:
                    log.warning("LATE_AUDIO: звук робота отстал на %d мс (сеть до Asterisk или занятый цикл)", -delay * 1000)
            if delay < -0.2:  # отстали (пауза в цикле): не догоняем рывком
                start, n = time.monotonic(), 0
                delay = 0.02
            await asyncio.sleep(max(delay, 0))

    async def heard(self, text: str, final: bool) -> None:
        if self.session is None:
            if final and self.first_words and not self.first_words.done():
                self.first_words.set_result(text)
            return
        await self.session.heard(text, final)

    async def run(self) -> None:
        kind, payload = await read_frame(self.reader)
        if kind != KIND_UUID:
            raise RuntimeError(f"AudioSocket: первым пришёл кадр {kind:#x}, ждали UUID")
        call_id = str(uuidlib.UUID(bytes=payload))
        pending = PENDING.pop(call_id, None)
        if pending:
            pending.connected.set()
        phone, operator, meta = (pending.phone, pending.operator, pending.meta) if pending else ("", "kitchen", {})
        record_name = f"{call_id}.wav"
        self.session = None
        self.first_words = asyncio.get_running_loop().create_future()
        asr = self.make_asr(self) if self.make_asr else None
        tasks = [asyncio.create_task(self.player())]
        if asr is not None:
            tasks.append(asyncio.create_task(asr.run()))
        session = CallSession(self.make_dialog(load(operator)), self.send_audio, self.send_event,
                              mode="телефон", phone=phone, synthesize=self.synthesize, save=self.save)
        session.extra = {"call_id": call_id, **meta, **({"recording": record_name} if self.recording else {})}
        reader_task = asyncio.create_task(self._read_audio(asr))
        try:
            try:
                words = await asyncio.wait_for(asyncio.shield(self.first_words), FIRST_WORDS_WAIT_S)
            except asyncio.TimeoutError:
                words = "Алло"
            self.session = session
            await session.start(words)
            done = asyncio.create_task(session.done.wait())
            await asyncio.wait({done, reader_task}, return_when=asyncio.FIRST_COMPLETED)
            if not session.done.is_set():
                await session.hangup()
            else:
                while self.out and not reader_task.done():  # договорить прощание
                    await asyncio.sleep(0.1)
                await self.write(frame(KIND_HANGUP))
        finally:
            self.closed = True
            for task in tasks + [reader_task]:
                task.cancel()
            if asr is not None:
                await asr.close()
            self.writer.close()
            if self.recording:
                try:
                    save_recording(record_name, bytes(self.rec_robot), bytes(self.rec_client))
                except Exception:
                    log.exception("запись разговора не сохранилась")

    async def _read_audio(self, asr) -> None:
        while True:
            try:
                kind, payload = await read_frame(self.reader)
            except (asyncio.IncompleteReadError, ConnectionError):
                return
            if kind == KIND_AUDIO:
                if self.recording:
                    self.rec_client += payload
                if asr is not None:
                    await asr.send(payload)
            elif kind in (KIND_HANGUP, KIND_ERROR):
                return


class PhoneASR:
    """Deepgram на 8 кГц для одного звонка."""

    def __init__(self, call: PhoneCall) -> None:
        import aiohttp
        self.call = call
        self.http = aiohttp.ClientSession()
        self.stream = speech.asr_stream(self.http, rate=RATE)
        self.opened = asyncio.Event()
        self.closing = False

    async def run(self) -> None:
        await self._listen()
        if isinstance(self.stream, speech.YandexStream) and not self.closing:
            # Яндекс оборвал поток посреди звонка: дослушиваем через Deepgram, иначе робот оглохнет
            log.warning("распознавание Яндекса оборвалось, переключаюсь на Deepgram")
            self.opened.clear()
            old, self.stream = self.stream, speech.DeepgramStream(self.http, rate=RATE)
            await old.close()
            await self._listen()

    async def _listen(self) -> None:
        try:
            await self.stream.open()
            self.opened.set()
            keep = asyncio.create_task(speech.keepalive(self.stream))
            async for heard in self.stream:
                await self.call.heard(heard.text, heard.final)
            keep.cancel()
        except Exception:
            log.exception("распознавание оборвалось")

    async def send(self, pcm: bytes) -> None:
        if self.opened.is_set():
            await self.stream.send(pcm)

    async def close(self) -> None:
        self.closing = True
        await self.stream.close()
        await self.http.close()


# ------------------------------------------------------------------ сервис


async def serve() -> None:
    from aiohttp import web

    async def on_socket(reader, writer):
        ACTIVE[0] += 1
        try:
            await PhoneCall(reader, writer, make_asr=PhoneASR).run()
        except Exception:
            log.exception("сбой звонка")
        finally:
            ACTIVE[0] -= 1

    async def api_dial(request: web.Request) -> web.Response:
        data = await request.json()
        meta = {k: data[k] for k in ("campaign_id", "owner", "target_id") if data.get(k) not in (None, "")}
        try:
            call_id = await dial(str(data.get("phone", "")), str(data.get("operator") or "kitchen"), meta=meta)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response({"id": call_id})

    async def api_status(request: web.Request) -> web.Response:
        """Сколько звонков идёт: CRM не заказывает следующий, пока робот занят."""
        return web.json_response({"active": ACTIVE[0], "pending": len(PENDING)})

    async def cleaner() -> None:
        while True:
            removed = clean_recordings()
            if removed:
                log.info("удалено старых записей: %d", removed)
            await asyncio.sleep(3600)

    await asyncio.to_thread(background)  # петля фона готовится 0,3 с: заранее, а не посреди первого звонка
    server = await asyncio.start_server(on_socket, AUDIOSOCKET_HOST, AUDIOSOCKET_PORT)
    app = web.Application()
    app.router.add_post("/dial", api_dial)
    app.router.add_get("/status", api_status)
    cleaning = asyncio.create_task(cleaner())  # noqa: F841 (держим ссылку, чтобы задачу не собрал сборщик)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", API_PORT).start()
    log.info("AudioSocket %s:%s, заказ звонков http://127.0.0.1:%s/dial", AUDIOSOCKET_HOST, AUDIOSOCKET_PORT, API_PORT)
    async with server:
        await server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve")
    d = sub.add_parser("dial")
    d.add_argument("phone")
    d.add_argument("--operator", default="kitchen")
    args = parser.parse_args(argv)
    if args.cmd == "serve":
        asyncio.run(serve())
        return 0
    r = httpx.post(f"http://127.0.0.1:{API_PORT}/dial", json={"phone": args.phone, "operator": args.operator}, timeout=15)
    print(r.text)
    return 0 if r.status_code == 200 else 1


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    sys.exit(main())
