"""
Звук для голосового оператора: потоковое распознавание Deepgram и синтез
Yandex SpeechKit в сырой PCM (16 кГц, 16 бит, моно), плюс нарезка ответа
модели на фразы, чтобы озвучка начиналась с первого предложения.

Ключи те же, что у бота: DEEPGRAM_API_KEY и YANDEX_TTS_API_KEY.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import AsyncIterator
from urllib.parse import urlencode

import httpx

log = logging.getLogger("voice.speech")

SAMPLE_RATE = 16000
BYTES_PER_SECOND = SAMPLE_RATE * 2
YANDEX_TTS_URL = "https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize"
DEEPGRAM_WS = "wss://api.deepgram.com/v1/listen"

# Конец фразы: . ! ? … и пробел после. Длинную фразу без точки режем на запятой.
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
_LONG = 140


class PhraseSplitter:
    """Копит куски текста от модели и отдаёт готовые к озвучке фразы."""

    def __init__(self) -> None:
        self.buffer = ""

    def feed(self, chunk: str) -> list[str]:
        self.buffer += chunk
        out = []
        while True:
            parts = _SENTENCE_END.split(self.buffer, maxsplit=1)
            if len(parts) == 2 and parts[0].strip():
                out.append(parts[0].strip())
                self.buffer = parts[1]
                continue
            if len(self.buffer) > _LONG and ", " in self.buffer[40:]:
                cut = self.buffer.rindex(", ") + 1
                out.append(self.buffer[:cut].strip())
                self.buffer = self.buffer[cut:]
                continue
            return out

    def flush(self) -> list[str]:
        rest, self.buffer = self.buffer.strip(), ""
        return [rest] if rest else []


# ------------------------------------------------------------------ синтез

_http: httpx.AsyncClient | None = None


def _client() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=20.0)
    return _http


def tts_configured() -> bool:
    return bool(os.getenv("YANDEX_TTS_API_KEY", "").strip() or os.getenv("OPENROUTER_API_KEY", "").strip())


# Голоса Yandex SpeechKit и их интонации (role), по документации Яндекса.
VOICES = {
    "alena": ["neutral", "good"], "jane": ["neutral", "good", "evil"], "omazh": ["neutral", "evil"],
    "marina": ["neutral", "whisper", "friendly"], "dasha": ["neutral", "good", "friendly"],
    "julia": ["neutral", "strict"], "lera": ["neutral", "friendly"], "masha": ["good", "strict", "friendly"],
    "alexandra": ["neutral"],
    "filipp": ["neutral"], "ermil": ["neutral", "good"], "zahar": ["neutral", "good"], "kirill": ["neutral", "strict", "good"],
    "anton": ["neutral", "good"], "alexander": ["neutral", "good"], "madirus": ["neutral"],
}
# ElevenLabs через OpenRouter: голос с префиксом «el:». Голоса многоязычные, по-русски говорят все.
ELEVEN_VOICES = ["sarah", "alice", "jessica", "laura", "lily", "matilda", "bella",
                 "george", "adam", "brian", "daniel", "eric", "liam", "roger", "will"]
ELEVEN_MODEL = os.getenv("VOICE_ELEVEN_MODEL", "elevenlabs/eleven-flash-v2.5")
OPENROUTER_TTS_URL = "https://openrouter.ai/api/v1/audio/speech"


def voices() -> dict[str, list[str]]:
    """Доступные голоса: Яндекс, если есть его ключ, и ElevenLabs, если есть ключ OpenRouter."""
    out = dict(VOICES) if os.getenv("YANDEX_TTS_API_KEY", "").strip() else {}
    if os.getenv("OPENROUTER_API_KEY", "").strip():
        out.update({f"el:{name}": [] for name in ELEVEN_VOICES})
    return out


TTS_RATE = int(os.getenv("VOICE_TTS_RATE", "48000"))  # браузеру 48 кГц, телефону потом хватит 8
TTS_BYTES_PER_SECOND = TTS_RATE * 2
YANDEX_TTS_V3_URL = "https://tts.api.cloud.yandex.net/tts/v3/utteranceSynthesis"


@dataclass
class Voice:
    name: str
    role: str = ""
    speed: float = 1.0


class TTSRequestError(RuntimeError):
    def __init__(self, status: int, text: str) -> None:
        self.status = status
        super().__init__(f"Yandex SpeechKit {status}: {text[:300]}")


def _auth() -> dict:
    headers = {"Authorization": f"Api-Key {os.environ['YANDEX_TTS_API_KEY']}"}
    folder_id = os.getenv("YANDEX_TTS_FOLDER_ID", "").strip()
    if folder_id:
        headers["x-folder-id"] = folder_id
    return headers


async def _yandex_v3(text: str, voice: Voice) -> bytes:
    hints = [{"voice": voice.name}, {"speed": str(voice.speed)}]
    if voice.role:
        hints.append({"role": voice.role})
    body = {
        "text": text,
        "hints": hints,
        "outputAudioSpec": {"rawAudio": {"audioEncoding": "LINEAR16_PCM", "sampleRateHertz": TTS_RATE}},
        "loudnessNormalizationType": "LUFS",
    }
    r = await _client().post(YANDEX_TTS_V3_URL, headers=_auth(), json=body)
    if r.status_code != 200:
        raise TTSRequestError(r.status_code, r.text)
    audio = bytearray()
    for line in r.text.splitlines():  # ответ: по JSON-объекту на строку, звук кусками в base64
        if not line.strip():
            continue
        item = json.loads(line)
        if "error" in item:
            raise TTSRequestError(200, json.dumps(item["error"], ensure_ascii=False))
        chunk = ((item.get("result") or {}).get("audioChunk") or {}).get("data")
        if chunk:
            audio += base64.b64decode(chunk)
    if not audio:
        raise TTSRequestError(200, "пустой ответ")
    return bytes(audio)


async def _yandex_v1(text: str, voice: Voice) -> bytes:
    data = {"text": text, "lang": "ru-RU", "voice": voice.name, "format": "lpcm",
            "sampleRateHertz": str(TTS_RATE), "speed": str(voice.speed)}
    if voice.role in ("neutral", "good", "evil"):
        data["emotion"] = voice.role
    folder_id = os.getenv("YANDEX_TTS_FOLDER_ID", "").strip()
    if folder_id:
        data["folderId"] = folder_id
    r = await _client().post(YANDEX_TTS_URL, headers={"Authorization": _auth()["Authorization"]}, data=data)
    if r.status_code != 200:
        raise TTSRequestError(r.status_code, r.text)
    return r.content


def resample(pcm: bytes, src: int, dst: int) -> bytes:
    """Линейная передискретизация 16-битного моно PCM."""
    if src == dst or not pcm:
        return pcm
    import array
    x = array.array("h", pcm[: len(pcm) // 2 * 2])
    n = int(len(x) * dst / src)
    out = array.array("h", bytes(2 * n))
    step = src / dst
    last = len(x) - 1
    for i in range(n):
        pos = i * step
        j = int(pos)
        k = j + 1 if j < last else last
        frac = pos - j
        out[i] = int(x[j] + (x[k] - x[j]) * frac)
    return out.tobytes()


async def _eleven(text: str, voice: Voice) -> bytes:
    body = {"model": ELEVEN_MODEL, "input": text, "voice": voice.name.removeprefix("el:"), "response_format": "pcm"}
    if abs(voice.speed - 1.0) > 0.01:
        body["speed"] = voice.speed
    r = await _client().post(OPENROUTER_TTS_URL, json=body,
                             headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"})
    if r.status_code != 200:
        raise TTSRequestError(r.status_code, r.text)
    # Частота PCM: из заголовка, если провайдер её пишет (audio/pcm;rate=24000), иначе из настройки.
    match = re.search(r"rate=(\d+)", r.headers.get("content-type", ""))
    rate = int(match.group(1)) if match else int(os.getenv("VOICE_ELEVEN_PCM_RATE", "24000"))
    return resample(r.content, rate, TTS_RATE)


def _cache_path(text: str, voice: Voice):
    """Файл кэша фразы. Одинаковые фразы (приветствие, вопросы анкеты, прощание) синтезируются один раз."""
    folder = os.getenv("VOICE_TTS_CACHE", ".voice_tts_cache")
    if folder in ("", "off"):
        return None
    import hashlib
    from pathlib import Path
    model = ELEVEN_MODEL if voice.name.startswith("el:") else os.getenv("VOICE_TTS_API", "v3")
    key = json.dumps([model, voice.name, voice.role, round(voice.speed, 2), TTS_RATE, " ".join(text.split())], ensure_ascii=False)
    return Path(folder) / (hashlib.sha256(key.encode()).hexdigest() + ".pcm")


async def synthesize_pcm(text: str, voice: Voice) -> bytes:
    """Фраза в PCM TTS_RATE Гц, 16 бит, моно, с кэшем на диске."""
    path = _cache_path(text, voice)
    if path is not None and path.exists():
        return path.read_bytes()
    pcm = await _synthesize(text, voice)
    if path is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(pcm)
            tmp.replace(path)
        except OSError as e:
            log.warning("кэш синтеза не записан: %s", e)
    return pcm


async def _synthesize(text: str, voice: Voice) -> bytes:
    """Яндекс: сначала API v3 (там интонации), при отказе доступа v1. Голоса «el:» через OpenRouter."""
    if voice.name.startswith("el:"):
        return await _eleven(text, voice)
    if os.getenv("VOICE_TTS_API", "v3") == "v3":
        try:
            return await _yandex_v3(text, voice)
        except TTSRequestError as e:
            if e.status not in (401, 403, 404):
                raise
            log.warning("SpeechKit v3 недоступен (%s), синтезирую через v1", e)
    return await _yandex_v1(text, voice)


# ------------------------------------------------------------------ распознавание


def deepgram_url() -> str:
    params = {
        "model": os.getenv("VOICE_DG_MODEL", os.getenv("DG_MODEL", "nova-2")),
        "language": os.getenv("DG_LANG", "ru"),
        "encoding": "linear16",
        "sample_rate": SAMPLE_RATE,
        "channels": 1,
        "interim_results": "true",
        "smart_format": "true",
        "punctuate": "true",
        "endpointing": os.getenv("VOICE_ENDPOINTING_MS", "300"),
        "utterance_end_ms": "1000",
        "vad_events": "true",
    }
    return DEEPGRAM_WS + "?" + urlencode(params)


@dataclass
class Heard:
    text: str
    final: bool  # True: реплика клиента закончена, пора отвечать


class UtteranceCollector:
    """Склеивает сообщения Deepgram в реплики: is_final копим, speech_final или UtteranceEnd закрывают реплику."""

    def __init__(self) -> None:
        self.parts: list[str] = []

    def handle(self, message: dict) -> Heard | None:
        kind = message.get("type")
        if kind == "UtteranceEnd":
            return self._close()
        if kind != "Results":
            return None
        alternatives = message.get("channel", {}).get("alternatives") or [{}]
        text = (alternatives[0].get("transcript") or "").strip()
        if message.get("is_final"):
            if text:
                self.parts.append(text)
            if message.get("speech_final"):
                return self._close()
            return Heard(" ".join(self.parts), False) if text else None
        if text:
            return Heard(" ".join(self.parts + [text]), False)
        return None

    def _close(self) -> Heard | None:
        if not self.parts:
            return None
        text, self.parts = " ".join(self.parts), []
        return Heard(text, True)


class DeepgramStream:
    """Потоковое распознавание: send(pcm) на вход, async for heard in stream на выходе."""

    def __init__(self, session) -> None:  # aiohttp.ClientSession
        self.session = session
        self.ws = None
        self.collector = UtteranceCollector()

    async def open(self) -> None:
        self.ws = await self.session.ws_connect(
            deepgram_url(), headers={"Authorization": f"Token {os.environ['DEEPGRAM_API_KEY']}"}, heartbeat=10)

    async def send(self, pcm: bytes) -> None:
        if self.ws is not None and not self.ws.closed:
            await self.ws.send_bytes(pcm)

    async def close(self) -> None:
        if self.ws is not None and not self.ws.closed:
            try:
                await self.ws.send_str(json.dumps({"type": "CloseStream"}))
            except Exception:
                pass
            await self.ws.close()

    async def __aiter__(self) -> AsyncIterator[Heard]:
        import aiohttp
        async for msg in self.ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                heard = self.collector.handle(json.loads(msg.data))
                if heard:
                    yield heard
            elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                break


async def keepalive(stream: DeepgramStream, every: float = 5.0) -> None:
    while stream.ws is not None and not stream.ws.closed:
        await asyncio.sleep(every)
        try:
            await stream.ws.send_str(json.dumps({"type": "KeepAlive"}))
        except Exception:
            return
