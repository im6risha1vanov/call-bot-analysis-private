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
from voice.llm import AnthropicBackend, CvcBackend, OpenRouterBackend, YandexBackend
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
        dialog = Dialog(load("kitchen"), AnthropicBackend("test-model", client=client))
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
        self.assertGreater(dialog.cost, 0)
        self.assertTrue(all(t["eager_input_streaming"] for t in request["tools"]))
        with self.assertRaises(RuntimeError):
            await dialog.reply("ещё")

    async def test_silent_finish_uses_farewell_but_not_for_ndz(self):
        op = load("kitchen")
        dialog = Dialog(op, AnthropicBackend(client=FakeClient([finish({"outcome": "not_relevant", "comment": "Шаг 1: через полгода"})])))
        dialog.greet()
        turn = await dialog.reply("Нет, не актуально")
        self.assertEqual((turn.text, turn.fallback), (op.farewell, True))

        dialog = Dialog(op, AnthropicBackend(client=FakeClient([finish({"outcome": "ndz", "comment": "Автоответчик"})])))
        dialog.greet("Оставьте сообщение после сигнала")
        turn = await dialog.reply("(молчание)")
        self.assertEqual((turn.text, turn.fallback, turn.result.outcome), ("", False, "ndz"))

    async def test_bad_tool_input_is_retried_once(self):
        client = FakeClient([
            finish({"outcome": "maybe", "comment": "x"}),
            finish({"outcome": "callback", "comment": "Вечером"}, "Хорошо, наберу вечером."),
        ])
        dialog = Dialog(load("kitchen"), AnthropicBackend(client=client))
        dialog.greet()
        turn = await dialog.reply("Я за рулём")
        self.assertEqual(turn.result.outcome, "callback")
        retry = client.requests[1]["messages"][-1]
        self.assertEqual(retry["role"], "user")
        self.assertIn("неверно", retry["content"])

    async def test_barge_in_keeps_history_valid(self):
        client = FakeClient([say("Отлично, ", "а в каком ", "городе?"), say("Поняла, Сочи.")], delay=0.05)
        dialog = Dialog(load("kitchen"), AnthropicBackend(client=client))
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


def sse(*events):
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events)


class OpenRouterBackendTest(unittest.IsolatedAsyncioTestCase):
    async def test_streams_text_then_tool_call_in_pieces(self):
        sent = []

        def chunk(**delta):
            return {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]}

        def handler(request):
            sent.append(json.loads(request.content))
            events = [chunk(content="Записала, "), chunk(content="до свидания!"),
                      chunk(tool_calls=[{"index": 0, "id": "c1", "type": "function",
                                         "function": {"name": "finish_call", "arguments": '{"outcome": "inter'}}]),
                      chunk(tool_calls=[{"index": 0, "function": {"arguments": 'ested", "comment": "Сочи", "facts": {"city": "Сочи"}}'}}]),
                      {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
                      {"choices": [], "usage": {"prompt_tokens": 900, "completion_tokens": 30, "cost": 0.00012}}]
            text = ": OPENROUTER PROCESSING\n\n" + "".join(f"data: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events) + "data: [DONE]\n\n"
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=text)

        backend = OpenRouterBackend("anthropic/test", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)), api_key="k")
        dialog = Dialog(load("kitchen"), backend)
        dialog.greet()
        chunks = []

        async def sink(c):
            chunks.append(c)

        turn = await dialog.reply("Да, Сочи", sink)
        self.assertEqual(chunks, ["Записала, ", "до свидания!"])
        self.assertEqual((turn.result.outcome, turn.result.facts), ("interested", {"city": "Сочи"}))
        body = sent[0]
        self.assertEqual(body["model"], "anthropic/test")
        self.assertEqual(body["messages"][0]["role"], "system")
        self.assertIn("Мария", body["messages"][0]["content"][0]["text"])
        self.assertEqual(body["tools"][0]["function"]["name"], "finish_call")
        self.assertEqual((dialog.cost, dialog.cost_unit), (0.00012, "$"))

    async def test_error_status(self):
        backend = OpenRouterBackend("m", http=httpx.AsyncClient(transport=httpx.MockTransport(
            lambda r: httpx.Response(402, json={"error": {"message": "Insufficient credits"}}))), api_key="k")
        with self.assertRaisesRegex(RuntimeError, "402"):
            await backend.complete("s", [{"role": "user", "content": "Алло"}], [], lambda c: None)


