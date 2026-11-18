"""Conservative local matching for approved, prerecorded campaign replies."""
import base64
import binascii
import re
import hashlib
import json
import os


def normalize(text):
    return " ".join(re.findall(r"[\w]+", str(text).lower().replace("ё", "е")))


PRICE_VARIANTS = {"сколько стоит", "сколько это стоит", "какая цена", "какова цена"}
SHORT_EQUIVALENTS = (
    frozenset(('где производство', 'где ваше производство', 'где находится производство')),
    frozenset(('вы производитель', 'вы производители', 'вы сами производитель')),
    frozenset(('делаете печать', 'делаете ли печать', 'вы делаете печать')),
)
PACKAGING_PRICE = re.compile(r"(?:сколько стоит|сколько стоят|какая цена|какова цена) (?:коробка|коробки|коробок|упаковка|упаковки)")


# Each prototype is deliberately narrow: a match requires an approved trigger
# from the SAME family. Logo approval never implicitly authorizes offset print.
SEMANTIC_PATTERNS = {
    'print_general': re.compile(r'(?:какая|какую) печать (?:возможна|возможна у вас|возможна на коробках|делаете|вы делаете|есть|у вас есть)|(?:какая|какую) (?:у вас )?печать(?: возможна| есть)?'),
    'print_logo': re.compile(r'(?:можно|можете|вы можете) (?:нанести|печатать|напечатать) логотип(?: на коробке| на коробках)?|(?:наносите|наносите ли|делаете|печатаете) логотип(?: на коробках)?'),
    'print_offset': re.compile(r'(?:у вас )?(?:есть )?(?:офсетная|афсетная) печать(?: есть| возможна| у вас)?|(?:офсетную|афсетную) печать (?:делаете|вы делаете|можно|можете)|(?:вы )?делаете (?:офсетную|афсетную) печать'),
    'assortment': re.compile(r'что (?:вы |вы вообще )?(?:производите|изготавливаете|делаете)|(?:вы )?(?:картон|коробки|упаковку) производите или что|(?:вы )?производите (?:картон|коробки|упаковку) или что'),
}


def semantic_intent(text):
    value = normalize(text)
    # Only discourse fillers, not qualifiers, may be dropped.
    value = re.sub(r'^(?:(?:а|ну|подскажите|скажите|пожалуйста) )+', '', value)
    if re.search(r'\b(?:не|нет|без|кроме|вместо|только|исключая)\b', value):
        return None
    if len(value.split()) > 9:
        return None
    matched = [key for key, pattern in SEMANTIC_PATTERNS.items() if pattern.fullmatch(value)]
    return matched[0] if len(matched) == 1 else None


