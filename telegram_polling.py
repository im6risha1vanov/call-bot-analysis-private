"""Telegram transport for the two long-polling bots."""

import logging
import socket

from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.methods import GetUpdates


class IPv4PollingSession(AiohttpSession):
    def __init__(self) -> None:
        super().__init__()
        # aiogram builds the aiohttp connector from these options on demand.
        self._connector_init['family'] = socket.AF_INET
        self._first_poll_succeeded = False

    async def make_request(self, bot, method, timeout=None):
        result = await super().make_request(bot, method, timeout=timeout)
        if isinstance(method, GetUpdates) and not self._first_poll_succeeded:
            self._first_poll_succeeded = True
            logging.getLogger(__name__).info('Telegram getUpdates succeeded over IPv4')
        return result
