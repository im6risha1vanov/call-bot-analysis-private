"""
Диалог голосового оператора: реплики клиента на входе, реплики робота на выходе.

Модель отвечает потоком, чтобы синтез речи мог начать озвучку с первого
предложения. Звонок заканчивается только инструментом (finish_call или
transfer_to_specialist), поэтому итог всегда структурированный и сразу ложится
в таблицу. Какая модель отвечает, задаёт voice/llm.py (VOICE_LLM, VOICE_MODEL).
"""

from __future__ import annotations

import asyncio
import re
import logging
import time
from dataclasses import dataclass, field

from voice import presets
from voice.llm import BadToolJSON, TextSink, make_backend
from voice.operator import OUTCOMES, Operator

log = logging.getLogger("voice.dialog")

SILENCE = "(молчание)"

FINISH = "finish_call"
TRANSFER = "transfer_to_specialist"


@dataclass
class CallResult:
    outcome: str
    comment: str = ""
    facts: dict[str, str] = field(default_factory=dict)
    transfer: bool = False
    error: str | None = None


@dataclass
class Turn:
    text: str
    result: CallResult | None = None
    first_text_ms: int | None = None
    fallback: bool = False  # text не от модели, а прощание из анкеты


def build_tools(op: Operator) -> list[dict]:
    facts = {
        "type": "object",
        "description": "Данные, которые назвал клиент. Чего не было в разговоре, не указывай.",
        "properties": {key: {"type": "string", "description": text} for key, text in op.fields.items()},
        "additionalProperties": False,
    }
    comment = {"type": "string", "description": "Коротко для таблицы, до 160 символов: собранные данные или причина отказа."}
    tools = [{
        "name": FINISH,
        "description": "Завершить звонок и записать итог. Вызывай, когда разговор окончен по плану, правилам или по желанию клиента.",
        "input_schema": {
            "type": "object",
            "properties": {
                "outcome": {"type": "string", "enum": list(OUTCOMES), "description": "Итог звонка."},
                "comment": comment,
                "facts": facts,
            },
            "required": ["outcome", "comment"],
            "additionalProperties": False,
        },
    }]
    if op.transfer_to:
        tools.append({
            "name": TRANSFER,
            "description": "Перевести клиента на живого специалиста прямо сейчас. Только если клиент подходит и сам просит соединить с человеком. "
                           "Вопрос «вы робот?» — не просьба соединить: ответь и продолжай по плану.",
                "input_schema": {
                "type": "object",
                # описания полей уже есть в finish_call: здесь только имена, инструменты уходят модели в каждом запросе
                "properties": {"comment": comment, "facts": {**facts, "properties": {key: {"type": "string"} for key in op.fields}}},
                "required": ["comment"],
                "additionalProperties": False,
            },
        })
    return tools


def parse_result(name: str, data, op: Operator) -> CallResult:
    """Проверка входа инструмента: при потоковой передаче API его не валидирует."""
    if not isinstance(data, dict):
        raise ValueError("вход инструмента не объект")
    comment = data.get("comment")
    if not isinstance(comment, str) or not comment.strip():
        raise ValueError("нет comment")
    facts = data.get("facts") or {}
    if not isinstance(facts, dict) or any(not isinstance(v, str) for v in facts.values()):
        raise ValueError("facts должен быть объектом со строками")
    facts = {k: v.strip() for k, v in facts.items() if k in op.fields and v.strip()}
    if name == TRANSFER:
        return CallResult("interested", comment.strip()[:300], facts, transfer=True)
    if name != FINISH:
        raise ValueError(f"неизвестный инструмент {name}")
    outcome = data.get("outcome")
    if outcome not in OUTCOMES:
        raise ValueError(f"неизвестный итог {outcome!r}")
    return CallResult(outcome, comment.strip()[:300], facts)


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w ]", " ", text.lower().replace("ё", "е")).split())


QUICK_ECHO = "{answer}"  # в reply быстрого ответа: слова клиента («Сыктывкар, поняла.»)


def _afters(item: dict, key: str = "after") -> list[str]:
    after = item.get(key, "")
    return [after] if isinstance(after, str) else list(after)


def quick_answer(item: dict, asked: str, client_text: str) -> str | None:
    """Подходит ли ответ клиента к быстрому ответу item после вопроса робота asked (нормализован).
    Возвращает слова клиента для повтора (без «ну», «меня зовут»), None — не подходит, отвечает модель."""
    if not any(_norm(a) and _norm(a) in asked for a in _afters(item)):
        return None
    answer = _norm(client_text)
    if not answer:
        return None
    if item.get("answers") and answer in {_norm(a) for a in item["answers"]}:
        return answer
    if not item.get("match"):
        return None
    words = re.findall(r"\w+", client_text)
    if item.get("strip"):
        cut = re.match(item["strip"], answer)
        if cut and cut.end():
            dropped = len(answer[:cut.end()].split())
            answer, words = answer[cut.end():].strip(), words[dropped:]
    if not answer or len(answer.split()) > int(item.get("max_words", 6)):
        return None
    if not re.search(item["match"], answer) or (item.get("exclude") and re.search(item["exclude"], answer)):
        return None
    # распознавание часто пишет «ирина петровна»: имена и города с большой, предлоги («в», «из») как есть
    words = [w if (i and len(w) <= 2) or w[:1].isupper() else w[:1].upper() + w[1:] for i, w in enumerate(words)]
    return " ".join(words) or answer.capitalize()