def signature(voice, text=""):
    """Fingerprint effective synthesis, including live settings and PCM rate."""
    from voice import speech, presets
    effective = speech.effective_voice(voice, speech.spoken_text(text))
    override = speech.settings_override.get()
    settings = dict(override if override is not None else speech.live_settings())
    env_keys = ("VOICE_VOICE", "VOICE_SPEED", "VOICE_PRESET", "VOICE_TTS_RATE", "VOICE_TTS_API",
                "VOICE_YANDEX_VOICE", "VOICE_YANDEX_ROLE", "VOICE_ELEVEN_PCM_RATE", "VOICE_ELEVEN_MODEL")
    payload = {"format": 2, "voice": [effective.name, effective.role, effective.speed],
               "sample_rate": speech.TTS_RATE, "preset": presets.current(),
               "tts_model": presets.tts_model(), "eleven": speech.eleven_params(),
               "settings": settings, "env": {key: os.environ.get(key) for key in env_keys}}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def prepare(items, sample_rate, voice_signature=None):
    prepared = []
    if not isinstance(items, list):
        return prepared
    for item in items[:500]:
        if not isinstance(item, dict) or item.get("sample_rate") != sample_rate or not isinstance(item.get("reply"), str):
            continue
        if voice_signature is not None and item.get("voice_signature") != (voice_signature(item["reply"]) if callable(voice_signature) else voice_signature):
            continue
        try:
            audio = base64.b64decode(item.get("audio_pcm_b64", ""), validate=True)
        except (ValueError, TypeError, binascii.Error):
            continue
        if not audio or len(audio) % 2 or len(audio) > sample_rate * 2 * 120:
            continue
        if item.get("next_action", "reply") != "reply":
            continue
        triggers = item.get("triggers")
        reply = item.get("reply")
        if not item.get("id") or not item.get("version_id") or not isinstance(reply, str) or not reply.strip() or not isinstance(triggers, list):
            continue
        normalized = {normalize(t) for t in triggers if isinstance(t, str) and len(normalize(t).split()) >= 2}
        if not normalized:
            continue
        contexts = item.get("context_terms", [])
        contacts = item.get("contact_terms", [])
        if not isinstance(contexts, list) or not isinstance(contacts, list) or any(not isinstance(v, str) or not normalize(v) for v in contexts + contacts):
            continue
        prepared.append({"id": item["id"], "version_id": item["version_id"], "reply": reply.strip(),
                         "triggers": normalized, "audio": audio, "voice_signature": item.get("voice_signature"),
                         "contexts": [normalize(x) for x in contexts], "contacts": [normalize(x) for x in contacts]})
    return prepared


def match(items, text, messages, contact=""):
    phrase = normalize(text)
    # Stop requests and corrections must reach the normal dialogue / outcome path.
    if re.search(r'\b(?:не звоните|не звонить|больше не звоните|больше не звонить|хватит звонить|не беспокойте|удалите номер|удалите мой номер|уберите номер|уберите мой номер)\b', phrase):
        return None, 0.0
    history = " ".join(normalize(m.get("content", "")) for m in messages if isinstance(m, dict) and isinstance(m.get("content"), str))
    contact = normalize(contact)
    candidates = [item for item in items if (phrase in item["triggers"] or
                  phrase in PRICE_VARIANTS and bool(item["triggers"] & PRICE_VARIANTS) or
                  PACKAGING_PRICE.fullmatch(phrase) is not None and any(PACKAGING_PRICE.fullmatch(x) for x in item["triggers"]) or
                  any(phrase in group and bool(group & item["triggers"]) for group in SHORT_EQUIVALENTS) or
                  semantic_intent(phrase) is not None and any(semantic_intent(trigger) == semantic_intent(phrase) for trigger in item["triggers"]))
                  and all(term in history for term in item["contexts"])
                  and all(term in contact for term in item["contacts"])]
    # A recorded follow-up question is unsafe if the customer already supplied the fact.
    client_history = ' '.join(normalize(m.get('content', '')) for m in messages
                              if isinstance(m, dict) and m.get('role') == 'user' and isinstance(m.get('content'), str))
    client_history = (client_history + ' ' + phrase).strip()
    filtered = []
    for item in candidates:
        questions = ' '.join(part for part in item['reply'].split('?')[:-1])
        needs_specs = bool(re.search(r'размер|тираж|количеств|сколько штук', questions, re.I))
        needs_time = bool(re.search(r'когда|удобн.{0,20}врем|во сколько', questions, re.I))
        has_specs = bool(re.search(r'\d|размер|тираж|количеств|штук|партия', client_history))
        has_time = bool(contact or re.search(r'\d|сегодня|завтра|послезавтра|понедельник|вторник|среду|четверг|пятниц|суббот|воскрес|утром|вечером|после обеда|часов', client_history))
        if needs_specs and has_specs or needs_time and has_time:
            continue
        filtered.append(item)
    # Ambiguous versions or answers never auto-play.
    return (filtered[0], 1.0 if phrase in filtered[0]["triggers"] else 0.95) if len(filtered) == 1 else (None, 0.0)
