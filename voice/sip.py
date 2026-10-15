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
from dataclasses import dataclass, field

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
    created: float = field(default_factory=time.monotonic)
    connected: asyncio.Event = field(default_factory=asyncio.Event)


PENDING: dict[str, Pending] = {}


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


async def dial(phone: str, operator: str = "kitchen", *, originate=ari_originate) -> str:
    phone = "".join(ch for ch in phone if ch.isdigit())
    if len(phone) < 10:
        raise ValueError("номер слишком короткий")
    load(operator)  # анкета должна читаться до звонка
    call_id = str(uuidlib.uuid4())
    pending = PENDING[call_id] = Pending(phone, operator)
    await originate(call_id, phone)
    asyncio.create_task(_no_answer_watch(call_id, pending))
    log.info("звоню %s, id %s", phone, call_id)
    return call_id


async def _no_answer_watch(call_id: str, pending: Pending) -> None:
    """Клиент не взял трубку: AudioSocket так и не подключился. Пишем НДЗ."""
    try:
        await asyncio.wait_for(pending.connected.wait(), RING_TIMEOUT_S + 15)
    except asyncio.TimeoutError:
        PENDING.pop(call_id, None)
        dialog = Dialog(load(pending.operator), backend=_NoModel())
        dialog.result = CallResult("ndz", "Не взял трубку")
        await results.save(dialog, phone=pending.phone, mode="телефон")
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
        self.lock = asyncio.Lock()
        self.first_words: asyncio.Future | None = None
        self.session: CallSession | None = None
        self.closed = False

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
        while not self.closed:
            if self.out:
                chunk = bytes(self.out[:FRAME_BYTES]).ljust(FRAME_BYTES, b"\0")
                del self.out[:FRAME_BYTES]
                await self.write(frame(KIND_AUDIO, chunk))
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
        phone, operator = (pending.phone, pending.operator) if pending else ("", "kitchen")
        self.session = None
        self.first_words = asyncio.get_running_loop().create_future()
        asr = self.make_asr(self) if self.make_asr else None
        tasks = [asyncio.create_task(self.player())]
        if asr is not None:
            tasks.append(asyncio.create_task(asr.run()))
        session = CallSession(self.make_dialog(load(operator)), self.send_audio, self.send_event,
                              mode="телефон", phone=phone, synthesize=self.synthesize, save=self.save)
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

    async def _read_audio(self, asr) -> None:
        while True:
            try:
                kind, payload = await read_frame(self.reader)
            except (asyncio.IncompleteReadError, ConnectionError):
                return
            if kind == KIND_AUDIO:
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
        try:
            await PhoneCall(reader, writer, make_asr=PhoneASR).run()
        except Exception:
            log.exception("сбой звонка")

    async def api_dial(request: web.Request) -> web.Response:
        data = await request.json()
        try:
            call_id = await dial(str(data.get("phone", "")), str(data.get("operator") or "kitchen"))
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response({"id": call_id})

    server = await asyncio.start_server(on_socket, AUDIOSOCKET_HOST, AUDIOSOCKET_PORT)
    app = web.Application()
    app.router.add_post("/dial", api_dial)
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
