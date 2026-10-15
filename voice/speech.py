"""
Звук для голосового оператора: потоковое распознавание Deepgram и синтез
Yandex SpeechKit в сырой PCM (16 кГц, 16 бит, моно), плюс нарезка ответа
модели на фразы, чтобы озвучка начиналась с первого предложения.

Ключи те же, что у бота: DEEPGRAM_API_KEY и YANDEX_TTS_API_KEY.
"""

from __future__ import annotations

import asyncio
import contextvars
import base64
import json
import logging
import os
import re
import time
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


async def warm_up_tts(voice: "Voice") -> None:
    """Открыть соединение с синтезом заранее: первая фраза без прогрева ждала TLS-рукопожатие, до 1,5 с."""
    url = f"{ELEVEN_API}/models" if voice.name.startswith("11:") else "https://tts.api.cloud.yandex.net/"
    try:
        await _client().head(url, timeout=5.0)
    except Exception as e:  # прогрев не обязателен
        log.info("прогрев синтеза не удался: %s", e)


def tts_configured() -> bool:
    return any(os.getenv(k, "").strip() for k in ("YANDEX_TTS_API_KEY", "OPENROUTER_API_KEY", "ELEVENLABS_API_KEY"))


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


# ElevenLabs напрямую (ELEVENLABS_API_KEY): голоса аккаунта, включая клоны, с префиксом «11:».
ELEVEN_API = "https://api.elevenlabs.io/v1"
ELEVEN_DIRECT_MODEL = os.getenv("VOICE_ELEVEN_DIRECT_MODEL", "eleven_flash_v2_5")
_eleven_direct: dict[str, str] = {}  # имя голоса -> voice_id


async def load_eleven_voices() -> dict[str, str]:
    """Голоса аккаунта ElevenLabs. Ошибку не бросает: без списка голоса можно задать по voice_id."""
    key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not key:
        return {}
    try:
        r = await _client().get(ELEVEN_API + "/voices", headers={"xi-api-key": key})
        r.raise_for_status()
        _eleven_direct.clear()
        _eleven_direct.update({v["name"].strip(): v["voice_id"] for v in r.json().get("voices", []) if v.get("voice_id")})
    except Exception as e:
        log.warning("не получил голоса ElevenLabs: %s", e)
    return dict(_eleven_direct)


def voices() -> dict[str, list[str]]:
    """Доступные голоса: Яндекс, ElevenLabs через OpenRouter, ElevenLabs напрямую, смотря какие есть ключи."""
    out = dict(VOICES) if os.getenv("YANDEX_TTS_API_KEY", "").strip() else {}
    if os.getenv("OPENROUTER_API_KEY", "").strip():
        out.update({f"el:{name}": [] for name in ELEVEN_VOICES})
    out.update({f"11:{name}": [] for name in _eleven_direct})
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
    def __init__(self, status: int, text: str, service: str = "Yandex SpeechKit") -> None:
        self.status = status
        super().__init__(f"{service} {status}: {text[:300]}")


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
        raise TTSRequestError(r.status_code, r.text, "ElevenLabs")
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
    model = {"el:": ELEVEN_MODEL, "11:": str(eleven_params())}.get(
        voice.name[:3], os.getenv("VOICE_TTS_API", "v3"))
    key = json.dumps([model, voice.name, voice.role, round(voice.speed, 2), TTS_RATE, " ".join(text.split())], ensure_ascii=False)
    return Path(folder) / (hashlib.sha256(key.encode()).hexdigest() + ".pcm")


def _find_eleven(name: str) -> str | None:
    """Голос аккаунта по имени: точно, без учёта регистра или по началу («Eva» найдёт «Eva - calm»)."""
    if name in _eleven_direct:
        return _eleven_direct[name]
    low = name.casefold()
    for known, voice_id in _eleven_direct.items():
        if known.casefold() == low or known.casefold().startswith(low):
            return voice_id
    return None


async def _eleven_voice_id(name: str) -> str:
    found = _find_eleven(name)
    if found is None and re.fullmatch(r"[A-Za-z0-9]{20}", name):
        return name  # voice_id из Voice Library («Copy voice ID»), список голосов не нужен
    if found is None:  # телефонный процесс список голосов заранее не грузит
        await load_eleven_voices()
        found = _find_eleven(name)
    if found is None and _eleven_direct:
        raise TTSRequestError(404, f"голоса «{name}» нет в My Voices, есть: {', '.join(sorted(_eleven_direct))[:250]}",
                              "ElevenLabs")
    return found or name  # можно указать сразу voice_id


