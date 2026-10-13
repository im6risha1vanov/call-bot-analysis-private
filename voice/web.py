"""
Тест голосового оператора из браузера, без телефонии.

Запуск на сервере:
    python -m voice.web            # слушает 127.0.0.1:8090

Открыть со своего компьютера через SSH-туннель (микрофон в браузере работает
только на https или localhost, поэтому наружу порт не открываем):
    ssh -L 8090:127.0.0.1:8090 <сервер>
    http://localhost:8090

Говорить в наушниках: иначе микрофон слышит робота из колонок и он сам себя перебивает.
Без DEEPGRAM_API_KEY работает только текстом, без YANDEX_TTS_API_KEY робот не озвучивает ответы.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from aiohttp import WSMsgType, web, ClientSession

from voice import speech
from voice.call import CallSession
from voice.dialog import Dialog
from voice.operator import OPERATORS_DIR, OperatorError, load

log = logging.getLogger("voice.web")
STATIC = Path(__file__).parent / "static"


async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC / "index.html")


async def operators(request: web.Request) -> web.Response:
    items = []
    for path in sorted(OPERATORS_DIR.glob("*.toml")):
        try:
            op = load(path.stem)
            items.append({"id": op.id, "name": op.name, "company": op.company})
        except OperatorError as e:
            items.append({"id": path.stem, "error": str(e)})
    return web.json_response({
        "operators": items,
        "asr": bool(os.getenv("DEEPGRAM_API_KEY")),
        "tts": speech.tts_configured(),
        "llm": f"{os.getenv('VOICE_LLM', 'cvc')}: {os.getenv('VOICE_MODEL') or os.getenv('CVC_MODEL', 'gpt-6-astra')}",
    })


async def call_ws(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=4 * 1024 * 1024)
    await ws.prepare(request)
    send_lock = asyncio.Lock()

    async def send_event(event: dict) -> None:
        async with send_lock:
            if not ws.closed:
                await ws.send_str(json.dumps(event, ensure_ascii=False))

    async def send_audio(pcm: bytes) -> None:
        async with send_lock:
            if not ws.closed:
                await ws.send_bytes(pcm)

    first = await ws.receive()
    if first.type != WSMsgType.TEXT:
        await ws.close()
        return ws
    hello = json.loads(first.data)
    try:
        operator = load(hello.get("operator") or "kitchen")
    except OperatorError as e:
        await send_event({"type": "error", "text": str(e)})
        await ws.close()
        return ws

    session = CallSession(Dialog(operator), send_audio, send_event,
                          voice_enabled=speech.tts_configured(), mode="браузер")
    use_mic = bool(hello.get("mic")) and bool(os.getenv("DEEPGRAM_API_KEY"))
    http = ClientSession()
    asr: speech.DeepgramStream | None = None
    tasks: list[asyncio.Task] = []
    try:
        if use_mic:
            asr = speech.DeepgramStream(http)
            await asr.open()

            async def listen() -> None:
                async for heard in asr:
                    await session.heard(heard.text, heard.final)

            tasks += [asyncio.create_task(listen()), asyncio.create_task(speech.keepalive(asr))]
        async def close_when_done() -> None:
            await session.done.wait()
            await asyncio.sleep(0.5)
            await ws.close()

        tasks.append(asyncio.create_task(close_when_done()))
        await send_event({"type": "ready", "operator": operator.name, "mic": use_mic, "tts": session.voice_enabled})
        await session.start(hello.get("first_words") or "Алло")

        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                if asr:
                    await asr.send(msg.data)
            elif msg.type == WSMsgType.TEXT:
                data = json.loads(msg.data)
                if data.get("type") == "text":
                    await session.heard(data.get("text", ""), True)
                elif data.get("type") == "hangup":
                    break
    except Exception as e:
        log.exception("сбой звонка")
        await send_event({"type": "error", "text": str(e)})
    finally:
        await session.hangup()
        for task in tasks:
            task.cancel()
        if asr:
            await asr.close()
        await http.close()
        await ws.close()
    return ws


def app() -> web.Application:
    application = web.Application()
    application.router.add_get("/", index)
    application.router.add_get("/operators", operators)
    application.router.add_get("/ws", call_ws)
    return application


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    web.run_app(app(), host=os.getenv("VOICE_WEB_HOST", "127.0.0.1"), port=int(os.getenv("VOICE_WEB_PORT", "8090")))
