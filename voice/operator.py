"""
Голосовой оператор: анкета (как в кабинете Соловья) и системный промпт из неё.

Анкета лежит в voice/operators/<id>.toml: кто звонит, приветствие, план
разговора, правила, факты и поля, которые робот собирает. Чтобы поменять
поведение робота, правят анкету, код не трогают.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

OPERATORS_DIR = Path(__file__).parent / "operators"
MAX_ITEMS = 10  # как у Соловья: план, правила и факты до 10 пунктов

OUTCOMES = {
    "interested": "подходит по всем пунктам плана и согласился на звонок специалиста",
    "callback": "просит перезвонить позже или разговор прервался до конца анкеты",
    "not_relevant": "отказался или не подходит по одному из пунктов плана",
    "ndz": "живого разговора не было: автоответчик, голосовое меню, не тот человек",
    "do_not_call": "просит больше не звонить или грубит: номер в стоп-лист",
}


class OperatorError(ValueError):
    """Анкета оператора заполнена неверно."""


@dataclass
class Operator:
    id: str
    name: str
    company: str
    greeting: str
    farewell: str
    goal: str
    lead_source: str
    plan: list[str]
    rules: list[str] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)
    fields: dict[str, str] = field(default_factory=dict)
    gender: str = "female"
    voice: str = "alena"
    voice_role: str = ""
    voice_speed: float = 1.0
    transfer_to: str = ""
    # быстрые ответы без модели: после вопроса робота (after) на ожидаемый короткий ответ (answers) — reply
    quick: list[dict] = field(default_factory=list)

    def validate(self) -> None:
        for key in ("name", "company", "greeting", "farewell", "goal"):
            if not str(getattr(self, key)).strip():
                raise OperatorError(f"{self.id}: не заполнено поле {key}")
        if not self.plan:
            raise OperatorError(f"{self.id}: пустой план разговора")
        for key in ("plan", "rules", "facts"):
            items = getattr(self, key)
            if len(items) > MAX_ITEMS:
                raise OperatorError(f"{self.id}: в блоке {key} {len(items)} пунктов, можно не больше {MAX_ITEMS}")
            if any(not str(item).strip() for item in items):
                raise OperatorError(f"{self.id}: пустой пункт в блоке {key}")
        if not 0.5 <= float(self.voice_speed) <= 2.0:
            raise OperatorError(f"{self.id}: voice_speed от 0.5 до 2.0")
        if self.gender not in ("female", "male"):
            raise OperatorError(f"{self.id}: gender должен быть female или male")
        for item in self.quick:
            if not (str(item.get("after", "")).strip() and item.get("answers") and str(item.get("reply", "")).strip()):
                raise OperatorError(f"{self.id}: в быстром ответе нужны after, answers и reply")
        for key in self.fields:
            if not key.isidentifier():
                raise OperatorError(f"{self.id}: ключ поля {key!r} должен быть латиницей без пробелов")

    def system_prompt(self) -> str:
        return compile_prompt(self)


def load(operator_id: str, directory: Path = OPERATORS_DIR) -> Operator:
    path = directory / f"{operator_id}.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise OperatorError(f"нет анкеты {path}") from None
    except tomllib.TOMLDecodeError as e:
        raise OperatorError(f"{path.name}: ошибка в TOML: {e}") from None
    known = set(Operator.__dataclass_fields__) - {"id"}
    unknown = set(data) - known
    if unknown:
        raise OperatorError(f"{path.name}: неизвестные поля {sorted(unknown)}")
    try:
        operator = Operator(id=operator_id, **data)
    except TypeError as e:
        raise OperatorError(f"{path.name}: {e}") from None
    operator.validate()
    return operator


def _numbered(items: list[str]) -> str:
    return "\n".join(f"{i}. {item}" for i, item in enumerate(items, 1))


def compile_prompt(op: Operator) -> str:
    me = "в женском роде («поняла», «записала»)" if op.gender == "female" else "в мужском роде («понял», «записал»)"
    said = "произнесла" if op.gender == "female" else "произнёс"
    outcomes = "\n".join(f"- {key}: {text}" for key, text in OUTCOMES.items())
    fields = "\n".join(f"- {key}: {text}" for key, text in op.fields.items()) or "- нет"
    parts = [
        f"Ты {op.name}, голосовой помощник ({op.company}). Ты звонишь по телефону человеку, который {op.lead_source}. "
        f"Цель звонка: {op.goal}. Говоришь о себе {me}.",
        "Как ты говоришь:\n"
        "- Это живой телефонный разговор: каждое твоё слово сразу озвучивает синтезатор. Пиши только то, что произносится вслух: "
        "без списков, скобок, кавычек-ёлочек, эмодзи, ремарок и латиницы. Числа и суммы пиши словами.\n"
        "- Одна реплика: одно-два коротких предложения, не длиннее двадцати слов, и не больше одного вопроса. Не рассказывай то, о чём не спросили. Обычная разговорная речь, на «вы».\n"
        "- Слушай, что уже сказал клиент. Если он ответил на будущий пункт плана заранее, не переспрашивай: "
        "подтверди и иди дальше. Не задавай вопросов, ответ на которые уже очевиден из сказанного.\n"
        "- Говори как живой спокойный менеджер, а не как автоответчик или ведущая: ровный деловой тон, простые слова, без канцелярита. "
        "Не радуйся и не восторгайся: никаких восклицательных знаков, «отлично», «замечательно», «прекрасно», «супер». "
        "Уместны короткие живые связки: «так», «ага, поняла», «угу, хорошо».\n"
        "- Начинай реплику с короткой спокойной реакции на ответ клиента («Понятно», «Хорошо», «Угу», «Поняла»), но не повторяй одно и то же подряд.\n"
        "- Текст клиента приходит из распознавания речи и может быть с ошибками. Если ответ непонятен или оборван, коротко переспроси.\n"
        "- На вопрос клиента отвечай по существу из фактов ниже, затем мягко вернись к плану. Если ответа в фактах нет, "
        "не выдумывай: скажи, что это точно подскажет специалист.\n"
        "- Не дави и не спорь. После отказа можно один раз мягко уточнить, после второго отказа вежливо попрощайся.\n"
        "- Если в тексте клиента только «(молчание)», спроси, слышит ли он тебя. После второго молчания заверши звонок с итогом ndz.",
        f"План разговора, по порядку:\n{_numbered(op.plan)}",
    ]
    if op.rules:
        parts.append(f"Правила:\n{_numbered(op.rules)}")
    if op.facts:
        parts.append(f"Факты, на которые можно опираться в ответах (больше ничего о компании не утверждай):\n{_numbered(op.facts)}")
    parts.append(
        "Как заканчивать звонок. Звонок заканчивается только вызовом инструмента:\n"
        "- finish_call: итог звонка и собранные данные. В том же ответе перед вызовом произнеси короткое прощание "
        "(для interested повтори, когда позвонит специалист). Для ndz прощание не нужно.\n"
        + ("- transfer_to_specialist: клиент подходит и хочет поговорить с человеком прямо сейчас. "
           "Перед вызовом скажи, что сейчас соединишь со специалистом.\n" if op.transfer_to else "")
        + f"Итоги:\n{outcomes}\n"
        f"Поля, которые нужно собрать (что клиент не сказал, оставь пустым, не придумывай):\n{fields}"
    )
    parts.append(
        f"Первую реплику ты уже {said}: «{op.greeting}». Дальше продолжай разговор с ответа клиента.\n"
        "When you use a tool, you may say a brief sentence first. If no tool can express what the user asked for, "
        "say so instead of guessing. Do not include internal or system XML tags in your response."
    )
    return "\n\n".join(parts)
