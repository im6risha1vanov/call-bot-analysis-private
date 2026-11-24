"""Агент РОПа у стороннего провайдера: вызов инструмента приходит со stop_reason="end_turn"."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault('BOT_TOKEN', '1:test')
os.environ.setdefault('CVC_API_KEY', 'test')
os.environ.setdefault('ANTHROPIC_API_KEY', 'test')

import rop_agent

USAGE = SimpleNamespace(input_tokens=10, output_tokens=10)


class EndTurnToolUseTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_runs_even_when_stop_reason_is_end_turn(self):
        tool_call = SimpleNamespace(type='tool_use', id='call_1', name='count_calls', input={'day': '2026-10-17'})
        first = SimpleNamespace(content=[tool_call], stop_reason='end_turn', usage=USAGE)
        final = SimpleNamespace(content=[SimpleNamespace(type='text', text='Вчера 12 звонков.')],
                                stop_reason='end_turn', usage=USAGE)
        create = AsyncMock(side_effect=[first, final])
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value={'timezone': 'Europe/Moscow'})
        actor = SimpleNamespace(client_id=1)
        with patch.object(rop_agent.claude.messages, 'create', create), \
                patch.object(rop_agent, '_over_daily_limit', AsyncMock(return_value=False)), \
                patch.object(rop_agent, '_add_spend', AsyncMock()), \
                patch.object(rop_agent, '_dispatch_tool', AsyncMock(return_value={'calls': 12})) as dispatch:
            text = await rop_agent.answer(pool, actor, 'Сколько звонков вчера?')
        self.assertEqual(text, 'Вчера 12 звонков.')
        dispatch.assert_awaited_once()
        self.assertEqual(create.await_count, 2)
        self.assertEqual(create.await_args.kwargs['messages'][2]['content'][0]['tool_use_id'], 'call_1')


if __name__ == '__main__':
    unittest.main()