# Настройки голоса ElevenLabs (как ползунки в их редакторе). Стабильность выше — интонация ровнее от фразы
# к фразе, но суше; ниже — живее, но фразы звучат по-разному.
ELEVEN_SETTINGS = {
    "stability": float(os.getenv("VOICE_ELEVEN_STABILITY", "0.8")),
    "similarity_boost": float(os.getenv("VOICE_ELEVEN_SIMILARITY", "0.8")),
    "style": float(os.getenv("VOICE_ELEVEN_STYLE", "0")),
}
# Что робот сказал перед этой фразой: ElevenLabs продолжает ту же интонацию, а не начинает заново
previous_phrase: contextvars.ContextVar[str] = contextvars.ContextVar("previous_phrase", default="")
# id прошлых синтезов этого звонка (request stitching ElevenLabs): список на звонок, None вне звонка
tts_chain: contextvars.ContextVar[list | None] = contextvars.ContextVar("tts_chain", default=None)
ELEVEN_SEED = int(os.getenv("VOICE_ELEVEN_SEED", "4242"))


# Настройки голоса со страницы /voice: файл VOICE_SETTINGS_FILE перечитывается при изменении, звонки
# подхватывают его без перезапуска. Пустые поля — значения из .env.
SETTINGS_FILE = os.getenv("VOICE_SETTINGS_FILE", "")
_live_cache: dict = {"mtime": None, "data": {}}
# Несохранённые настройки для «Прослушать» на странице
settings_override: contextvars.ContextVar[dict | None] = contextvars.ContextVar("settings_override", default=None)
SETTING_KEYS = {"voice_id": str, "model": str, "stability": float, "similarity": float, "style": float,
                "speed": float, "speaker_boost": bool, "calm": bool, "tail_ms": int, "filler_after_ms": int,
                "fillers": bool}


def clean_settings(data: dict) -> dict:
    """Только известные поля нужного типа, в допустимых пределах."""
    out = {}
    for key, kind in SETTING_KEYS.items():
        value = data.get(key)
        if value is None or value == "":
            continue
        try:
            value = kind(value) if kind is not bool else bool(value)
        except (TypeError, ValueError):
            continue
        out[key] = value
    for key, low, high in (("stability", 0, 1), ("similarity", 0, 1), ("style", 0, 1), ("speed", 0.7, 1.2),
                           ("tail_ms", 300, 2000), ("filler_after_ms", 100, 3000)):
        if key in out:
            out[key] = min(max(out[key], low), high)
    if "voice_id" in out and not re.fullmatch(r"[A-Za-z0-9]{10,40}", out["voice_id"]):
        del out["voice_id"]
    if "model" in out and not re.fullmatch(r"eleven_[a-z0-9_]{2,40}", out["model"]):
        del out["model"]
    return out


def live_settings() -> dict:
    if not SETTINGS_FILE:
        return {}
    try:
        mtime = os.stat(SETTINGS_FILE).st_mtime
    except OSError:
        return {}
    if mtime != _live_cache["mtime"]:
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                _live_cache["data"] = clean_settings(json.load(f))
        except (OSError, ValueError) as e:
            log.warning("настройки голоса %s не прочитаны: %s", SETTINGS_FILE, e)
            _live_cache["data"] = {}
        _live_cache["mtime"] = mtime
    return _live_cache["data"]


def setting(key: str, default):
    override = settings_override.get()
    return (override if override is not None else live_settings()).get(key, default)


def save_settings(data: dict) -> dict:
    data = clean_settings(data)
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, SETTINGS_FILE)
    return data


def eleven_params() -> tuple[str, dict]:
    """Модель и ползунки ElevenLabs с учётом страницы настроек."""
    return setting("model", ELEVEN_DIRECT_MODEL), {
        "stability": setting("stability", ELEVEN_SETTINGS["stability"]),
        "similarity_boost": setting("similarity", ELEVEN_SETTINGS["similarity_boost"]),
        "style": setting("style", ELEVEN_SETTINGS["style"]),
        "use_speaker_boost": setting("speaker_boost", True),
    }


