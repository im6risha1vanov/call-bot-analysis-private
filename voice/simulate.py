"""
Прогон голосового оператора текстом против симулированных клиентов.

Запуск на сервере (ключи модели из .env бота, см. voice/llm.py):
    python -m voice.simulate                    # все персонажи по разу
    python -m voice.simulate --persona busy -n 3
    python -m voice.simulate --out voice_sim.md # разговоры в файл

Для каждого персонажа известен правильный итог, поэтому в конце видно,
сколько звонков робот закончил верно. Звонки в таблицу не пишутся.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass

from voice.dialog import Dialog
from voice.llm import make_backend
from voice.operator import load

SIM_MODEL = os.getenv("VOICE_SIM_MODEL")  # модель клиента; по умолчанию та же, что у робота
MAX_TURNS = 16
HANGUP = "[ТРУБКА]"


@dataclass
class Persona:
    key: str
    expected: str
    story: str
    scripted: list[str] | None = None  # фиксированные реплики вместо модели (автоответчик)


PERSONAS = [
    Persona("ready", "interested",
            "Ты Ирина Петровна из Краснодара, делаешь ремонт и хочешь кухню в этом месяце. Плёнка ПВХ подходит, "
            "бюджет около трёхсот тысяч. Удобно, чтобы специалист позвонил завтра после шести вечера. Отвечаешь коротко."),
    Persona("ready_fast", "interested",
            "Ты Сергей Иванович, Новороссийск. На первый же вопрос сразу вываливаешь всё: кухня нужна через месяц, "
            "фасады хочешь плёнку, бюджет тысяч триста пятьдесят. Раздражаешься, если переспрашивают уже сказанное. "
            "Специалист пусть звонит в субботу утром."),
    Persona("busy", "callback",
            "Ты за рулём, говорить неудобно. Кухня в принципе интересна. Просишь перезвонить сегодня после семи вечера."),
    Persona("later", "not_relevant",
            "Кухню думаешь менять, но не раньше чем через полгода, сейчас точно нет. Вежливый, но разговор продолжать не хочешь."),
    Persona("cheap", "not_relevant",
            "Кухня нужна в этом месяце, город Ростов-на-Дону, плёнка устраивает, но бюджет максимум сто пятьдесят тысяч, больше не готов."),
    Persona("enamel", "not_relevant",
            "Кухня нужна скоро, город Сочи. Хочешь только эмаль, плёнку считаешь дешёвкой и на неё не согласен ни в каком виде."),
    Persona("questions", "interested",
            "Ты Анна Сергеевна, Краснодар. Кухня нужна в течение месяца. Сначала спрашиваешь, откуда у них твой номер, "
            "потом не робот ли это, потом сколько стоит кухня и сколько делают. Про материал не знаешь, после объяснения плёнка подходит. "
            "Бюджет от двухсот пятидесяти устраивает. Специалист может звонить в будни после обеда."),
    Persona("angry", "do_not_call",
            "Тебя достали звонки. Грубо говоришь, что ничего не оставлял, и требуешь удалить номер и больше не звонить."),
    Persona("noisy", "interested",
            "Ты Ольга Викторовна из Анапы, говоришь из шумного места: часть слов в твоих репликах искажена, будто плохо распознаны "
            "(например «пленка пвх» как «пенка пэ вэ ха», «триста» как «тиста»). Кухня нужна в ближайший месяц, плёнка подходит, "
            "бюджет около трёхсот, специалист пусть звонит в пятницу в десять утра."),
    Persona("voicemail", "ndz", "", scripted=[
        "Здравствуйте, вы позвонили Алексею. Сейчас я не могу ответить, оставьте сообщение после звукового сигнала.",
        "(молчание)",
        "(молчание)",
    ]),
]

CLIENT_PROMPT = """Ты играешь человека, которому позвонил голосовой помощник компании, делающей кухни на заказ.
Отвечай одной-двумя короткими фразами, как люди говорят по телефону: разговорно, без вежливых формальностей,
иногда с «ну», «да», «а». Не помогай собеседнику и не подсказывай ему, отвечай только на то, что спросили.
Если разговор окончен (попрощались, ты отказался или тебя переводят на специалиста), напиши только {hangup}.

