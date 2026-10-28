"""
Автотест робота без звонков людям: виртуальный абонент разговаривает с телефонным
кодом робота так же, как это делает Asterisk.

Абонент подключается к роботу по AudioSocket (кадры по 20 мс, 8 кГц), говорит
синтезированным голосом, робот слышит его через Deepgram, думает моделью и отвечает
голосом. Работает всё, кроме Mango: распознавание, паузы, перебивания, модель,
синтез, запись итога. Реплики абонента придумывает модель по персонажу из
voice/simulate.py, поэтому у каждого разговора есть правильный итог.

По желанию плюс текстовый прогон персонажей (без звука, проверяет только логику).

    python -m voice.autotest                       # голосом ready, busy, questions
    python -m voice.autotest --text all            # плюс все персонажи текстом
    python -m voice.autotest --audio all --text none
    python -m voice.autotest --sheet               # строки на лист «Робот · Автотест»

На сервере его запускает таймер после каждого нового коммита ветки
(scripts/voice_test.sh autotest), результат ложится на лист «Робот · Автотест»
таблицы VOICE_AUTOTEST_SHEET_ID (по умолчанию GOOGLE_SHEET_ID).
"""

from __future__ import annotations

import argparse
import array
import asyncio
import logging
import os
import subprocess
import sys
import time
import uuid as uuidlib
from dataclasses import dataclass, field
from datetime import datetime

from voice import results, simulate, sip, speech
from voice.dialog import SILENCE, Dialog
from voice import presets
from voice.llm import make_backend

log = logging.getLogger("voice.autotest")

CLIENT_VOICE = os.getenv("VOICE_AUTOTEST_CLIENT_VOICE", "filipp")
DEFAULT_AUDIO = ["ready", "busy", "questions", "voicemail"]
VOICE_RMS = 400            # громче этого кадр робота считается речью
ROBOT_DONE_S = 0.9         # абонент отвечает, когда робот молчит столько
WAIT_ROBOT_S = 12.0        # столько ждём, что робот заговорит после реплики абонента
SLOW_MS = 1500             # реакция медленнее этого попадает в проблемы
SHEET_TITLE = "Робот · Автотест"
HEADER = ["Время", "Коммит", "Вид", "Персонаж", "Ждали", "Получили", "Верно", "Реакция, мс (сред/макс)",
          "Проблемы", "Стоимость", "Разговор"]