def effective_voice(voice: Voice) -> Voice:
    """Голос ElevenLabs и темп со страницы настроек. Голоса Яндекса (автотест) не трогаем."""
    if not voice.name.startswith("11:"):
        return voice
    voice_id = setting("voice_id", "")
    return Voice(f"11:{voice_id}" if voice_id else voice.name, voice.role, setting("speed", voice.speed))


async def _eleven_direct_tts(text: str, voice: Voice) -> bytes:
    voice_id = await _eleven_voice_id(voice.name.removeprefix("11:"))
    rate = int(os.getenv("VOICE_ELEVEN_PCM_RATE", "24000"))
    model, sliders = eleven_params()
    body = {"text": text, "model_id": model, "language_code": "ru",
            "voice_settings": {**sliders, "speed": min(max(voice.speed, 0.7), 1.2)}}
    continuity = {}
    if ELEVEN_SEED:
        continuity["seed"] = ELEVEN_SEED  # одно «зерно» на все фразы: голос не гуляет от фразы к фразе
    chain = tts_chain.get()
    if chain:  # продолжение прошлых фраз этого звонка: та же интонация весь разговор
        continuity["previous_request_ids"] = chain[-3:]
    elif previous_phrase.get():
        continuity["previous_text"] = previous_phrase.get()[-300:]
    url, params = f"{ELEVEN_API}/text-to-speech/{voice_id}", {"output_format": f"pcm_{rate}"}
    headers = {"xi-api-key": os.environ["ELEVENLABS_API_KEY"]}
    r = await _client().post(url, params=params, headers=headers, json={**body, **continuity})
    if r.status_code == 400 and continuity:  # модель не умеет связку: озвучиваем фразу отдельно
        log.warning("ElevenLabs не принял связку фраз (%s): %s", ", ".join(continuity), r.text[:200])
        r = await _client().post(url, params=params, headers=headers, json=body)
    if r.status_code != 200:
        raise TTSRequestError(r.status_code, r.text, "ElevenLabs")
    if chain is not None and r.headers.get("request-id"):
        chain.append(r.headers["request-id"])
    return resample(r.content, rate, TTS_RATE)


# Восклицательный знак синтезатор читает радостно-приподнято, и это выдаёт робота: говорим ровно
CALM = os.getenv("VOICE_CALM", "1") != "0"
_CHEERFUL = re.compile(r"\b(Отлично|Замечательно|Прекрасно|Супер)\b", re.IGNORECASE)


def calm_text(text: str) -> str:
    if not setting("calm", CALM):
        return text
    text = re.sub(r"!+", ".", text)
    text = _CHEERFUL.sub(lambda m: "Хорошо" if m.group(0)[0].isupper() else "хорошо", text)
    return re.sub(r"\b(Хорошо|хорошо)([,.]\s*хорошо)+\b", r"\1", text, flags=re.IGNORECASE)


async def synthesize_pcm(text: str, voice: Voice) -> bytes:
    """Фраза в PCM TTS_RATE Гц, 16 бит, моно, с кэшем на диске."""
    text = calm_text(text)
    voice = effective_voice(voice)
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


YANDEX_MAX_CHARS = 200  # SpeechKit v3 отвечает «Too long text» на длинные фразы


def split_long(text: str, limit: int = YANDEX_MAX_CHARS) -> list[str]:
    """Режет текст на куски не длиннее limit: по концу предложения, иначе по запятой, иначе по пробелу."""
    text = " ".join(text.split())
    parts = []
    while len(text) > limit:
        window = text[:limit + 1]
        cut = max(window.rfind(". "), window.rfind("! "), window.rfind("? "), window.rfind("… "))
        if cut < limit // 3:
            cut = window.rfind(", ")
        if cut < limit // 3:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = limit - 1
        parts.append(text[:cut + 1].strip())
        text = text[cut + 1:].strip()
    if text:
        parts.append(text)
    return parts


async def _synthesize(text: str, voice: Voice) -> bytes:
    if not voice.name.startswith(("el:", "11:")) and len(text) > YANDEX_MAX_CHARS:
        return b"".join([await _synthesize_one(part, voice) for part in split_long(text)])
    return await _synthesize_one(text, voice)


