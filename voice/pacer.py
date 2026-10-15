"""
Ровная подача звука робота в Asterisk. Работает на российском VPS рядом с Asterisk, только стандартная библиотека.

Робот живёт на основном сервере, звук звонка ходит через SSH-туннель. Туннель доставляет кадры
пачками: Asterisk сразу превращает каждую пачку в RTP, и у клиента фразы рвутся и наслаиваются.
Здесь кадры робота копятся в небольшом буфере и уходят в Asterisk строго раз в 20 мс.

    Asterisk -> 127.0.0.1:9092 (здесь) -> 127.0.0.1:9094 (туннель) -> робот
Звук клиента идёт к роботу без задержки.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import os
import struct
import time

log = logging.getLogger("voice.pacer")

LISTEN = ("127.0.0.1", int(os.getenv("PACER_LISTEN_PORT", "9092")))
ROBOT = ("127.0.0.1", int(os.getenv("PACER_ROBOT_PORT", "9094")))
FRAME_S = 0.02
PREBUFFER = int(os.getenv("PACER_PREBUFFER_FRAMES", "4"))  # 80 мс запаса на неровности туннеля
KIND_HANGUP, KIND_AUDIO = 0x00, 0x10


async def read_frame(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    head = await reader.readexactly(3)
    kind, size = head[0], struct.unpack(">H", head[1:])[0]
    return kind, await reader.readexactly(size) if size else b""


def pack(kind: int, payload: bytes) -> bytes:
    return bytes([kind]) + struct.pack(">H", len(payload)) + payload


class Call:
    def __init__(self, ast_reader, ast_writer, bot_reader, bot_writer) -> None:
        self.ast_reader, self.ast_writer = ast_reader, ast_writer
        self.bot_reader, self.bot_writer = bot_reader, bot_writer
        self.queue: collections.deque[tuple[int, bytes]] = collections.deque()
        self.wake = asyncio.Event()
        self.done = False
        self.late = 0

    async def upstream(self) -> None:  # Asterisk -> робот, без задержки
        try:
            while True:
                kind, payload = await read_frame(self.ast_reader)
                self.bot_writer.write(pack(kind, payload))
                await self.bot_writer.drain()
                if kind == KIND_HANGUP:
                    return
        except (asyncio.IncompleteReadError, ConnectionError):
            pass

    async def downstream(self) -> None:  # робот -> очередь
        try:
            while True:
                frame = await read_frame(self.bot_reader)
                self.queue.append(frame)
                self.wake.set()
                if frame[0] == KIND_HANGUP:
                    return
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self.done = True
            self.wake.set()

    async def player(self) -> None:  # очередь -> Asterisk, кадр раз в 20 мс
        playing = False
        start, n = 0.0, 0
        while True:
            if not self.queue:
                if self.done:
                    return
                playing = False
                self.wake.clear()
                await self.wake.wait()
                continue
            if not playing:
                # новая фраза: ждём запас кадров (или конец передачи), потом играем ровно
                deadline = time.monotonic() + PREBUFFER * FRAME_S
                while len(self.queue) < PREBUFFER and not self.done and time.monotonic() < deadline:
                    await asyncio.sleep(0.005)
                playing, start, n = True, time.monotonic(), 0
            kind, payload = self.queue.popleft()
            self.ast_writer.write(pack(kind, payload))
            await self.ast_writer.drain()
            if kind == KIND_HANGUP:
                return
            if kind != KIND_AUDIO:
                continue
            n += 1
            delay = start + n * FRAME_S - time.monotonic()
            if delay < -0.1:
                self.late += 1
                start, n = time.monotonic(), 0
                delay = 0
            await asyncio.sleep(max(delay, 0))

    async def run(self) -> None:
        tasks = [asyncio.create_task(c) for c in (self.upstream(), self.downstream(), self.player())]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        # кто-то положил трубку: дадим доиграть очереди и закроем обе стороны
        try:
            await asyncio.wait_for(tasks[2], 5)
        except asyncio.TimeoutError:
            pass
        for t in tasks:
            t.cancel()
        for w in (self.ast_writer, self.bot_writer):
            w.close()
        if self.late:
            log.warning("кадры робота отставали %d раз", self.late)


async def on_asterisk(ast_reader, ast_writer) -> None:
    try:
        bot_reader, bot_writer = await asyncio.open_connection(*ROBOT)
    except OSError as e:
        log.warning("робот недоступен через туннель: %s", e)
        ast_writer.close()
        return
    log.info("звонок")
    await Call(ast_reader, ast_writer, bot_reader, bot_writer).run()
    log.info("звонок закончен")


async def main() -> None:
    server = await asyncio.start_server(on_asterisk, *LISTEN)
    log.info("жду Asterisk на %s:%d, робот на %s:%d", *LISTEN, *ROBOT)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(main())
