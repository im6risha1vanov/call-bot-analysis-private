"""Offline checks. Never load .env or connect to production providers/DB."""
import os
from pathlib import Path
import sys
import signal
import unittest
from unittest.mock import patch, AsyncMock
from contextlib import ExitStack

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.dont_write_bytecode=True

async def inline_thread(func,*args,**kwargs):
    # Pure aiogram callback filters are synchronous. Restricted Python 3.14
    # thread executors hang in this environment; this substitutes execution
    # scheduling only, retaining actual filters, callbacks and permission code.
    return func(*args,**kwargs)

def run():
    from aiogram.client.session.aiohttp import AiohttpSession
    import asyncpg,httpx
    fake={'CVC_API_KEY':'synthetic-test-key','BOT_TOKEN':'123456:synthetic-test-token',
          'TRAIN_BOT_TOKEN':'123456:synthetic-test-token'}
    if hasattr(signal,'SIGALRM'):signal.alarm(120)
    with ExitStack() as s:
        s.enter_context(patch.dict(os.environ,fake,clear=True))
        s.enter_context(patch('dotenv.load_dotenv',return_value=False))
        s.enter_context(patch('asyncio.to_thread',side_effect=inline_thread))
        for target,name in [(AiohttpSession,'make_request'),(asyncpg,'create_pool'),(asyncpg,'connect'),(httpx.AsyncClient,'send')]:
            s.enter_context(patch.object(target,name,AsyncMock(side_effect=AssertionError('Unexpected external operation'))))
        names=['tests.test_legacy_method_rollback','tests.test_legacy_mode','tests.test_extended_review',
               'tests.test_unified.RendererTests','tests.test_report_reanalysis.CombinedStyleTests',
               'tests.test_methodology','tests.test_automatic.AutomaticMenuTests','tests.test_deploy_legacy_preview']
        suite=unittest.defaultTestLoader.loadTestsFromNames(names)
        result=unittest.TextTestRunner(verbosity=1).run(suite)
        if hasattr(signal,'SIGALRM'):signal.alarm(0)
        return result.wasSuccessful()

if __name__=='__main__':raise SystemExit(0 if run() else 1)
