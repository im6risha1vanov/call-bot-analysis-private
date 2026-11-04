"""Run learning and related pure regressions without production providers or DB.

A socket-free JS/DOM harness exercises the actual engine and practice controller.
It does not replace Chromium desktop/mobile/CSP validation. No dependency install.
"""
from contextlib import ExitStack
import os
from pathlib import Path
import signal
import shutil
import subprocess
import json
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.dont_write_bytecode = True


def main():
    node = os.environ.get("CRM_LEARNING_NODE") or shutil.which("node")
    import asyncpg
    import httpx
    import aiohttp
    import requests
    from aiogram.client.session.aiohttp import AiohttpSession
    fake = {'BOT_TOKEN':'123456:synthetic-test-token', 'TRAIN_BOT_TOKEN':'123456:synthetic-test-token',
            'CVC_API_KEY':'synthetic-test-key', 'ANTHROPIC_API_KEY':'synthetic-test-key',
            'ENCRYPTION_KEY':'q7m8b4gN0bO2PZqvG6b0yC3rA1D5pYc8tW2XkL9uQeE='}
    if hasattr(signal, 'SIGALRM'): signal.alarm(120)
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, fake, clear=True))
        stack.enter_context(patch('dotenv.load_dotenv', return_value=False))
        for obj, name in [(asyncpg,'create_pool'),(asyncpg,'connect'),(httpx.AsyncClient,'send'),
                          (aiohttp.ClientSession,'_request'),(AiohttpSession,'make_request')]:
            stack.enter_context(patch.object(obj,name,AsyncMock(side_effect=AssertionError('Unexpected external/DB operation'))))
        stack.enter_context(patch.object(requests.Session,'request',side_effect=AssertionError('Unexpected provider request')))
        suite = unittest.defaultTestLoader.loadTestsFromNames([
            'tests.test_crm_learning', 'tests.test_crm.PureTests', 'tests.test_gpt.PureTests',
            'tests.test_automations.PureTests', 'tests.test_automations.AgentTests'])
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    if hasattr(signal, 'SIGALRM'): signal.alarm(0)
    if result.skipped:
        print('Acceptance checks are incomplete: skipped tests must be resolved.', file=sys.stderr)
        return 2
    if not result.wasSuccessful():
        return 1
    if not node:
        print('Parent/iframe checks require Node. Set CRM_LEARNING_NODE to its executable.', file=sys.stderr)
        return 2
    check = subprocess.run([node, 'tests/js/learning_parent_test.cjs'],
                           cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=60)
    if check.returncode:
        print(check.stdout, file=sys.stderr); print(check.stderr, file=sys.stderr)
        return 1
    cases = json.loads(check.stdout)['cases']
    print(f'Parent/iframe DOM adapter: {len(cases)} cases passed (not a browser).')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
