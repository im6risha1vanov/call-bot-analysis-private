"""Голосовой оператор: анкета, диалог с подменённой моделью, нарезка фраз, сессия звонка, запись итога."""
import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace as NS

import httpx

from voice import results, speech
from voice.call import CallSession
from voice.dialog import Dialog, build_tools, parse_result
from voice.operator import MAX_ITEMS, OperatorError, load


def text_block(text):
    return NS(type="text", text=text)


def tool_block(name, data, id="toolu_1"):
    return NS(type="tool_use", name=name, input=data, id=id)


class FakeStream:
    def __init__(self, chunks, message, delay=0.0):
        self.chunks, self.message, self.delay = chunks, message, delay

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @property
    def text_stream(self):
        async def gen():
            for chunk in self.chunks:
                if self.delay:
                    await asyncio.sleep(self.delay)
                yield chunk
        return gen()

    async def get_final_message(self):
        return self.message


class FakeClient:
    """Отвечает заготовками по очереди и запоминает запросы."""

    def __init__(self, replies, delay=0.0):
        self.replies = list(replies)
        self.requests = []
        self.delay = delay
        self.messages = self

    def stream(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs, default=lambda o: o.__dict__)))
        chunks, content, stop = self.replies.pop(0)
        usage = NS(input_tokens=1000, output_tokens=50, cache_creation_input_tokens=0, cache_read_input_tokens=0)
        return FakeStream(chunks, NS(content=content, stop_reason=stop, usage=usage), self.delay)


def say(*chunks):
    return (list(chunks), [text_block("".join(chunks))], "end_turn")


def finish(data, *chunks):
    blocks = ([text_block("".join(chunks))] if chunks else []) + [tool_block("finish_call", data)]
    return (list(chunks), blocks, "tool_use")