class YandexBackendTest(unittest.IsolatedAsyncioTestCase):
    async def test_model_uri_folder_header_and_text(self):
        sent = []

        def handler(request):
            sent.append(request)
            events = [{"choices": [{"index": 0, "delta": {"content": "В каком городе?"}, "finish_reason": None}]},
                      {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": {"total_tokens": 1200}}]
            text = "".join(f"data: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events) + "data: [DONE]\n\n"
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=text)

        backend = YandexBackend("yandexgpt-lite", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                                api_key="ak", folder="b1gtest")
        dialog = Dialog(load("kitchen"), backend)
        dialog.greet()
        turn = await dialog.reply("Да")
        self.assertEqual((turn.text, dialog.cost, dialog.cost_unit), ("В каком городе?", 1200.0, "ток."))
        body = json.loads(sent[0].content)
        self.assertEqual(body["model"], "gpt://b1gtest/yandexgpt-lite/latest")
        self.assertIsInstance(body["messages"][0]["content"], str)
        self.assertNotIn("reasoning", body)
        self.assertEqual((sent[0].headers["authorization"], sent[0].headers["openai-project"]), ("Bearer ak", "b1gtest"))


class ToolTextGateTest(unittest.IsolatedAsyncioTestCase):
    async def test_tool_call_written_as_text_is_not_spoken(self):
        pieces = ["Поняла, спасибо", ", всего доброго!\nfin", "ish_call\n{\"comment\": \"Не планирует\", ",
                  "\"outcome\": \"not_relevant\"}"]

        def handler(request):
            events = [{"choices": [{"index": 0, "delta": {"content": p}, "finish_reason": None}]} for p in pieces]
            events.append({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
            text = "".join(f"data: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events) + "data: [DONE]\n\n"
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=text)

        backend = YandexBackend("yandexgpt", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                                api_key="ak", folder="b1g")
        dialog = Dialog(load("kitchen"), backend)
        dialog.greet()
        heard = []

        async def sink(c):
            heard.append(c)

        turn = await dialog.reply("Нет, не планирую", sink)
        self.assertNotIn("finish", "".join(heard))
        self.assertEqual("".join(heard).strip(), "Поняла, спасибо, всего доброго!")
        self.assertEqual((turn.result.outcome, turn.result.comment), ("not_relevant", "Не планирует"))

    def test_plain_text_passes_and_latin_tail_is_held(self):
        from voice.llm import ToolTextGate
        gate = ToolTextGate(["finish_call", "transfer_to_specialist"])
        self.assertEqual(gate.feed("Хорошо, записала. "), "Хорошо, записала. ")
        self.assertEqual(gate.feed("tra"), "")          # может оказаться началом transfer_to_specialist
        self.assertEqual(gate.feed("ктор"), "traктор")  # не оказалось
        self.assertIsNone(gate.call())


    def test_tool_call_marker_and_invented_dialog_are_not_spoken(self):
        from voice.llm import ToolTextGate
        gate = ToolTextGate(["finish_call"])
        spoken = gate.feed("Спасибо, до свидания!\n\n[TOOL_") + gate.feed("CALL_START]finish_call {\"outcome\": \"interested\", \"comment\": \"ок\"}")
        self.assertEqual((spoken + gate.flush()).strip(), "Спасибо, до свидания!")
        self.assertEqual(gate.call(), ("finish_call", {"outcome": "interested", "comment": "ок"}))

        gate = ToolTextGate(["finish_call"])
        spoken = gate.feed("Спасибо за уточнение!\n\n") + gate.feed("[\n    \"finish_call\", {\"outcome\": \"callback\"}]")
        self.assertEqual((spoken + gate.flush()).strip(), "Спасибо за уточнение!")
        self.assertEqual(gate.call(), ("finish_call", {"outcome": "callback"}))
        gate = ToolTextGate(["finish_call"])
        gate.feed("Пока!\n[\"finish_call\", {\"outcome\": \"callback\"},\n    {\"x\": 1}]")
        self.assertEqual(gate.call(), ("finish_call", {"outcome": "callback"}))

        gate = ToolTextGate(["finish_call"])
        spoken = gate.feed("Какой материал вас интересует?\n\n Пользователь: Плёнка\n Ассистент: Отлично")
        self.assertEqual((spoken + gate.flush()).strip(), "Какой материал вас интересует?")
        self.assertIsNone(gate.call())


class CvcBackendTest(unittest.IsolatedAsyncioTestCase):
    def backend(self, handler):
        return CvcBackend("gpt-test", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                          base_url="https://proxy/v1", api_key="k")

    async def test_streams_text_then_tool_call(self):
        sent = []

        def handler(request):
            sent.append(json.loads(request.content))
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=sse(
                {"type": "response.output_text.delta", "delta": "Записала, "},
                {"type": "response.output_text.delta", "delta": "до свидания!"},
                {"type": "response.completed", "response": {"status": "completed", "usage": {"input_tokens": 100, "output_tokens": 10},
                 "output": [{"type": "message", "content": [{"type": "output_text", "text": "Записала, до свидания!"}]},
                            {"type": "function_call", "name": "finish_call", "call_id": "c1",
                             "arguments": json.dumps({"outcome": "interested", "comment": "Сочи", "facts": {"city": "Сочи"}})}]}},
            ))

        dialog = Dialog(load("kitchen"), self.backend(handler))
        dialog.greet()
        chunks = []

        async def sink(c):
            chunks.append(c)

        turn = await dialog.reply("Да, Сочи", sink)
        self.assertEqual(chunks, ["Записала, ", "до свидания!"])
        self.assertEqual((turn.result.outcome, turn.result.facts, turn.text), ("interested", {"city": "Сочи"}, "Записала, до свидания!"))
        body = sent[0]
        self.assertTrue(body["stream"])
        self.assertEqual(body["model"], "gpt-test")
        self.assertEqual(body["tools"][0]["type"], "function")
        self.assertEqual([m["role"] for m in body["input"]], ["user", "assistant", "user"])
        self.assertIn("Мария", body["instructions"])
        self.assertEqual((dialog.cost, dialog.cost_unit), (110 * 6.5, "ед."))

    async def test_plain_json_answer_and_errors(self):
        def plain(request):
            return httpx.Response(200, json={"status": "completed", "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "В каком городе?"}]}]})

        dialog = Dialog(load("kitchen"), self.backend(plain))
        dialog.greet()
        turn = await dialog.reply("Да")
        self.assertEqual((turn.text, turn.result), ("В каком городе?", None))

        def broken(request):
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=sse(
                {"type": "response.completed", "response": {"status": "completed", "output": [
                    {"type": "function_call", "name": "finish_call", "arguments": "{oops"}]}}))

        dialog = Dialog(load("kitchen"), self.backend(broken))
        dialog.greet()
        turn = await dialog.reply("Нет")
        self.assertEqual((turn.result.outcome, turn.text), ("callback", load("kitchen").farewell))

        dialog = Dialog(load("kitchen"), self.backend(lambda r: httpx.Response(402, text="no credits")))
        dialog.greet()
        with self.assertRaisesRegex(RuntimeError, "402"):
            await dialog.reply("Да")


class SpeechTest(unittest.TestCase):
    def test_phrase_splitter(self):
        s = speech.PhraseSplitter()
        out = []
        for chunk in ["Отлично", "! А в каком", " городе", " будет кухня? Спа", "сибо."]:
            out += s.feed(chunk)
        # одно слово отдельным куском звучит с паузой: склеивается со следующим предложением
        self.assertEqual(out, ["Отлично! А в каком городе будет кухня?"])
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

    def test_split_long_text_for_yandex(self):
        text = "Первое предложение про кухню. " * 12 + "Хвост без точки, но с запятой, и ещё слова"
        parts = speech.split_long(text, 100)
        self.assertTrue(all(len(p) <= 100 for p in parts), parts)
        self.assertEqual(" ".join(parts), " ".join(text.split()))
        self.assertEqual(speech.split_long("коротко"), ["коротко"])

    def test_collector_closes_on_pause(self):
        c = speech.UtteranceCollector(gap=0.5, open_gap=1.0, stale_tail=1.2)
        res = lambda t, final=False: {"type": "Results", "is_final": final, "speech_final": False, "channel": {"alternatives": [{"transcript": t}]}}
        self.assertIsNone(c.gap_left(0))  # ещё нечего закрывать
        c.handle(res("Москва", final=True))  # без точки: клиент, скорее всего, ещё договаривает
        self.assertAlmostEqual(c.gap_left(c.final_at + 0.2), 0.8)
        c.handle(res("Краснодар.", final=True))
        self.assertAlmostEqual(c.gap_left(c.final_at + 0.2), 0.3)
        c.handle(res("и ещё"))  # клиент продолжает говорить: ждём не паузу, а пока промежуточный текст застынет
        self.assertAlmostEqual(c.gap_left(c.tail_at + 1), 0.2)
        c.handle(res("и ещё Сочи", final=True))
        self.assertEqual(c.gap_left(c.final_at + 1), 0.0)
        self.assertEqual(c.close_on_gap(), speech.Heard("Москва Краснодар. и ещё Сочи", True))
        self.assertIsNone(c.gap_left(10))

    def test_collector_closes_on_stale_interim(self):
        c = speech.UtteranceCollector(gap=0.7, open_gap=1.0, stale_tail=1.2)
        res = lambda t, final=False: {"type": "Results", "is_final": final, "speech_final": False, "channel": {"alternatives": [{"transcript": t}]}}
        c.handle(res("Около 300"))
        c.handle(res("Около 300 тысяч"))
        c.handle(res("Около 300 тысяч"))  # тот же текст: таймер не сдвигается
        self.assertEqual(c.gap_left(c.tail_at + 1.5), 0.0)
        self.assertEqual(c.close_on_gap(), speech.Heard("Около 300 тысяч", True))
        self.assertIsNone(c.handle(res("Около 300 тысяч.", final=True)))  # запоздалый is_final не повторяем
        c.handle(res("Сколько стоит"))
        c.close_on_gap()
        c.handle(res("Сколько стоит кухня и сколько делаете?", final=True))  # продолжение не теряем
        self.assertEqual(c.close_on_gap(), speech.Heard("кухня и сколько делаете?", True))
        self.assertIsNone(c.gap_left(10))
        c.handle(res("Краснодар", final=True))
        self.assertEqual(c.parts, ["Краснодар"])


class CalmTextTest(unittest.TestCase):
    def test_no_cheerful_tone(self):
        self.assertEqual(speech.calm_text("Здравствуйте! Отлично, подскажите?"), "Здравствуйте. Хорошо, подскажите?")
        self.assertEqual(speech.calm_text("Хорошо, отлично. Город?"), "Хорошо. Город?")
        self.assertEqual(speech.calm_text("Плёнка — отличный выбор!"), "Плёнка — отличный выбор.")


class FillerTest(unittest.TestCase):
    def test_no_second_reaction_after_filler(self):
        from voice.call import _strip_reaction
        self.assertEqual(_strip_reaction("Поняла. Когда удобно, чтобы позвонил специалист?"), "Когда удобно, чтобы позвонил специалист?")
        self.assertEqual(_strip_reaction("Поняла."), "Поняла.")


class PauseTest(unittest.TestCase):
    def test_trim_silence_keeps_pause(self):
        import array, math
        rate = 8000
        tone = array.array("h", [int(5000 * math.sin(i / 3)) for i in range(rate)]).tobytes()
        out = speech.trim_silence(bytes(rate) + tone + bytes(rate * 2), rate, 100)
        self.assertAlmostEqual(len(out) / 2 / rate, 1.14, delta=0.03)  # 30 мс в начале, звук, 100 мс в конце

    def test_long_inner_pause_is_shortened(self):
        import array, math
        rate = 8000
        tone = array.array("h", [int(5000 * math.sin(i / 3)) for i in range(rate // 2)]).tobytes()
        out = speech.shorten_pauses(tone + bytes(rate) + tone, rate, 100)  # 0,5 с тишины между словами
        self.assertAlmostEqual(len(out) / 2 / rate, 1.1, delta=0.02)
        self.assertEqual(speech.shorten_pauses(tone + bytes(rate // 10) + tone, rate, 150), tone + bytes(rate // 10) + tone)

    def test_fewer_commas(self):
        self.assertEqual(speech.fewer_pauses("Хорошо. Подскажите, когда удобно?"), "Хорошо, подскажите когда удобно?")


class CasualTextTest(unittest.TestCase):
    def test_levels(self):
        phrase = "Подскажите, пожалуйста, на какую сумму вы ориентируетесь? Нужно от двухсот пятидесяти тысяч рублей."
        token = speech.settings_override.set({"casual": 0})
        self.assertEqual(speech.casual_text(phrase), phrase)
        speech.settings_override.reset(token)
        token = speech.settings_override.set({"casual": 1})
        casual = speech.casual_text(phrase)
        speech.settings_override.reset(token)
        self.assertIn("тыщ", casual)
        self.assertNotIn("пожалуйста", casual)
        token = speech.settings_override.set({"casual": 1})
        self.assertEqual(speech.casual_text(phrase), casual)  # одна фраза звучит одинаково: работает кэш
        speech.settings_override.reset(token)
        self.assertEqual(speech.casual_text("Так…"), "Так…")


class SynthesisTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        os.environ["VOICE_TTS_CACHE"] = "off"

    def tearDown(self):
        del os.environ["VOICE_TTS_CACHE"]

    async def test_cache_synthesizes_same_phrase_once(self):
        calls = []

        def handler(request):
            calls.append(1)
            return httpx.Response(200, content=b"pcm1")

        os.environ["VOICE_TTS_API"] = "v1"
        with tempfile.TemporaryDirectory() as d:
            os.environ["VOICE_TTS_CACHE"] = d
            try:
                v = speech.Voice("alena")
                self.assertEqual(await self.run_with(handler, v), b"pcm1")
                self.assertEqual(await self.run_with(handler, v), b"pcm1")
                await self.run_with(handler, speech.Voice("alena", speed=1.2))
            finally:
                del os.environ["VOICE_TTS_API"]
        self.assertEqual(len(calls), 2)

    async def run_with(self, handler, voice):
        os.environ["YANDEX_TTS_API_KEY"] = "k"
        speech._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await speech.synthesize_pcm("Здравствуйте", voice)
        finally:
            speech._http = None
            del os.environ["YANDEX_TTS_API_KEY"]

    async def test_v3_with_role_then_v1_fallback(self):
        import base64
        seen = []

        def v3(request):
            seen.append(json.loads(request.content))
            lines = [json.dumps({"result": {"audioChunk": {"data": base64.b64encode(b"ab").decode()}}}),
                     json.dumps({"result": {"audioChunk": {"data": base64.b64encode(b"cd").decode()}}})]
            return httpx.Response(200, text="\n".join(lines))

        self.assertEqual(await self.run_with(v3, speech.Voice("dasha", "friendly", 1.1)), b"abcd")
        self.assertIn({"role": "friendly"}, seen[0]["hints"])
        self.assertEqual(seen[0]["outputAudioSpec"]["rawAudio"]["sampleRateHertz"], speech.TTS_RATE)

        def no_v3(request):
            if "v3" in request.url.path:
                return httpx.Response(403, text="no access")
            seen.append(dict(x.split("=") for x in request.content.decode().split("&")))
            return httpx.Response(200, content=b"pcm")

        self.assertEqual(await self.run_with(no_v3, speech.Voice("alena", "good")), b"pcm")
        self.assertEqual((seen[-1]["voice"], seen[-1]["emotion"]), ("alena", "good"))

        with self.assertRaisesRegex(speech.TTSRequestError, "400"):
            await self.run_with(lambda r: httpx.Response(400, text="bad role"), speech.Voice("dasha", "evil"))


class ElevenTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        os.environ["VOICE_TTS_CACHE"] = "off"

    def tearDown(self):
        del os.environ["VOICE_TTS_CACHE"]

    async def test_openrouter_pcm_is_resampled(self):
        seen = []

        def handler(request):
            seen.append((request.headers["authorization"], json.loads(request.content)))
            return httpx.Response(200, headers={"content-type": "audio/pcm;rate=24000"}, content=b"\x00\x10" * 240)

        os.environ["OPENROUTER_API_KEY"] = "or"
        speech._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            pcm = await speech.synthesize_pcm("Привет", speech.Voice("el:sarah", "", 1.1))
            self.assertIn("el:sarah", speech.voices())
        finally:
            speech._http = None
            del os.environ["OPENROUTER_API_KEY"]
        self.assertEqual(len(pcm), 480 * speech.TTS_RATE // 24000)
        auth, body = seen[0]
        self.assertEqual((auth, body["voice"], body["model"], body["response_format"], body["speed"]),
                         ("Bearer or", "sarah", "elevenlabs/eleven-flash-v2.5", "pcm", 1.1))

    async def test_direct_account_voice(self):
        seen = []

        def handler(request):
            seen.append(request)
            if request.url.path.endswith("/voices"):
                return httpx.Response(200, json={"voices": [{"name": "Ольга клон", "voice_id": "vid123"}]})
            return httpx.Response(200, content=b"\x00\x10" * 24)

        os.environ["ELEVENLABS_API_KEY"] = "xi"
        speech._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            await speech.load_eleven_voices()
            self.assertIn("11:Ольга клон", speech.voices())
            pcm = await speech.synthesize_pcm("Привет", speech.Voice("11:Ольга клон"))
        finally:
            speech._http = None
            speech._eleven_direct.clear()
            del os.environ["ELEVENLABS_API_KEY"]
        tts = seen[-1]
        self.assertTrue(tts.url.path.endswith("/text-to-speech/vid123"))
        self.assertEqual((tts.url.params["output_format"], tts.headers["xi-api-key"]), ("pcm_24000", "xi"))
        self.assertEqual(json.loads(tts.content)["model_id"], "eleven_flash_v2_5")
        self.assertEqual(len(pcm), 48 * speech.TTS_RATE // 24000)

    async def test_voice_name_prefix_and_unknown_name(self):
        def handler(request):
            if request.url.path.endswith("/voices"):
                return httpx.Response(200, json={"voices": [{"name": "Eva - calm", "voice_id": "eva1"}]})
            return httpx.Response(200, content=b"")

        os.environ["ELEVENLABS_API_KEY"] = "xi"
        speech._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            self.assertEqual(await speech._eleven_voice_id("eva"), "eva1")
            with self.assertRaisesRegex(speech.TTSRequestError, "ElevenLabs 404.*Eva - calm"):
                await speech._eleven_voice_id("Anna")
        finally:
            speech._http = None
            speech._eleven_direct.clear()
            del os.environ["ELEVENLABS_API_KEY"]

    async def test_settings_page_file_changes_voice_and_sliders(self):
        seen = []

        def handler(request):
            seen.append(json.loads(request.content))
            return httpx.Response(200, content=b"\x00\x10" * 24)

        with tempfile.TemporaryDirectory() as tmp:
            old = speech.SETTINGS_FILE
            speech.SETTINGS_FILE = str(Path(tmp) / "voice.json")
            os.environ["ELEVENLABS_API_KEY"] = "xi"
            speech._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            try:
                saved = speech.save_settings({"voice_id": "tj9wwIq7s2HekkDfg1KH", "model": "eleven_v4", "speed": 1.5,
                                              "stability": 0.8, "tail_ms": 700, "junk": 1, "calm": False})
                self.assertEqual((saved["speed"], saved["tail_ms"], "junk" in saved), (1.2, 700, False))
                await speech.synthesize_pcm("Отлично!", speech.Voice("11:old", "", 1.0))
                body = seen[-1]
                self.assertEqual((body["model_id"], body["voice_settings"]["stability"], body["voice_settings"]["speed"],
                                  body["text"]), ("eleven_v4", 0.8, 1.2, "Отлично!"))
                self.assertEqual(speech.YandexStream().collector.stale_tail, 0.7)
                # несохранённые ползунки страницы «Прослушать» не трогают звонки
                token = speech.settings_override.set({"stability": 0.3})
                await speech.synthesize_pcm("Да", speech.Voice("11:tj9wwIq7s2HekkDfg1KH"))
                speech.settings_override.reset(token)
                self.assertEqual(seen[-1]["voice_settings"]["stability"], 0.3)
                # голос Яндекса (автотест) файл не меняет
                self.assertEqual(speech.effective_voice(speech.Voice("dasha", "", 1.0)).name, "dasha")
            finally:
                speech._http = None
                speech.SETTINGS_FILE = old
                speech._live_cache.update(mtime=None, data={})
                del os.environ["ELEVENLABS_API_KEY"]

    async def test_stream_plays_pieces_and_fills_cache(self):
        import array, math
        tone = array.array("h", [int(5000 * math.sin(i / 3)) for i in range(4800)]).tobytes()
        seen = []

        def handler(request):
            seen.append(request.url.path)
            return httpx.Response(200, content=bytes(2400) + tone + bytes(9600), headers={"request-id": "r1"})

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["VOICE_TTS_CACHE"] = tmp
            os.environ["ELEVENLABS_API_KEY"] = "xi"
            speech._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            heard = []

            async def on_audio(pcm):
                heard.append(pcm)
            try:
                chain = []
                speech.tts_chain.set(chain)
                out = await speech.stream_pcm("Хорошо", speech.Voice("11:tj9wwIq7s2HekkDfg1KH"), on_audio)
                again = await speech.stream_pcm("Хорошо", speech.Voice("11:tj9wwIq7s2HekkDfg1KH"), on_audio)
            finally:
                speech._http = None
                speech.tts_chain.set(None)
                del os.environ["ELEVENLABS_API_KEY"]
                os.environ["VOICE_TTS_CACHE"] = "off"
        self.assertEqual(seen, ["/v1/text-to-speech/tj9wwIq7s2HekkDfg1KH/stream"])  # второй раз из кэша
        self.assertEqual(chain, ["r1"])
        self.assertEqual(b"".join(heard[:-1]), out)
        self.assertEqual(out, again)
        rate = speech.TTS_RATE
        self.assertLess(len(out), len(tone) * rate // 24000 + rate // 2)  # тишина по краям обрезана

    def test_resample_keeps_level(self):
        import array
        src = array.array("h", [1000] * 100).tobytes()
        out = array.array("h", speech.resample(src, 8000, 16000))
        self.assertEqual((len(out), min(out), max(out)), (200, 1000, 1000))


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

        session = CallSession(Dialog(load("kitchen"), AnthropicBackend(client=client)), send_audio, send_event, synthesize=synth, save=save)
        await session.start()
        await session.heard("да", False)
        await session.heard("Да, актуально, присматриваюсь", True)
        await session.reply_task
        await session.heard("Бюджет тысяч сто", True)
        await session.reply_task
        await asyncio.wait_for(session.done.wait(), 2)
        result = [e for e in events if e["type"] == "result"][0]
        self.assertEqual(result["outcome"], "not_relevant")
        self.assertEqual(len(audio), 3)  # приветствие + «Отлично! В каком городе…» одним куском + прощание
        self.assertEqual(saved, [{"phone": "", "mode": "браузер"}])
        self.assertIn({"type": "interim", "text": "да"}, events)

    async def test_expected_answer_gets_quick_reply_without_model(self):
        client = FakeClient([say("В каком городе будет кухня?")])
        events, said = [], []

        async def nothing(_):
            pass

        async def send_event(e):
            events.append(e)

        async def synth(text, voice):
            said.append(text)
            return b"\x00\x00" * 160

        async def save(dialog, **kw):
            return results.record(dialog, **kw)

        session = CallSession(Dialog(load("kitchen"), AnthropicBackend(client=client)), nothing, send_event,
                              synthesize=synth, save=save)
        await session.start()
        await session.heard("Да, актуально.", True)
        await session.reply_task
        self.assertEqual(client.requests, [])  # модель не спрашивали
        quick = load("kitchen").quick[0]["reply"]
        self.assertIn({"type": "transcript", "role": "robot", "text": quick, "first_text_ms": 0}, events)
        for _ in range(100):  # дожидаемся, пока быстрый ответ прозвучит
            if not session.is_speaking():
                break
            await asyncio.sleep(0.01)
        await session.heard("Конечно", True)  # второй быстрый ответ: на вопрос о сроках
        await session.reply_task
        self.assertEqual(client.requests, [])
        for _ in range(100):
            if not session.is_speaking():
                break
            await asyncio.sleep(0.01)
        await session.heard("Москва", True)  # дальше — модель, и она видит весь обмен
        await session.reply_task
        self.assertEqual(len(client.requests), 1)
        self.assertIn(quick, json.dumps(client.requests[0], ensure_ascii=False))
        await session.hangup()

    async def test_client_continuing_over_quick_reply_undoes_it(self):
        client = FakeClient([say("Поняла, перезвоню после семи.")])
        events = []

        async def nothing(_):
            pass

        async def send_event(e):
            events.append(e)

        async def synth(text, voice):
            return b"\x00\x00" * speech.TTS_RATE * 2  # 2 секунды звука

        async def save(dialog, **kw):
            return results.record(dialog, **kw)

        session = CallSession(Dialog(load("kitchen"), AnthropicBackend(client=client)), nothing, send_event,
                              synthesize=synth, save=save)
        await session.start()
        session.speaking_until = 0.0
        await session.heard("Да, актуально.", True)
        await session.reply_task
        for _ in range(100):
            if session.audio_sent and session.is_speaking():
                break
            await asyncio.sleep(0.01)
        await session.heard("но я за рулём", True)  # быстрый ответ ещё звучит
        await session.reply_task
        sent = client.requests[0]["messages"]
        self.assertEqual(sent[-1], {"role": "user", "content": "Да, актуально. но я за рулём"})
        await session.hangup()

    async def test_broken_voice_falls_back_to_operator_voice(self):
        audio, used = [], []

        async def send_audio(pcm):
            audio.append(pcm)

        async def nothing(_):
            pass

        async def synth(text, voice):
            used.append(voice.name)
            if voice.name.startswith("11:"):
                raise speech.TTSRequestError(401, "no permission", "ElevenLabs")
            return b"\x00\x00" * 160

        async def save(dialog, **kw):
            return results.record(dialog, **kw)

        os.environ["VOICE_VOICE"] = "11:Eva"
        try:
            session = CallSession(Dialog(load("kitchen"), AnthropicBackend(client=FakeClient([]))), send_audio, nothing,
                                  synthesize=synth, save=save)
        finally:
            del os.environ["VOICE_VOICE"]
        await session.start()
        for _ in range(50):
            if audio:
                break
            await asyncio.sleep(0.01)
        await session.hangup()
        self.assertEqual(used[:2], ["11:Eva", load("kitchen").voice])
        self.assertEqual(len(audio), 1)

    async def test_slow_greeting_is_not_dropped_by_hello_or_silence(self):
        import voice.call as call_mod
        audio, events = [], []

        async def send_audio(pcm):
            audio.append(pcm)

        async def send_event(e):
            events.append(e)

        async def synth(text, voice):
            await asyncio.sleep(0.3)  # медленный синтез, как первый запрос к ElevenLabs
            return b"\x00\x00" * 16

        async def save(dialog, **kw):
            return results.record(dialog, **kw)

        old = call_mod.SILENCE_AFTER_S
        call_mod.SILENCE_AFTER_S = 0.1
        try:
            session = CallSession(Dialog(load("kitchen"), AnthropicBackend(client=FakeClient([]))), send_audio, send_event,
                                  synthesize=synth, save=save)
            await session.start()
            await asyncio.sleep(0.15)
            await session.heard("Алло,", True)   # клиент не слышит робота и повторяет «алло»
            await asyncio.sleep(0.25)
            self.assertEqual(len(audio), 1)        # приветствие прозвучало
            self.assertIsNone(session.reply_task)  # ни молчания, ни ответа на «алло»
        finally:
            call_mod.SILENCE_AFTER_S = old
            await session.hangup()

    async def test_hello_over_playing_greeting_is_ignored(self):
        audio, events = [], []

        async def send_audio(pcm):
            audio.append(pcm)

        async def send_event(e):
            events.append(e)

        async def synth(text, voice):
            return b"\x00\x00" * speech.TTS_RATE * 2  # 2 секунды звука

        async def save(dialog, **kw):
            return results.record(dialog, **kw)

        session = CallSession(Dialog(load("kitchen"), AnthropicBackend(client=FakeClient([]))), send_audio, send_event,
                              synthesize=synth, save=save)
        await session.start()
        for _ in range(50):
            if audio:
                break
            await asyncio.sleep(0.01)
        await session.heard("Алло.", True)  # приветствие ещё звучит
        self.assertIsNone(session.reply_task)
        self.assertNotIn({"type": "clear"}, events)
        await session.hangup()

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

        session = CallSession(Dialog(load("kitchen"), AnthropicBackend(client=FakeClient([]))), nothing, send_event, synthesize=synth, save=save)
        await session.start()
        await session.hangup()
        self.assertEqual(events[-1]["outcome"], "callback")


class PhoneTest(unittest.IsolatedAsyncioTestCase):
    async def test_audiosocket_call_end_to_end(self):
        import uuid as uuidlib
        from voice import sip
        saved = []

        class ScriptASR:
            def __init__(self, call):
                self.call, self.got = call, 0

            async def run(self):
                await self.call.heard("Алло", True)
                await asyncio.sleep(0.3)
                await self.call.heard("Нет, не актуально", True)

            async def send(self, pcm):
                self.got += len(pcm)

            async def close(self):
                pass

        async def synth(text, voice):
            return b"\x10\x00" * (speech.TTS_RATE // 50)  # 20 мс

        async def save(dialog, **kw):
            saved.append(results.record(dialog, **kw))
            return saved[-1]

        client = FakeClient([finish({"outcome": "not_relevant", "comment": "Шаг 1: неактуально"}, "Поняла, всего доброго!")])
        call_id = str(uuidlib.uuid4())
        sip.PENDING[call_id] = sip.Pending("79990001122", "kitchen")

        async def on_socket(reader, writer):
            await sip.PhoneCall(reader, writer, make_dialog=lambda op: Dialog(op, AnthropicBackend(client=client)),
                                make_asr=ScriptASR, synthesize=synth, save=save).run()

        server = await asyncio.start_server(on_socket, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(sip.frame(sip.KIND_UUID, uuidlib.UUID(call_id).bytes))
        writer.write(sip.frame(sip.KIND_AUDIO, b"\0" * sip.FRAME_BYTES))
        await writer.drain()
        audio_frames = 0
        while True:
            kind, payload = await asyncio.wait_for(sip.read_frame(reader), 5)
            if kind == sip.KIND_AUDIO:
                self.assertEqual(len(payload), sip.FRAME_BYTES)
                audio_frames += 1
            elif kind == sip.KIND_HANGUP:
                break
        writer.close()
        server.close()
        self.assertEqual(audio_frames, 2)  # приветствие и прощание, по 20 мс в 8 кГц
        self.assertEqual((saved[0]["outcome"], saved[0]["phone"], saved[0]["mode"]), ("not_relevant", "79990001122", "телефон"))
        self.assertTrue(sip.PENDING.get(call_id) is None)

    async def test_dial_validates_and_registers(self):
        from voice import sip
        seen = []

        async def originate(call_id, phone):
            seen.append((call_id, phone))

        call_id = await sip.dial("+7 (999) 000-11-22", originate=originate)
        self.assertEqual(seen, [(call_id, "79990001122")])
        self.assertIn(call_id, sip.PENDING)
        sip.PENDING.clear()
        with self.assertRaises(ValueError):
            await sip.dial("123", originate=originate)

    def test_downsample(self):
        import array
        from voice import sip
        pcm = array.array("h", [600] * 48).tobytes()
        self.assertEqual(array.array("h", sip.downsample(pcm, 48000, 8000)).tolist(), [600] * 8)


class AutotestTest(unittest.IsolatedAsyncioTestCase):
    async def test_virtual_client_talks_to_phone_robot(self):
        import array
        from voice import autotest, simulate

        async def synth(text, voice):  # «речь»: тон по 10 мс на букву
            wave = [3000] * 20 + [-3000] * 20  # 400 Гц на 16 кГц: переживает понижение до 8 кГц
            return array.array("h", wave * (speech.TTS_RATE // 100 // 40) * len(text)).tobytes()

        clients = []

        class EnergyASR:
            """Распознаёт по громкости: после 300 мс тишины отдаёт последнюю реплику абонента."""
            def __init__(self, call):
                self.call, self.voiced, self.quiet = call, False, 0

            async def run(self):
                await asyncio.Event().wait()

            async def send(self, pcm):
                if autotest.rms(pcm) > 500:
                    self.voiced, self.quiet = True, 0
                elif self.voiced:
                    self.quiet += 1
                    if self.quiet >= 15:
                        self.voiced = False
                        await self.call.heard(clients[0].run.said[-1], True)

            async def close(self):
                pass

        robot = AnthropicBackend(client=FakeClient([finish({"outcome": "not_relevant", "comment": "неактуально"}, "Поняла, всего доброго!")]))
        persona = simulate.Persona("t", "not_relevant", "", scripted=["Алло", "Нет, не актуально."])
        client = autotest.VirtualClient(persona, robot, None, make_asr=EnergyASR, synthesize=synth)
        clients.append(client)
        run = await asyncio.wait_for(client.talk(), 30)
        self.assertEqual(run.outcome, "not_relevant")
        self.assertEqual(run.said, ["Алло", "Нет, не актуально."])
        self.assertEqual(len(run.reactions), 2)  # приветствие и ответ
        self.assertTrue(all(ms < 1500 for ms in run.reactions), run.reactions)
        self.assertEqual(run.problems, [])
        rows = autotest.rows([run], [], "14.10.2026 21:00", "abc1234")
        self.assertEqual(rows[0][:7], ["14.10.2026 21:00", "abc1234", "голос", "t", "not_relevant", "not_relevant", "да"])


class ConcludeTest(unittest.IsolatedAsyncioTestCase):
    async def test_hangup_before_finish_asks_model_for_result(self):
        client = FakeClient([say("Хорошо, в какое время удобно?"),
                             finish({"outcome": "callback", "comment": "Перезвонить после семи"})])
        dialog = Dialog(load("kitchen"), AnthropicBackend(client=client))
        dialog.greet()
        await dialog.reply("Я за рулём, перезвоните после семи")
        await dialog.conclude()
        self.assertEqual(dialog.result.outcome, "callback")
        self.assertTrue(dialog.result.comment.startswith("Клиент положил трубку."))
        self.assertIn("finish_call", json.dumps(client.requests[-1], ensure_ascii=False))

    async def test_no_conclude_without_client_answer(self):
        client = FakeClient([])
        dialog = Dialog(load("kitchen"), AnthropicBackend(client=client))
        dialog.greet()
        await dialog.conclude()
        self.assertIsNone(dialog.result)
        self.assertEqual(client.requests, [])

    def test_clean_client_line(self):
        from voice.simulate import clean_client_line
        self.assertEqual(clean_client_line("В этом месяце планирую.\n\nПользователь: Какой бюджет?\n\nАссистент: 300"),
                         "В этом месяце планирую.")
        self.assertLessEqual(len(clean_client_line("Очень длинно. " * 40)), 220)


class ResultsTest(unittest.IsolatedAsyncioTestCase):
    async def test_jsonl_and_sheet_append(self):
        client = FakeClient([finish({"outcome": "interested", "comment": "Казань", "facts": {"city": "Казань"}}, "Спасибо!")])
        dialog = Dialog(load("kitchen"), AnthropicBackend(client=client))
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


class NoiseAndFillerTest(unittest.IsolatedAsyncioTestCase):
    def session(self, client, audio, events, said=None):
        async def send_audio(pcm):
            audio.append(pcm)

        async def send_event(e):
            events.append(e)

        async def synth(text, voice):
            if said is not None:
                said.append(text)
            return b"\x00\x00" * speech.TTS_RATE * 2  # 2 секунды звука

        async def save(dialog, **kw):
            return results.record(dialog, **kw)

        return CallSession(Dialog(load("kitchen"), AnthropicBackend(client=client)), send_audio, send_event,
                           synthesize=synth, save=save)

    async def wait_audio(self, audio):
        for _ in range(50):
            if audio:
                return
            await asyncio.sleep(0.01)

    async def test_single_noise_word_does_not_stop_robot_but_stop_word_does(self):
        audio, events = [], []
        session = self.session(FakeClient([say("Поняла.")]), audio, events)
        await session.start()
        await self.wait_audio(audio)
        session.client_spoke = True
        await session.heard("Тонь,", True)  # музыка на фоне распозналась как слово
        self.assertIsNone(session.reply_task)
        self.assertNotIn({"type": "clear"}, events)
        await session.heard("Нет.", True)
        self.assertIn({"type": "clear"}, events)
        await session.hangup()

    async def test_filler_while_model_thinks(self):
        from voice import call
        audio, events, said = [], [], []
        session = self.session(FakeClient([say("Хорошо, записала.")], delay=0.6), audio, events, said)
        old = call.FILLER_CHANCE, call.FILLER_AFTER_S
        call.FILLER_CHANCE, call.FILLER_AFTER_S = 1.0, 0.1
        try:
            await session.start()
            await self.wait_audio(audio)
            session.speaking_until = 0.0
            await session.heard("Город Москва.", True)
            await session.reply_task
            for _ in range(50):
                if said[-1] == "Хорошо, записала.":
                    break
                await asyncio.sleep(0.01)
        finally:
            call.FILLER_CHANCE, call.FILLER_AFTER_S = old
        self.assertIn(said[1], call.FILLERS)
        self.assertEqual(said[-1], "Хорошо, записала.")
        robot = [e["text"] for e in events if e.get("role") == "robot"]
        self.assertNotIn(said[1], robot)  # «так…» не попадает в разговор
        await session.hangup()


class YandexAsrTest(unittest.IsolatedAsyncioTestCase):
    def test_repeated_partial_after_early_close_is_not_new_speech(self):
        c = speech.UtteranceCollector(gap=0.7, stale_tail=0.01)
        part = lambda t: {"type": "Results", "is_final": False, "channel": {"alternatives": [{"transcript": t}]}}
        fin = lambda t: {"type": "Results", "is_final": True, "channel": {"alternatives": [{"transcript": t}]}}
        c.handle(part("в этом месяце хочу сделать"))
        self.assertEqual(c.close_on_gap().text, "в этом месяце хочу сделать")
        self.assertIsNone(c.handle(part("в этом месяце хочу")))
        self.assertIsNone(c.handle(part("в этом месяце хочу сделать")))
        self.assertIsNone(c.handle(fin("в этом месяце хочу сделать")))
        self.assertIsNone(c.handle({"type": "UtteranceEnd"}))
        self.assertEqual(c.handle(part("да")).text, "да")  # после окончательного текста — новая речь

    def test_two_early_closes_then_whole_final(self):
        c = speech.UtteranceCollector(gap=0.7, stale_tail=0.01)
        part = lambda t: {"type": "Results", "is_final": False, "channel": {"alternatives": [{"transcript": t}]}}
        fin = lambda t: {"type": "Results", "is_final": True, "channel": {"alternatives": [{"transcript": t}]}}
        c.handle(part("ирина петровна"))
        self.assertEqual(c.close_on_gap().text, "ирина петровна")
        self.assertEqual(c.handle(part("ирина петровна завтра после шести")).text, "завтра после шести")
        self.assertEqual(c.close_on_gap().text, "завтра после шести")
        self.assertIsNone(c.handle(fin("ирина петровна завтра после шести")))  # не повторяем всю фразу
        self.assertIsNone(c.handle({"type": "UtteranceEnd"}))

    def test_utterance_end_on_hanging_word_waits(self):
        c = speech.UtteranceCollector(gap=0.7, stale_tail=0.65)
        fin = lambda t: {"type": "Results", "is_final": True, "channel": {"alternatives": [{"transcript": t}]}}
        c.handle(fin("город"))
        self.assertIsNone(c.handle({"type": "UtteranceEnd"}))  # не закрываем на «город»
        self.assertGreater(c.gap_left(c.final_at), 1.5)
        c.handle(fin("сыктывкар"))
        self.assertEqual(c.handle({"type": "UtteranceEnd"}).text, "город сыктывкар")

    def test_hanging_word_waits_longer(self):
        c = speech.UtteranceCollector(gap=0.7, stale_tail=0.9)
        c.handle({"type": "Results", "is_final": False, "channel": {"alternatives": [{"transcript": "город"}]}})
        self.assertGreater(c.gap_left(c.tail_at), 1.5)
        c.handle({"type": "Results", "is_final": False, "channel": {"alternatives": [{"transcript": "город сыктывкар"}]}})
        self.assertLess(c.gap_left(c.tail_at), 1.0)

    def test_low_confidence_is_noise(self):
        c = speech.UtteranceCollector(gap=0.7)
        noise = {"type": "Results", "is_final": True, "channel": {"alternatives": [{"transcript": "Тонь", "confidence": 0.2}]}}
        self.assertIsNone(c.handle(noise))
        self.assertIsNone(c.handle({"type": "UtteranceEnd"}))

    async def test_events_become_deepgram_messages(self):
        def resp(kind, text=""):
            alt = NS(text=text, confidence=0.0)
            return NS(WhichOneof=lambda _: kind, **{kind: NS(alternatives=[alt])})

        async def responses():
            for r in (resp("partial", "город"), resp("final", "Город Москва."), resp("eou_update")):
                yield r

        stream = speech.YandexStream()
        await stream._read(responses())
        heard = []

        async def collect():
            async for h in stream:
                heard.append(h)
        await asyncio.wait_for(collect(), 2)
        self.assertEqual([(h.text, h.final) for h in heard],
                         [("город", False), ("Город Москва.", False), ("Город Москва.", True)])

    def test_factory_falls_back_to_deepgram_without_libraries(self):
        os.environ["VOICE_ASR"] = "yandex"
        try:
            stream = speech.asr_stream(None)
        finally:
            del os.environ["VOICE_ASR"]
        try:
            import grpc  # noqa: F401
            from yandex.cloud.ai.stt.v3 import stt_pb2  # noqa: F401
            expected = speech.YandexStream
        except ImportError:
            expected = speech.DeepgramStream
        self.assertIsInstance(stream, expected)


class PacerTest(unittest.IsolatedAsyncioTestCase):
    async def test_burst_from_tunnel_is_played_every_20_ms(self):
        import time as _t
        from voice import pacer

        async def robot(reader, writer):  # робот: читает UUID, шлёт пачкой 10 кадров и отбой
            await pacer.read_frame(reader)
            writer.write(b"".join(pacer.pack(pacer.KIND_AUDIO, bytes(320)) for _ in range(10)))
            writer.write(pacer.pack(pacer.KIND_HANGUP, b""))
            await writer.drain()
            await asyncio.sleep(0.5)
            writer.close()

        bot = await asyncio.start_server(robot, "127.0.0.1", 0)
        old = pacer.ROBOT
        pacer.ROBOT = ("127.0.0.1", bot.sockets[0].getsockname()[1])
        front = await asyncio.start_server(pacer.on_asterisk, "127.0.0.1", 0)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", front.sockets[0].getsockname()[1])
            writer.write(pacer.pack(0x01, bytes(16)))
            await writer.drain()
            times = []
            while True:
                kind, _ = await asyncio.wait_for(pacer.read_frame(reader), 3)
                if kind == pacer.KIND_HANGUP:
                    break
                times.append(_t.monotonic())
            writer.close()
        finally:
            pacer.ROBOT = old
            front.close()
            bot.close()
        self.assertEqual(len(times), 10)
        self.assertGreater(times[-1] - times[0], 0.15)  # пачка растянута во времени, а не вывалена разом