async def _synthesize_one(text: str, voice: Voice) -> bytes:
    """Яндекс: сначала API v3 (там интонации), при отказе доступа v1. Голоса «el:» через OpenRouter."""
    if voice.name.startswith("el:"):
        return await _eleven(text, voice)
    if voice.name.startswith("11:"):
        return await _eleven_direct_tts(text, voice)
    if os.getenv("VOICE_TTS_API", "v3") == "v3":
        try:
            return await _yandex_v3(text, voice)
        except TTSRequestError as e:
            if e.status not in (401, 403, 404):
                raise
            log.warning("SpeechKit v3 недоступен (%s), синтезирую через v1", e)
    return await _yandex_v1(text, voice)


# ------------------------------------------------------------------ распознавание


def deepgram_url(rate: int = SAMPLE_RATE) -> str:
    params = {
        "model": os.getenv("VOICE_DG_MODEL", os.getenv("DG_MODEL", "nova-2")),
        "language": os.getenv("DG_LANG", "ru"),
        "encoding": "linear16",
        "sample_rate": rate,
        "channels": 1,
        "interim_results": "true",
        # smart_format переводит числа в цифры и ради этого держит окончательный текст секундами
        # («около трёхсот тысяч» → «около 300 1000» через 6 с). Модели слова понятнее цифр.
        "smart_format": os.getenv("VOICE_DG_SMART_FORMAT", "false"),
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


TURN_GAP_S = int(os.getenv("VOICE_TURN_GAP_MS", "700")) / 1000  # 500 мс резали клиента между предложениями
# фраза без точки в конце («Город…») обычно не закончена: ждём дольше, чтобы не перебить клиента
OPEN_GAP_S = int(os.getenv("VOICE_TURN_GAP_OPEN_MS", "1000")) / 1000


MIN_CONFIDENCE = float(os.getenv("VOICE_MIN_CONFIDENCE", "0.5"))  # ниже — музыка, шум, чужие голоса
STALE_TAIL_S = int(os.getenv("VOICE_STALE_TAIL_MS", "3000")) / 1000  # страховка: 1,5 с резали клиента на полуслове


# После этих слов фраза почти наверняка не закончена: ждём продолжения дольше
HANGING_WORDS = {"город", "городе", "район", "районе", "улица", "в", "во", "на", "и", "а", "но", "или", "ну", "это",
                 "около", "примерно", "где", "гдето", "где-то", "наверное", "так", "мм", "эм", "э", "у", "с", "к",
                 "по", "до", "от", "за", "для", "что", "чтобы", "когда", "если", "потому", "зовут", "меня"}
HANGING_GAP_S = int(os.getenv("VOICE_HANGING_GAP_MS", "1800")) / 1000
# Короткие законченные ответы на вопросы да/нет: реплику закрываем почти сразу, без обычного ожидания
# Голое «да» сюда не входит: после него часто идёт продолжение («да, актуален, но я за рулём»), и ранний
# ответ на «да» уводил разговор на шаг вперёд (автотест fb7d8e1)
SHORT_ANSWERS = {"да конечно", "актуально", "актуален", "да актуально",
                 "да актуален", "не актуально", "неактуально", "уже не актуально", "нет не актуально",
                 "интересовался", "интересовалась", "да интересовался", "да интересовалась", "не интересовался",
                 "не интересовалась", "планирую", "планируем", "да планирую", "да планируем", "не планирую",
                 "не планируем", "подходит", "да подходит", "устраивает", "да устраивает", "да верно", "верно"}
SHORT_ANSWER_GAP_S = int(os.getenv("VOICE_SHORT_ANSWER_GAP_MS", "350")) / 1000


def _hanging(text: str) -> bool:
    words = re.sub(r"[^\w ]", " ", text.lower().replace("ё", "е")).split()
    return bool(words) and words[-1] in HANGING_WORDS


class UtteranceCollector:
    """Склеивает сообщения Deepgram в реплики: is_final копим, speech_final или UtteranceEnd закрывают реплику.
    По телефону speech_final часто не приходит (шум линии), а UtteranceEnd ждёт секунду и больше, поэтому
    реплику закрывает и пауза TURN_GAP_S после окончательного текста, если новых слов не слышно.
    Бывает, что окончательного текста нет секунды (числа: «около трёхсот тысяч»), есть только промежуточный,
    и он не меняется: такая реплика закрывается через STALE_TAIL_S по промежуточному тексту."""

    def __init__(self, gap: float = TURN_GAP_S, open_gap: float = OPEN_GAP_S, stale_tail: float = STALE_TAIL_S) -> None:
        self.parts: list[str] = []
        self.gap = gap
        self.open_gap = max(open_gap, gap)
        self.stale_tail = stale_tail
        self.tail = ""          # промежуточный текст после последнего окончательного: клиент ещё говорит
        self.tail_at = 0.0      # когда промежуточный текст последний раз менялся
        self.final_at = 0.0
        self.raw_tail = ""      # промежуточный текст как пришёл, с уже отвеченным началом
        self.closed_tail = ""   # реплику закрыли по промежуточному тексту: его запоздалый is_final не повторяем

    def gap_left(self, now: float) -> float | None:
        """Сколько ещё ждать до закрытия реплики по паузе; None, если закрывать нечего."""
        if self.gap <= 0:
            return None
        if self.tail:
            if self.stale_tail <= 0:
                return None
            wait = self.stale_tail
            if " ".join(_words(self.tail)) in SHORT_ANSWERS:
                wait = min(wait, SHORT_ANSWER_GAP_S)  # «да», «актуально»: ответ целиком, отвечаем сразу
            if _hanging(self.tail):
                wait = max(wait, HANGING_GAP_S)  # «город…», «где-то…»: клиент вспоминает продолжение
            return max(self.tail_at + wait - now, 0.0)
        if not self.parts:
            return None
        if _hanging(self.parts[-1]):
            return max(self.final_at + HANGING_GAP_S - now, 0.0)
        if " ".join(_words(" ".join(self.parts))) in SHORT_ANSWERS:
            return max(self.final_at + SHORT_ANSWER_GAP_S - now, 0.0)
        finished = self.parts[-1].rstrip().endswith((".", "!", "?", "…"))
        return max(self.final_at + (self.gap if finished else self.open_gap) - now, 0.0)

    def close_on_gap(self) -> Heard | None:
        return self._close()

    def handle(self, message: dict) -> Heard | None:
        kind = message.get("type")
        if kind == "UtteranceEnd":
            if self.parts and not self.tail and _hanging(self.parts[-1]):
                # распознавание считает фразу законченной на «город»: ждём название ещё HANGING_GAP_S
                self.final_at = time.monotonic()
                return None
            return self._close()
        if kind != "Results":
            return None
        alternatives = message.get("channel", {}).get("alternatives") or [{}]
        text = (alternatives[0].get("transcript") or "").strip()
        confidence = alternatives[0].get("confidence")
        if text and confidence is not None and 0 < confidence < MIN_CONFIDENCE:
            log.info("не верю распознаванию (%.2f), это шум: «%s»", confidence, text)
            text = ""
        if message.get("is_final"):
            self.tail = self.raw_tail = ""
            if text and self.closed_tail:
                text = _without_prefix(text, self.closed_tail)  # уже отвеченное начало не повторяем
                self.closed_tail = ""
                if not text:
                    return None
            if text:
                self.parts.append(text)
                self.final_at = time.monotonic()
            if message.get("speech_final"):
                return self._close()
            return Heard(" ".join(self.parts), False) if text else None
        self.raw_tail = text
        if text and self.closed_tail:
            # Промежуточный текст копится с начала фразы. После раннего закрытия распознавание присылает
            # уже отвеченное начало ещё раз: это не новая речь (автотест 03e93cc), берём только продолжение
            text = _without_prefix(text, self.closed_tail)
        if text != self.tail:
            self.tail, self.tail_at = text, time.monotonic()
        if text:
            return Heard(" ".join(self.parts + [text]), False)
        return None

    def _close(self) -> Heard | None:
        words = self.parts + ([self.tail] if self.tail else [])
        # отвеченным считаем весь накопленный текст фразы, а не только последний кусок: окончательный текст
        # Яндекса придёт целиком («ирина петровна завтра после шести»), повторять его нельзя (автотест 172f77a)
        self.closed_tail = self.raw_tail if self.tail else ""
        self.tail, self.parts, self.raw_tail = "", [], ""
        if not words:
            return None
        return Heard(" ".join(words), True)


def _words(text: str) -> list[str]:
    return re.sub(r"[^\w ]", " ", text.lower().replace("ё", "е")).split()


def _without_prefix(text: str, prefix: str) -> str:
    """Окончательный текст минус начало, на которое робот уже ответил. Не совпало начало — текст целиком."""
    words, head = text.split(), _words(prefix)
    norm = _words(text)
    if len(norm) == len(words) and norm[:len(head)] == head:
        return " ".join(words[len(head):])
    if norm and head[:len(norm)] == norm:  # повтор начала уже отвеченного текста
        return ""
    return text


class DeepgramStream:
    """Потоковое распознавание: send(pcm) на вход, async for heard in stream на выходе."""

    def __init__(self, session, rate: int = SAMPLE_RATE) -> None:  # aiohttp.ClientSession
        self.session = session
        self.rate = rate
        self.ws = None
        self.collector = UtteranceCollector()

    async def open(self) -> None:
        self.ws = await self.session.ws_connect(
            deepgram_url(self.rate), headers={"Authorization": f"Token {os.environ['DEEPGRAM_API_KEY']}"}, heartbeat=10)

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
        while True:
            wait = self.collector.gap_left(time.monotonic())
            if wait is not None:
                wait = max(wait, 0.001)  # aiohttp считает timeout=0 «без таймаута» и ждёт следующего сообщения
            try:
                msg = await self.ws.receive(timeout=wait)
            except asyncio.TimeoutError:
                heard = self.collector.close_on_gap()
                if heard:
                    log.info("реплика закрыта по паузе")
                    yield heard
                continue
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                _log_asr(data)
                heard = self.collector.handle(data)
                if heard:
                    yield heard
            elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING,
                              aiohttp.WSMsgType.CLOSED):
                break