class OperatorTest(unittest.TestCase):
    def test_kitchen_loads_and_prompt_has_script(self):
        op = load("kitchen")
        prompt = op.system_prompt()
        for needle in ("Мария", "двухсот пятидесяти", "плёнк", "finish_call", "transfer_to_specialist", "callback_time", "в женском роде"):
            self.assertIn(needle, prompt)
        self.assertIn(op.greeting, prompt)

    def test_limits_and_unknown_fields(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path("voice/operators/kitchen.toml").read_text(encoding="utf-8")
            Path(d, "long.toml").write_text(base.replace("rules = [", "rules = [" + '"x",' * MAX_ITEMS), encoding="utf-8")
            with self.assertRaisesRegex(OperatorError, "не больше"):
                load("long", Path(d))
            Path(d, "typo.toml").write_text('greting = "x"\n' + base, encoding="utf-8")
            with self.assertRaisesRegex(OperatorError, "неизвестные поля"):
                load("typo", Path(d))
            with self.assertRaisesRegex(OperatorError, "нет анкеты"):
                load("missing", Path(d))

    def test_tools_follow_fields(self):
        op = load("kitchen")
        finish_tool = build_tools(op)[0]
        self.assertTrue(finish_tool["eager_input_streaming"])
        self.assertEqual(set(finish_tool["input_schema"]["properties"]["facts"]["properties"]), set(op.fields))

    def test_parse_result_validates(self):
        op = load("kitchen")
        r = parse_result("finish_call", {"outcome": "interested", "comment": "ok", "facts": {"city": " Казань ", "zzz": "x", "name": ""}}, op)
        self.assertEqual(r.facts, {"city": "Казань"})
        for bad in ({"outcome": "maybe", "comment": "x"}, {"outcome": "ndz"}, {"outcome": "ndz", "comment": "x", "facts": {"city": 5}}, "str"):
            with self.assertRaises(ValueError):
                parse_result("finish_call", bad, op)
        self.assertTrue(parse_result("transfer_to_specialist", {"comment": "хочет сейчас"}, op).transfer)


class DialogTest(unittest.IsolatedAsyncioTestCase):
    async def test_conversation_to_interested(self):
        client = FakeClient([
            say("Отлично! ", "В каком городе будет стоять кухня?"),
            finish({"outcome": "interested", "comment": "Казань, плёнка", "facts": {"city": "Казань"}},
                   "Записала, специалист позвонит завтра после шести. ", "Хорошего дня!"),
        ])
        dialog = Dialog(load("kitchen"), client, model="test-model")
        self.assertIn("Мария", dialog.greet())
        heard = []

        async def sink(chunk):
            heard.append(chunk)

        turn = await dialog.reply("Да, актуально, в этом месяце", sink)
        self.assertIsNone(turn.result)
        self.assertEqual("".join(heard), "Отлично! В каком городе будет стоять кухня?")
        turn = await dialog.reply("Казань")
        self.assertEqual(turn.result.outcome, "interested")
        self.assertIn("Хорошего дня", turn.text)
        self.assertTrue(dialog.finished)

        request = client.requests[0]
        self.assertEqual(request["model"], "test-model")
        self.assertEqual(request["thinking"], {"type": "disabled"})
        self.assertEqual(request["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual([m["role"] for m in request["messages"]], ["user", "assistant", "user"])
        self.assertGreater(dialog.cost_usd, 0)
        with self.assertRaises(RuntimeError):
            await dialog.reply("ещё")

    async def test_silent_finish_uses_farewell_but_not_for_ndz(self):
        op = load("kitchen")
        dialog = Dialog(op, FakeClient([finish({"outcome": "not_relevant", "comment": "Шаг 1: через полгода"})]))
        dialog.greet()
        turn = await dialog.reply("Нет, не актуально")
        self.assertEqual((turn.text, turn.fallback), (op.farewell, True))

        dialog = Dialog(op, FakeClient([finish({"outcome": "ndz", "comment": "Автоответчик"})]))
        dialog.greet("Оставьте сообщение после сигнала")
        turn = await dialog.reply("(молчание)")
        self.assertEqual((turn.text, turn.fallback, turn.result.outcome), ("", False, "ndz"))

    async def test_bad_tool_input_is_retried_once(self):
        client = FakeClient([
            finish({"outcome": "maybe", "comment": "x"}),
            finish({"outcome": "callback", "comment": "Вечером"}, "Хорошо, наберу вечером."),
        ])
        dialog = Dialog(load("kitchen"), client)
        dialog.greet()
        turn = await dialog.reply("Я за рулём")
        self.assertEqual(turn.result.outcome, "callback")
        retry = client.requests[1]["messages"][-1]["content"][0]
        self.assertEqual((retry["type"], retry["is_error"]), ("tool_result", True))

    async def test_barge_in_keeps_history_valid(self):
        client = FakeClient([say("Отлично, ", "а в каком ", "городе?"), say("Поняла, Сочи.")], delay=0.05)
        dialog = Dialog(load("kitchen"), client)
        dialog.greet()
        task = asyncio.create_task(dialog.reply("Да"))
        await asyncio.sleep(0.08)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await dialog.reply("Сочи")
        roles = [m["role"] for m in client.requests[1]["messages"]]
        self.assertEqual(roles, ["user", "assistant", "user", "assistant", "user"])
        self.assertTrue(client.requests[1]["messages"][3]["content"].endswith("…"))


class SpeechTest(unittest.TestCase):
    def test_phrase_splitter(self):
        s = speech.PhraseSplitter()
        out = []
        for chunk in ["Отлично", "! А в каком", " городе", " будет кухня? Спа", "сибо."]:
            out += s.feed(chunk)
        self.assertEqual(out, ["Отлично!", "А в каком городе будет кухня?"])
        self.assertEqual(s.flush(), ["Спасибо."])
        long = "Слово " * 30 + "запятая, и ещё немного текста"
        self.assertTrue(speech.PhraseSplitter().feed(long)[0].endswith(","))

    def test_deepgram_collector(self):
        c = speech.UtteranceCollector()
        res = lambda t, final=False, sf=False: {"type": "Results", "is_final": final, "speech_final": sf, "channel": {"alternatives": [{"transcript": t}]}}
        self.assertEqual(c.handle(res("да")), speech.Heard("да", False))
        self.assertEqual(c.handle(res("да актуально", final=True)), speech.Heard("да актуально", False))
        self.assertEqual(c.handle(res("в этом месяце", final=True, sf=True)), speech.Heard("да актуально в этом месяце", True))
        self.assertIsNone(c.handle({"type": "UtteranceEnd"}))
        c.handle(res("Казань", final=True))
        self.assertEqual(c.handle({"type": "UtteranceEnd"}), speech.Heard("Казань", True))
        self.assertIn("encoding=linear16", speech.deepgram_url())


class CallSessionTest(unittest.IsolatedAsyncioTestCase):
    async def test_call_flow_sends_audio_and_result(self):
        client = FakeClient([
            say("Отлично! ", "В каком городе будет кухня?"),
            finish({"outcome": "not_relevant", "comment": "Шаг 5: бюджет 100 тыс."}, "Поняла, спасибо, всего доброго!"),
        ])
        audio, events, saved = [], [], []

        async def send_audio(pcm):
            audio.append(pcm)

        async def send_event(e):
            events.append(e)

        async def synth(text, voice):
            return b"\x00\x00" * 160  # 10 мс

        async def save(dialog, **kw):
            saved.append(kw)
            return results.record(dialog, **kw)

        session = CallSession(Dialog(load("kitchen"), client), send_audio, send_event, synthesize=synth, save=save)
        await session.start()
        await session.heard("да", False)
        await session.heard("Да, актуально", True)
        await session.reply_task
        await session.heard("Бюджет тысяч сто", True)
        await session.reply_task
        await asyncio.wait_for(session.done.wait(), 2)
        result = [e for e in events if e["type"] == "result"][0]
        self.assertEqual(result["outcome"], "not_relevant")
        self.assertEqual(len(audio), 4)  # приветствие + 2 фразы + прощание
        self.assertEqual(saved, [{"phone": "", "mode": "браузер"}])
        self.assertIn({"type": "interim", "text": "да"}, events)

    async def test_hangup_without_result_records_callback(self):
        events = []

        async def nothing(_):
            pass

        async def send_event(e):
            events.append(e)

        async def synth(text, voice):
            return b""

        async def save(dialog, **kw):
            return results.record(dialog, **kw)

        session = CallSession(Dialog(load("kitchen"), FakeClient([])), nothing, send_event, synthesize=synth, save=save)
        await session.start()
        await session.hangup()
        self.assertEqual(events[-1]["outcome"], "callback")


class ResultsTest(unittest.IsolatedAsyncioTestCase):
    async def test_jsonl_and_sheet_append(self):
        client = FakeClient([finish({"outcome": "interested", "comment": "Казань", "facts": {"city": "Казань"}}, "Спасибо!")])
        dialog = Dialog(load("kitchen"), client)
        dialog.greet()
        await dialog.reply("Да, Казань")
        rec = results.record(dialog, phone="79990001122", now=datetime(2026, 10, 14, 9, 30, tzinfo=results.MSK))
        fields = list(dialog.op.fields)
        row = results.sheet_row(rec, fields, {"client": "Клиент", "robot": "Мария"})
        header = results.sheet_header(fields)
        self.assertEqual(len(row), len(header))
        self.assertEqual(row[:6], ["14.10.2026", "09:30", "kitchen", "79990001122", "браузер", "Заинтересованный"])
        self.assertIn("Мария: Спасибо!", row[-1])

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "calls.jsonl")
            results.save_jsonl(rec, path)
            self.assertEqual(json.loads(Path(path).read_text(encoding="utf-8"))["outcome"], "interested")

        calls = []

        def handler(request):
            calls.append((request.method, request.url.path, request.content))
            if request.method == "GET":
                return httpx.Response(200, json={"sheets": [{"properties": {"title": "Сводка"}}]})
            return httpx.Response(200, json={})

        os.environ["VOICE_SHEET_ID"] = "sheet123"
        try:
            await results.append_sheet(row, header, http=httpx.AsyncClient(transport=httpx.MockTransport(handler)), access_token="t")
        finally:
            del os.environ["VOICE_SHEET_ID"]
        kinds = [(m, p.split("/")[-1]) for m, p, _ in calls]
        self.assertEqual(kinds[1], ("POST", "sheet123:batchUpdate"))  # создали лист
        self.assertTrue(kinds[-1][1].endswith(":append"))
        self.assertIn("Казань", calls[-1][2].decode())


if __name__ == "__main__":
    unittest.main()
