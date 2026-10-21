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

from voice import llm, speech
from voice.call import FILLER_AFTER_S, WHOLE_REPLY, CallSession
from voice.dialog import Dialog
from voice.operator import OPERATORS_DIR, OperatorError, load

log = logging.getLogger("voice.web")
STATIC = Path(__file__).parent / "static"


async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC / "index.html")


async def operators(request: web.Request) -> web.Response:
    if os.getenv("ELEVENLABS_API_KEY") and not any(k.startswith("11:") for k in speech.voices()):
        await speech.load_eleven_voices()
    items = []
    for path in sorted(OPERATORS_DIR.glob("*.toml")):
        try:
            op = load(path.stem)
            items.append({"id": op.id, "name": op.name, "company": op.company, "greeting": op.greeting,
                          "voice": op.voice, "voice_role": op.voice_role, "voice_speed": op.voice_speed})
        except OperatorError as e:
            items.append({"id": path.stem, "error": str(e)})
    return web.json_response({
        "operators": items,
        "asr": bool(os.getenv("DEEPGRAM_API_KEY")),
        "tts": speech.tts_configured(),
        "tts_rate": speech.TTS_RATE,
        "voices": speech.voices(),
        "llm": llm.describe(),
    })


def voice_from(data: dict) -> speech.Voice | None:
    """Голос, выбранный на странице: имя, интонация, скорость."""
    name = str(data.get("voice") or "").strip()
    if not name:
        return None
    try:
        speed = min(max(float(data.get("speed") or 1.0), 0.5), 2.0)
    except (TypeError, ValueError):
        speed = 1.0
    return speech.Voice(name, str(data.get("role") or "").strip(), speed)


async def voice_page(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC / "voice.html")


def _current_voice() -> dict:
    """Что сейчас звучит в звонках: .env плюс сохранённое на странице."""
    model, sliders = speech.eleven_params()
    env_voice = os.getenv("VOICE_VOICE", "")
    return {
        "voice_id": speech.setting("voice_id", env_voice.removeprefix("11:") if env_voice.startswith("11:") else ""),
        "model": model, "stability": sliders["stability"], "similarity": sliders["similarity_boost"],
        "style": sliders["style"], "speaker_boost": sliders["use_speaker_boost"],
        "speed": speech.setting("speed", float(os.getenv("VOICE_SPEED") or 1.0)),
        "calm": speech.setting("calm", speech.CALM),
        "tail_ms": speech.setting("tail_ms", int(speech.YANDEX_TAIL_S * 1000)),
        "filler_after_ms": speech.setting("filler_after_ms", int(FILLER_AFTER_S * 1000)),
        "fillers": speech.setting("fillers", True),
        "casual": speech.setting("casual", speech.CASUAL),
        "pause_ms": speech.setting("pause_ms", speech.PAUSE_MS),
        "fewer_commas": speech.setting("fewer_commas", True),
        "inner_pause_ms": speech.setting("inner_pause_ms", speech.INNER_PAUSE_MS),
        "whole_reply": speech.setting("whole_reply", WHOLE_REPLY),
        "bg_noise": speech.setting("bg_noise", float(os.getenv("VOICE_BG_NOISE", "0.3"))),
        "tempo_vary": speech.setting("tempo_vary", speech.TEMPO_VARY),
    }


async def get_settings(request: web.Request) -> web.Response:
    if os.getenv("ELEVENLABS_API_KEY") and not speech._eleven_direct:
        await speech.load_eleven_voices()
    return web.json_response({"current": _current_voice(), "saved": speech.live_settings(),
                              "file": speech.SETTINGS_FILE, "voices": speech._eleven_direct,
                              "test_phones": TEST_PHONES})


async def post_settings(request: web.Request) -> web.Response:
    if not speech.SETTINGS_FILE:
        return web.json_response({"error": "не задан VOICE_SETTINGS_FILE"}, status=400)
    try:
        saved = speech.save_settings(await request.json())
    except OSError as e:
        return web.json_response({"error": str(e)}, status=500)
    log.info("настройки голоса сохранены: %s", saved)
    return web.json_response({"saved": saved})


# Звонок со страницы: только на тестовые номера (свои), не на базу
TEST_PHONES = [p.strip() for p in os.getenv("VOICE_TEST_PHONES", "79042031766").split(",") if p.strip()]


async def test_dial(request: web.Request) -> web.Response:
    phone = str((await request.json()).get("phone", "")).strip()
    if phone not in TEST_PHONES:
        return web.json_response({"error": "звонок со страницы только на тестовый номер"}, status=400)
    port = os.getenv("VOICE_SIP_API_PORT", "8092")
    try:
        async with ClientSession() as http:
            async with http.post(f"http://127.0.0.1:{port}/dial", json={"phone": phone}) as r:
                return web.json_response(await r.json(content_type=None), status=r.status)
    except Exception as e:
        return web.json_response({"error": f"телефонный робот не отвечает: {e}"}, status=502)


async def preview(request: web.Request) -> web.Response:
    """Прослушать голос: короткая фраза, ответ сырым PCM. settings — несохранённые ползунки страницы."""
    data = await request.json()
    if isinstance(data.get("settings"), dict):
        speech.settings_override.set(speech.clean_settings(data["settings"]))
    voice = voice_from(data)
    text = str(data.get("text") or "").strip()[:400]
    if voice is None or not text:
        return web.json_response({"error": "нужны голос и текст"}, status=400)
    if not speech.tts_configured():
        return web.json_response({"error": "нет ключа Яндекса"}, status=400)
    try:
        pcm = await speech.synthesize_pcm(text, voice)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=502)
    return web.Response(body=pcm, content_type="application/octet-stream", headers={"X-Rate": str(speech.TTS_RATE)})


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
                          voice_enabled=speech.tts_configured(), mode="браузер", voice=voice_from(hello))
    use_mic = bool(hello.get("mic")) and bool(os.getenv("DEEPGRAM_API_KEY"))
    http = ClientSession()
    asr = None
    tasks: list[asyncio.Task] = []
    try:
        if use_mic:
            asr = speech.asr_stream(http)
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
        await send_event({"type": "ready", "operator": operator.name, "mic": use_mic, "tts": session.voice_enabled,
                          "tts_rate": speech.TTS_RATE, "voice": session.voice.__dict__})
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
    application.router.add_post("/preview", preview)
    application.router.add_get("/voice", voice_page)
    application.router.add_get("/settings", get_settings)
    application.router.add_post("/settings", post_settings)
    application.router.add_post("/dial", test_dial)
    return application


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    web.run_app(app(), host=os.getenv("VOICE_WEB_HOST", "127.0.0.1"), port=int(os.getenv("VOICE_WEB_PORT", "8090")))