def split_echo(reply: str) -> tuple[str, str]:
    """Реплика с {answer}: повтор до конца первого предложения (или до запятой, если предложение одно)
    и остальное — его синтезируем заранее."""
    at = reply.index(QUICK_ECHO) + len(QUICK_ECHO)
    stop = re.search(r"[.!?](\s|$)", reply[at:])
    if not stop or not reply[at + stop.end():].strip():
        stop = re.search(r",", reply[at:])
    cut = at + stop.end() if stop else len(reply)
    return reply[:cut].strip(), reply[cut:].strip()


def quick_static(reply: str) -> str:
    """Неизменная часть быстрого ответа: её синтезируем, пока идут гудки."""
    return split_echo(reply)[1] if QUICK_ECHO in reply else reply


class Dialog:
    def __init__(self, operator: Operator, backend=None):
        self.op = operator
        self.backend = backend or make_backend(model=presets.llm_model())  # модель выбранного набора, иначе из .env
        self.model = self.backend.model
        self.system = operator.system_prompt()
        self.tools = build_tools(operator)
        self.quick_parts: list[str] = []
        self.messages: list[dict] = []
        self.transcript: list[tuple[str, str]] = []  # ("client" | "robot", текст)
        self.result: CallResult | None = None
        self.cost = 0.0
        self.cost_unit = self.backend.unit
        self.started = time.monotonic()

    @property
    def finished(self) -> bool:
        return self.result is not None

    def greet(self, first_words: str = "Алло") -> str:
        """Робот ждёт «Алло» и говорит приветствие из анкеты (без модели, сразу)."""
        self._add_client(first_words or SILENCE)
        self._add_robot(self.op.greeting)
        return self.op.greeting

    def _add_client(self, text: str) -> None:
        text = text.strip() or SILENCE
        self.transcript.append(("client", text))
        if self.messages and self.messages[-1]["role"] == "user":
            self.messages[-1]["content"] += " " + text  # клиент перебил до того, как робот что-то сказал
        else:
            self.messages.append({"role": "user", "content": text})

    def _add_robot(self, text: str) -> None:
        self.transcript.append(("robot", text))
        self.messages.append({"role": "assistant", "content": text})

    def _finish(self, result: CallResult, spoken: str) -> Turn:
        self.result = result
        text = spoken.strip()
        if text or result.outcome == "ndz":
            return Turn(text, result)
        self.transcript.append(("robot", self.op.farewell))
        return Turn(self.op.farewell, result, fallback=True)

    def quick_reply(self, client_text: str) -> str | None:
        """Ожидаемый простой ответ на вопрос робота («да», город, имя): заготовка из анкеты, без модели.
        Модель увидит этот обмен в истории и продолжит с него. Части реплики для озвучки — в quick_parts:
        повтор слов клиента отдельно, остальное заранее синтезировано."""
        self.quick_parts = []
        if self.finished or not self.messages or self.messages[-1]["role"] != "assistant":
            return None
        asked = _norm(self.messages[-1]["content"])
        earlier = " | ".join(_norm(m["content"]) for m in self.messages[:-1]
                             if m["role"] == "assistant" and isinstance(m["content"], str))
        for item in self.op.quick:
            if any(_norm(u) and _norm(u) in earlier for u in _afters(item, "unless")):
                continue  # этот вопрос робот уже задавал раньше: следующий по заготовке был бы повтором
            answer = quick_answer(item, asked, client_text)
            if answer is None:
                continue
            reply = item["reply"]
            if QUICK_ECHO in reply:
                echo, rest = split_echo(reply)
                self.quick_parts = [echo.replace(QUICK_ECHO, answer), rest]
                reply = " ".join(p for p in self.quick_parts if p)
            else:
                self.quick_parts = [reply]
            self._add_client(client_text)
            self._add_robot(reply)
            return reply
        return None

    def undo_quick(self, reply: str) -> bool:
        """Клиент продолжил говорить поверх быстрого ответа: убираем его из истории, продолжение клиента
        приклеится к его прошлой реплике («да» + «актуален, но я за рулём»), и ответит уже модель."""
        if self.messages and self.messages[-1] == {"role": "assistant", "content": reply}:
            self.messages.pop()
            if self.transcript and self.transcript[-1] == ("robot", reply):
                self.transcript.pop()
            return True
        return False

    def undo_robot(self) -> bool:
        """Клиент договаривал свою фразу, а робот уже ответил на её начало: ответ убираем из истории,
        продолжение клиента приклеится к началу, и модель ответит на фразу целиком."""
        if self.finished or len(self.messages) < 2 or self.messages[-1]["role"] != "assistant" \
                or self.messages[-2]["role"] != "user":
            return False
        text = self.messages.pop()["content"]
        if self.transcript and self.transcript[-1] == ("robot", text):
            self.transcript.pop()
        return True

    async def reply(self, client_text: str, on_text: TextSink | None = None) -> Turn:
        """Ответ робота на реплику клиента. on_text получает куски текста по мере генерации."""
        if self.finished:
            raise RuntimeError("звонок уже завершён")
        self._add_client(client_text)
        started = time.monotonic()
        first_ms = None
        said: list[str] = []
        for attempt in range(2):
            spoken: list[str] = []

            async def sink(chunk: str) -> None:
                nonlocal first_ms
                if not chunk:
                    return
                if first_ms is None:
                    first_ms = int((time.monotonic() - started) * 1000)
                spoken.append(chunk)
                if on_text:
                    await on_text(chunk)

            try:
                reply = await self.backend.complete(self.system, self.messages, self.tools, sink)
            except asyncio.CancelledError:
                # Клиент перебил: в истории остаётся то, что робот успел начать.
                partial = "".join(spoken).strip()
                if partial:
                    self._add_robot(partial + "…")
                raise
            except BadToolJSON as e:
                log.warning("битый вход инструмента: %s", e)
                said.append("".join(spoken))
                return self._finish(CallResult("callback", "Сбой записи итога, проверить вручную", error=str(e)),
                                    " ".join(said))

            self.cost += reply.cost
            text = ("".join(spoken) or reply.text).strip()
            said.append(text)
            if reply.stop == "refusal":
                if text:
                    self.transcript.append(("robot", text))
                return self._finish(CallResult("callback", "Модель отказалась отвечать, проверить вручную", error="refusal"), text)
            if reply.tool_name is None:
                self._add_robot(text or "…")
                return Turn(text, None, first_ms)
            if text:
                self.transcript.append(("robot", text))
            if reply.stop == "max_tokens":
                return self._finish(CallResult("callback", "Итог обрезан, проверить вручную", error="max_tokens"), text)
            try:
                result = parse_result(reply.tool_name, reply.tool_input, self.op)
            except ValueError as e:
                if attempt == 0:
                    self.messages.append({"role": "assistant", "content": text or "…"})
                    self.messages.append({"role": "user", "content":
                        f"(служебно, клиент этого не слышит: итог записан неверно: {e}. "
                        "Вызови инструмент ещё раз с правильными данными, клиенту ничего не говори.)"})
                    continue
                return self._finish(CallResult("callback", "Сбой записи итога, проверить вручную", error=str(e)),
                                    " ".join(s for s in said if s))
            if result.outcome != "ndz" and not result.transfer and text.endswith("?"):
                # «…Договорились?» и тут же finish_call: трубку не кладём, ждём ответа, итог запишет следующий ход
                self.transcript.pop()
                self._add_robot(text)
                return Turn(text, None, first_ms)
            turn = self._finish(result, " ".join(s for s in said if s))
            turn.first_text_ms = first_ms
            return turn
        raise AssertionError("unreachable")

    async def conclude(self, reason: str = "клиент положил трубку") -> None:
        """Звонок оборвался до finish_call: просим модель записать итог по тому, что успели узнать."""
        # клиент ничего не сказал (только молчание): спрашивать модель не о чем, итог «оборван» без токенов
        if self.finished or not any(who == "client" and text != SILENCE for who, text in self.transcript[2:]):
            return
        note = (f"(служебно: {reason}, разговор окончен. Клиенту ничего не говори, "
                "только вызови finish_call с итогом по тому, что успели узнать.)")
        last = self.messages[-1]
        if last["role"] == "assistant":
            messages = self.messages + [{"role": "user", "content": note}]
        else:  # клиент сказал что-то и положил трубку, ответа робота не было
            messages = self.messages[:-1] + [{"role": "user", "content": last["content"] + " " + note}]

        async def ignore(_):
            pass

        try:
            reply = await self.backend.complete(self.system, messages, self.tools, ignore)
            self.cost += reply.cost
            if reply.tool_name:
                result = parse_result(reply.tool_name, reply.tool_input, self.op)
                result.comment = f"{reason.capitalize()}. {result.comment}".strip()
                self.result = result
        except Exception as e:
            log.warning("итог после обрыва не записан: %s", e)

    def duration_s(self) -> int:
        return int(time.monotonic() - self.started)

    def transcript_text(self) -> str:
        names = {"client": "Клиент", "robot": self.op.name}
        return "\n".join(f"{names[who]}: {text}" for who, text in self.transcript)