Кто ты: {story}"""


@dataclass
class Run:
    persona: Persona
    dialog: Dialog

    @property
    def ok(self) -> bool:
        return self.dialog.result is not None and self.dialog.result.outcome == self.persona.expected


async def client_line(client, persona: Persona, dialog: Dialog, turn: int) -> str:
    if persona.scripted is not None:
        return persona.scripted[turn] if turn < len(persona.scripted) else HANGUP
    # Для модели-клиента реплики робота это «user», свои реплики «assistant».
    messages = []
    for who, text in dialog.transcript[1:]:
        role = "user" if who == "robot" else "assistant"
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"] += " " + text
        else:
            messages.append({"role": role, "content": text})
    if messages[-1]["role"] == "assistant":
        messages.append({"role": "user", "content": "(молчание)"})

    async def ignore(_):
        pass

    reply = await client.complete(CLIENT_PROMPT.format(hangup=HANGUP, story=persona.story), messages, [], ignore)
    return clean_client_line(reply.text)


def clean_client_line(text: str) -> str:
    """Слабые модели дописывают за собеседника («Пользователь: …») и уходят в простыни: берём первую реплику."""
    import re
    text = re.split(r"\n\s*\n|(?:Пользователь|Ассистент|Мария|Робот|User|Assistant)\s*:", text.strip(), maxsplit=1)[0]
    text = " ".join(text.replace("Клиент:", " ").split())
    if len(text) > 220:
        cut = max(text.rfind(". ", 0, 220), text.rfind("? ", 0, 220), text.rfind("! ", 0, 220))
        text = text[:cut + 1] if cut > 40 else text[:220]
    return text.strip()


async def run_one(robot_backend, client_backend, persona: Persona, operator_id: str) -> Run:
    dialog = Dialog(load(operator_id), robot_backend)
    dialog.greet(persona.scripted[0] if persona.scripted else "Алло")
    turns = persona.scripted[1:] if persona.scripted else None
    sim = Persona(persona.key, persona.expected, persona.story, turns)
    for turn in range(MAX_TURNS):
        line = await client_line(client_backend, sim, dialog, turn)
        if HANGUP in line:
            line = line.replace(HANGUP, "").strip()
            if line:
                await dialog.reply(line)
            break
        await dialog.reply(line)
        if dialog.finished:
            break
    return Run(persona, dialog)


def report(runs: list[Run]) -> str:
    lines = []
    for run in runs:
        r = run.dialog.result
        got = f"{r.outcome}{' (перевод)' if r.transfer else ''}" if r else "нет итога"
        mark = "OK" if run.ok else "НЕ ТО"
        lines.append(f"## {run.persona.key}: ждали {run.persona.expected}, получили {got} [{mark}]\n")
        lines.append(run.dialog.transcript_text())
        if r:
            lines.append(f"\nКомментарий: {r.comment}\nДанные: {r.facts}")
        lines.append(f"Стоимость модели робота: {run.dialog.cost:.4f} {run.dialog.cost_unit}\n")
    ok = sum(run.ok for run in runs)
    lines.append(f"Итог: верно {ok} из {len(runs)}")
    return "\n".join(lines)


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--operator", default="kitchen")
    parser.add_argument("--persona", action="append", help="ключ персонажа, можно несколько раз")
    parser.add_argument("-n", type=int, default=1, help="сколько раз гонять каждого")
    parser.add_argument("--out", help="записать разговоры в файл markdown")
    args = parser.parse_args(argv)

    chosen = [p for p in PERSONAS if not args.persona or p.key in args.persona]
    if not chosen:
        print("нет таких персонажей, есть: " + ", ".join(p.key for p in PERSONAS), file=sys.stderr)
        return 2
    robot = make_backend()
    client = make_backend(model=SIM_MODEL)
    runs = await asyncio.gather(*(run_one(robot, client, p, args.operator) for p in chosen for _ in range(args.n)))
    text = report(list(runs))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)
    return 0 if all(run.ok for run in runs) else 1


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    sys.exit(asyncio.run(main()))