def _log_asr(data: dict) -> None:
    """Что прислал Deepgram: окончательный текст, конец фразы, ошибки. Промежуточные только с VOICE_ASR_DEBUG=1."""
    kind = data.get("type")
    if kind == "Results":
        text = ((data.get("channel", {}).get("alternatives") or [{}])[0].get("transcript") or "").strip()
        final = data.get("is_final")
        if final or os.getenv("VOICE_ASR_DEBUG"):
            log.info("распознано%s%s: «%s»", " окончательно" if final else "", ", конец фразы" if data.get("speech_final") else "", text)
    elif kind in ("UtteranceEnd", "Error", "Warning"):
        log.info("Deepgram %s: %s", kind, json.dumps(data, ensure_ascii=False)[:200])


YANDEX_TAIL_S = int(os.getenv("VOICE_YANDEX_TAIL_MS", "800")) / 1000


class YandexStream:
    """Потоковое распознавание Yandex SpeechKit v3 (gRPC): русская речь по телефону понимается лучше Deepgram,
    конец фразы определяет сама модель (eou). Ответы переводятся в формат Deepgram, дальше тот же UtteranceCollector.
    Ключ YANDEX_STT_API_KEY или YANDEX_TTS_API_KEY (сервисному аккаунту нужна роль ai.speechkit-stt.user)."""

    HOST = "stt.api.cloud.yandex.net:443"

    def __init__(self, session=None, rate: int = SAMPLE_RATE) -> None:
        self.rate = rate
        self.ws = None  # keepalive() не нужен: звук идёт непрерывно
        # Конец реплики сообщает сам Яндекс (eou), но с запасом около секунды. Раньше закрываем сами, когда
        # промежуточный текст не меняется YANDEX_TAIL_S: 0,6 с резали клиента на полуслове (автотест 5ab28c4)
        self.collector = UtteranceCollector(stale_tail=setting("tail_ms", YANDEX_TAIL_S * 1000) / 1000)
        self.audio: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.events: asyncio.Queue[dict | None] = asyncio.Queue()
        self.channel = None
        self.reader: asyncio.Task | None = None

    async def open(self) -> None:
        import grpc
        from yandex.cloud.ai.stt.v3 import stt_pb2 as stt, stt_service_pb2_grpc as service
        key = (os.getenv("YANDEX_STT_API_KEY") or os.environ["YANDEX_TTS_API_KEY"]).strip()
        options = stt.StreamingOptions(
            recognition_model=stt.RecognitionModelOptions(
                model=os.getenv("VOICE_YANDEX_STT_MODEL", "general"),
                audio_format=stt.AudioFormatOptions(raw_audio=stt.RawAudio(
                    audio_encoding=stt.RawAudio.LINEAR16_PCM, sample_rate_hertz=self.rate, audio_channel_count=1)),
                text_normalization=stt.TextNormalizationOptions(
                    text_normalization=stt.TextNormalizationOptions.TEXT_NORMALIZATION_ENABLED,
                    profanity_filter=False, literature_text=False),
                language_restriction=stt.LanguageRestrictionOptions(
                    restriction_type=stt.LanguageRestrictionOptions.WHITELIST, language_code=["ru-RU"]),
                audio_processing_type=stt.RecognitionModelOptions.REAL_TIME),
            eou_classifier=stt.EouClassifierOptions(default_classifier=stt.DefaultEouClassifier(
                type=stt.DefaultEouClassifier.HIGH,
                max_pause_between_words_hint_ms=int(os.getenv("VOICE_YANDEX_EOU_PAUSE_MS", "600")))))

        async def requests():
            yield stt.StreamingRequest(session_options=options)
            while True:
                pcm = await self.audio.get()
                if pcm is None:
                    return
                yield stt.StreamingRequest(chunk=stt.AudioChunk(data=pcm))

        self.channel = grpc.aio.secure_channel(self.HOST, grpc.ssl_channel_credentials())
        metadata = [("authorization", f"Api-Key {key}")]
        folder = (os.getenv("YANDEX_FOLDER_ID") or os.getenv("YANDEX_TTS_FOLDER_ID") or "").strip()
        if folder:
            metadata.append(("x-folder-id", folder))
        call = service.RecognizerStub(self.channel).RecognizeStreaming(requests(), metadata=metadata)
        self.reader = asyncio.create_task(self._read(call))

    async def _read(self, call) -> None:
        try:
            async for r in call:
                kind = r.WhichOneof("Event")
                if kind in ("partial", "final"):
                    alts = getattr(r, kind).alternatives
                    text = alts[0].text if alts else ""
                    conf = alts[0].confidence if alts else 0.0
                    await self.events.put({"type": "Results", "is_final": kind == "final", "speech_final": False,
                                           "channel": {"alternatives": [{"transcript": text, "confidence": conf}]}})
                elif kind == "eou_update":
                    await self.events.put({"type": "UtteranceEnd"})
        except Exception as e:
            log.warning("Yandex SpeechKit: распознавание оборвалось: %s", e)
        finally:
            await self.events.put(None)

    async def send(self, pcm: bytes) -> None:
        await self.audio.put(pcm)

    async def close(self) -> None:
        await self.audio.put(None)
        if self.reader is not None:
            try:
                await asyncio.wait_for(self.reader, 3)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self.reader.cancel()
        if self.channel is not None:
            await self.channel.close()

    async def __aiter__(self) -> AsyncIterator[Heard]:
        while True:
            wait = self.collector.gap_left(time.monotonic())
            try:
                data = await (asyncio.wait_for(self.events.get(), max(wait, 0.001)) if wait is not None
                              else self.events.get())
            except asyncio.TimeoutError:
                heard = self.collector.close_on_gap()
                if heard:
                    yield heard
                continue
            if data is None:
                return
            _log_asr(data)
            heard = self.collector.handle(data)
            if heard:
                yield heard


def asr_stream(session, rate: int = SAMPLE_RATE):
    """Распознавание по VOICE_ASR: yandex (SpeechKit) или deepgram. Нет библиотек для Яндекса — Deepgram."""
    if os.getenv("VOICE_ASR", "deepgram").strip().lower() == "yandex":
        try:
            import grpc  # noqa: F401
            from yandex.cloud.ai.stt.v3 import stt_pb2  # noqa: F401
            return YandexStream(session, rate)
        except ImportError as e:
            log.warning("VOICE_ASR=yandex, но нет библиотек (%s): распознаю через Deepgram", e)
    return DeepgramStream(session, rate)


async def keepalive(stream, every: float = 5.0) -> None:
    while stream.ws is not None and not stream.ws.closed:
        await asyncio.sleep(every)
        try:
            await stream.ws.send_str(json.dumps({"type": "KeepAlive"}))
        except Exception:
            return