def rms(pcm: bytes) -> float:
    a = array.array("h", pcm[: len(pcm) // 2 * 2])
    return (sum(x * x for x in a) / len(a)) ** 0.5 if a else 0.0


@dataclass
class AudioRun:
    persona: simulate.Persona
    dialog: Dialog | None = None
    record: dict | None = None
    said: list[str] = field(default_factory=list)       # что абонент сказал на самом деле
    reactions: list[int] = field(default_factory=list)  # мс от конца реплики абонента до голоса робота
    model_ms: list[int | None] = field(default_factory=list)  # мс до первых слов модели по репликам
    problems: list[str] = field(default_factory=list)
    preset: str = ""  # набор робота (voice/presets.py), если прогон сравнивает наборы

    @property
    def outcome(self) -> str:
        return self.record["outcome"] if self.record else "нет итога"

    @property
    def ok(self) -> bool:
        return self.outcome == self.persona.expected and not self.problems


class VirtualClient:
    """Абонент на другом конце AudioSocket: говорит кадрами в реальном времени и слушает робота."""

    def __init__(self, persona: simulate.Persona, robot_backend, client_backend, *,
                 make_asr=sip.PhoneASR, synthesize=speech.synthesize_pcm) -> None:
        self.run = AudioRun(persona)
        self.robot_backend, self.client_backend = robot_backend, client_backend
        self.make_asr, self.synthesize = make_asr, synthesize
        self.out = bytearray()            # голос абонента, ещё не отправленный
        self.speaking = False             # абонент говорит (очередь ещё не пуста)
        self.spoke_at = 0.0               # когда абонент договорил последнюю реплику
        self.robot_voice_at = 0.0         # последний кадр с голосом робота
        self.robot_hung = asyncio.Event()
        self.reply_heard = asyncio.Event()  # робот заговорил после реплики абонента
        self.waiting_reply = False
        self.measure = False              # считать ли реакцию (после молчания абонента не считаем)
        self.server_done = asyncio.Event()
        self.writer: asyncio.StreamWriter | None = None

    # ---------------------------------------------------------- сторона робота

    def _make_dialog(self, op):
        self.run.dialog = Dialog(op, self.robot_backend)
        return self.run.dialog

    async def _save(self, dialog, *, phone="", mode="", extra=None):
        self.run.record = results.record(dialog, phone=phone, mode="автотест")
        return self.run.record

    async def _on_socket(self, reader, writer) -> None:
        try:
            call = sip.PhoneCall(reader, writer, make_dialog=self._make_dialog, make_asr=self.make_asr,
                                 synthesize=self.synthesize, save=self._save)
            call.recording = False  # автотест не пишем в записи разговоров
            original = call.send_event

            async def send_event(event: dict) -> None:  # сколько думала модель на каждой реплике
                if event.get("type") == "transcript" and event.get("role") == "robot" and "first_text_ms" in event:
                    self.run.model_ms.append(event["first_text_ms"])
                await original(event)

            call.send_event = send_event
            await call.run()
        except Exception as e:
            log.exception("сбой робота")
            self.run.problems.append(f"сбой робота: {e}")
        finally:
            self.server_done.set()

    # ---------------------------------------------------------- линия

    async def _sender(self) -> None:
        start, n = time.monotonic(), 0
        while not self.robot_hung.is_set():
            if self.out:
                chunk = bytes(self.out[:sip.FRAME_BYTES]).ljust(sip.FRAME_BYTES, b"\0")
                del self.out[:sip.FRAME_BYTES]
                if not self.out and self.speaking:
                    self.speaking, self.spoke_at = False, time.monotonic()
            else:
                chunk = bytes(sip.FRAME_BYTES)
            try:
                self.writer.write(sip.frame(sip.KIND_AUDIO, chunk))
                await self.writer.drain()
            except (ConnectionError, RuntimeError):
                return
            n += 1
            await asyncio.sleep(max(start + n * 0.02 - time.monotonic(), 0))

    async def _receiver(self, reader) -> None:
        cut_in_noted = False
        while True:
            try:
                kind, payload = await sip.read_frame(reader)
            except (asyncio.IncompleteReadError, ConnectionError):
                break
            if kind == sip.KIND_HANGUP:
                break
            if kind != sip.KIND_AUDIO or rms(payload) < VOICE_RMS:
                continue
            now = time.monotonic()
            self.robot_voice_at = now
            if self.speaking and not cut_in_noted and self.run.said:
                cut_in_noted = True
                self.run.problems.append(f"робот заговорил, пока абонент говорил: «{self.run.said[-1]}»")
            elif not self.speaking:
                cut_in_noted = False
            if self.waiting_reply and not self.speaking:
                self.waiting_reply = False
                if self.measure:
                    self.run.reactions.append(int((now - self.spoke_at) * 1000))
                self.reply_heard.set()
        self.robot_hung.set()

    # ---------------------------------------------------------- абонент

    async def say(self, text: str) -> None:
        pcm = await self.synthesize(text, speech.Voice(CLIENT_VOICE, "", 1.0))
        self.run.said.append(text)
        self.reply_heard.clear()
        self.speaking, self.waiting_reply, self.measure = True, True, True
        self.out += sip.downsample(pcm, speech.TTS_RATE, sip.RATE)

    async def wait_robot(self) -> bool:
        """Ждём, пока робот ответит и замолчит. False: робот не ответил или повесил трубку."""
        try:
            await asyncio.wait_for(self._first_reply(), WAIT_ROBOT_S + len(self.out) / (sip.RATE * 2))
        except asyncio.TimeoutError:
            return False
        while not self.robot_hung.is_set() and time.monotonic() - self.robot_voice_at < ROBOT_DONE_S:
            await asyncio.sleep(0.05)
        return not self.robot_hung.is_set()

    async def _first_reply(self) -> None:
        hung = asyncio.create_task(self.robot_hung.wait())
        heard = asyncio.create_task(self.reply_heard.wait())
        await asyncio.wait({hung, heard}, return_when=asyncio.FIRST_COMPLETED)
        hung.cancel(), heard.cancel()

    async def talk(self) -> AudioRun:
        server = await asyncio.start_server(self._on_socket, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        reader, self.writer = await asyncio.open_connection("127.0.0.1", port)
        self.writer.write(sip.frame(sip.KIND_UUID, uuidlib.uuid4().bytes))
        tasks = [asyncio.create_task(self._sender()), asyncio.create_task(self._receiver(reader))]
        persona = self.run.persona
        scripted = list(persona.scripted or [])
        try:
            await asyncio.sleep(0.5)  # гудки кончились, абонент подносит трубку
            await self.say(scripted.pop(0) if scripted else "Алло")
            for turn in range(simulate.MAX_TURNS):
                if not await self.wait_robot():
                    if not self.robot_hung.is_set():
                        self.run.problems.append(f"робот молчит после «{self.run.said[-1]}»")
                    break
                if self.run.dialog is None:
                    break
                if persona.scripted is not None:
                    line = scripted.pop(0) if scripted else simulate.HANGUP
                else:
                    line = await simulate.client_line(self.client_backend, persona, self.run.dialog, turn)
                hang = simulate.HANGUP in line
                line = line.replace(simulate.HANGUP, "").strip()
                if line and line != SILENCE:
                    await self.say(line)
                elif not hang:  # абонент молчит: робот сам должен переспросить
                    self.run.said.append(SILENCE)
                    self.reply_heard.clear()
                    self.measure, self.waiting_reply = False, True
                if hang:
                    while self.out:
                        await asyncio.sleep(0.05)
                    await asyncio.sleep(0.3)
                    break
            if not self.robot_hung.is_set():
                try:
                    self.writer.write(sip.frame(sip.KIND_HANGUP))
                    await self.writer.drain()
                except (ConnectionError, RuntimeError):
                    pass
            await asyncio.wait_for(self.server_done.wait(), 30)
        except asyncio.TimeoutError:
            self.run.problems.append("робот не закончил звонок после отбоя")
        except Exception as e:  # сбой на стороне абонента (синтез, модель клиента): этот звонок, не весь прогон
            log.exception("сбой виртуального абонента")
            self.run.problems.append(f"сбой абонента: {e!r}"[:300])
        finally:
            self.robot_hung.set()
            for task in tasks:
                task.cancel()
            self.writer.close()
            server.close()
        self._check()
        return self.run

    def _check(self) -> None:
        run = self.run
        slow = [ms for ms in run.reactions if ms > SLOW_MS]
        if slow:
            run.problems.append(f"медленная реакция: {', '.join(map(str, slow))} мс")
        if run.dialog is not None:
            for who, text in run.dialog.transcript:
                if who == "robot" and any(mark in text for mark in ("finish_call", "transfer_to", "{\"", "outcome")):
                    run.problems.append(f"робот произнёс служебный текст: «{text[:80]}»")
        if run.record is None:
            run.problems.append("итог не записан")
        elif run.record.get("error"):
            run.problems.append(f"итог с ошибкой: {run.record['error']}")


# ------------------------------------------------------------------ отчёт


def commit() -> str:
    sha = os.getenv("VOICE_AUTOTEST_SHA", "").strip()
    if sha:
        return sha[:7]
    try:
        return subprocess.run(["git", "log", "-1", "--format=%h"], capture_output=True, text=True, timeout=5, check=False).stdout.strip()
    except Exception:
        return ""


def audio_text(run: AudioRun) -> str:
    lines = [f"Абонент говорил: {' | '.join(run.said)}"]
    if run.reactions:
        lines.append(f"Реакция по репликам, мс: {run.reactions}; из них модель до первых слов: {run.model_ms}")
    if run.dialog is not None:
        lines.append(run.dialog.transcript_text())
    if run.record:
        lines.append(f"Итог: {run.record['outcome']}. {run.record['comment']}. Данные: {run.record['facts']}")
    return "\n".join(lines)


def rows(audio: list[AudioRun], text: list[simulate.Run], at: str, sha: str) -> list[list]:
    out = []
    for run in audio:
        cost = f"{run.dialog.cost:.0f} {run.dialog.cost_unit}" if run.dialog else ""
        reaction = f"{sum(run.reactions) // len(run.reactions)}/{max(run.reactions)}" if run.reactions else ""
        kind = f"голос · {presets.PRESETS[run.preset]['name']}" if run.preset else "голос"
        out.append([at, sha, kind, run.persona.key, run.persona.expected, run.outcome, "да" if run.ok else "НЕТ",
                    reaction, "; ".join(run.problems), cost, audio_text(run)[:45000]])
    for run in text:
        r = run.dialog.result
        out.append([at, sha, "текст", run.persona.key, run.persona.expected, r.outcome if r else "нет итога",
                    "да" if run.ok else "НЕТ", "", "", f"{run.dialog.cost:.0f} {run.dialog.cost_unit}",
                    run.dialog.transcript_text()[:45000]])
    return out


def markdown(audio: list[AudioRun], text: list[simulate.Run], sha: str) -> str:
    lines = [f"# Автотест {sha}\n"]
    for run in audio:
        lines.append(f"## голос{' ' + run.preset if run.preset else ''} {run.persona.key}: ждали {run.persona.expected}, получили {run.outcome} "
                     f"[{'OK' if run.ok else 'НЕ ТО'}]")
        lines.append(f"Реакция, мс: {run.reactions}")
        if run.problems:
            lines.append("Проблемы: " + "; ".join(run.problems))
        lines.append(audio_text(run) + "\n")
    if text:
        lines.append(simulate.report(text))
    ok = sum(r.ok for r in audio) + sum(r.ok for r in text)
    lines.append(f"\nВсего верно {ok} из {len(audio) + len(text)}")
    return "\n".join(lines)


# ------------------------------------------------------------------ запуск


def pick(spec: str, default: list[str]) -> list[simulate.Persona]:
    if spec == "none":
        return []
    keys = default if spec == "default" else [p.key for p in simulate.PERSONAS] if spec == "all" else spec.split(",")
    unknown = set(keys) - {p.key for p in simulate.PERSONAS}
    if unknown:
        raise SystemExit("нет персонажей " + ", ".join(sorted(unknown)) + ", есть: "
                         + ", ".join(p.key for p in simulate.PERSONAS))
    return [p for p in simulate.PERSONAS if p.key in keys]


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--audio", default="default", help="персонажи голосом: default, all, none или ключи через запятую")
    parser.add_argument("--text", default="none", help="персонажи текстом: all, none или ключи через запятую")
    parser.add_argument("--operator", default="kitchen")
    parser.add_argument("--out", help="отчёт markdown в файл")
    parser.add_argument("--sheet", action="store_true", help="строки на лист «Робот · Автотест»")
    parser.add_argument("--presets", default="", help="сравнить наборы робота: ключи из voice/presets.py через запятую "
                                                      "(голосовые персонажи прогоняются для каждого набора по очереди)")
    args = parser.parse_args(argv)

    # по умолчанию робот говорит голосом из анкеты (Яндекс), чтобы не тратить символы ElevenLabs
    os.environ["VOICE_VOICE"] = os.getenv("VOICE_AUTOTEST_ROBOT_VOICE", "")
    audio_personas = pick(args.audio, DEFAULT_AUDIO)
    text_personas = pick(args.text, [])
    # клиента играет та же модель, что и робота: yandexgpt-lite дописывала реплики за обоих и сыпала простынями
    client_model = simulate.SIM_MODEL or os.getenv("VOICE_AUTOTEST_CLIENT_MODEL") or None
    sha = commit()
    spreadsheet = (os.getenv("VOICE_AUTOTEST_SHEET_ID") or os.getenv("GOOGLE_SHEET_ID") or "").strip() if args.sheet else ""
    compare = [k for k in args.presets.split(",") if k in presets.PRESETS]
    try:
        robot = make_backend(model=presets.llm_model())
        client = make_backend(model=client_model)
        audio_runs, text_runs = await asyncio.gather(
            asyncio.gather(*(VirtualClient(p, robot, client).talk() for p in ([] if compare else audio_personas))),
            asyncio.gather(*(simulate.run_one(robot, client, p, args.operator) for p in text_personas)))
        audio_runs = list(audio_runs)
        for key in compare:  # по очереди: у Яндекса без подписки всего две одновременные генерации
            runs = await asyncio.gather(*(VirtualClient(p, make_backend("yandex", presets.PRESETS[key]["llm"]), client).talk()
                                          for p in audio_personas))
            for run in runs:
                run.preset = key
            audio_runs += runs
    except Exception as e:  # сам автотест упал: пишем ошибку в таблицу, иначе её никто не увидит
        log.exception("автотест упал")
        if spreadsheet:
            at = datetime.now(results.MSK).strftime("%d.%m.%Y %H:%M")
            await results.append_rows(spreadsheet, SHEET_TITLE, HEADER,
                                      [[at, sha, "сбой", "", "", "", "НЕТ", "", f"автотест упал: {e!r}"[:1000], "", ""]])
        return 2
    audio_runs, text_runs = list(audio_runs), list(text_runs)

    report = markdown(audio_runs, text_runs, sha)
    print(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report + "\n")
    if args.sheet:
        if not spreadsheet:
            print("нет VOICE_AUTOTEST_SHEET_ID и GOOGLE_SHEET_ID: в таблицу не пишу", file=sys.stderr)
        else:
            at = datetime.now(results.MSK).strftime("%d.%m.%Y %H:%M")
            await results.append_rows(spreadsheet, SHEET_TITLE, HEADER, rows(audio_runs, text_runs, at, sha))
            print(f"записано на лист «{SHEET_TITLE}»")
    return 0 if all(r.ok for r in audio_runs) and all(r.ok for r in text_runs) else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    for noisy in ("httpx", "aiohttp.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    sys.exit(asyncio.run(main()))
