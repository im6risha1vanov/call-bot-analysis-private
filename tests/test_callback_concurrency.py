"""Concurrent callback changes cannot silently overwrite a successful change."""

import asyncio
from contextlib import suppress
from datetime import timedelta
import unittest

from crm import store
from tests.test_crm import DBCase, DSN, HEAD, MANAGER


@unittest.skipUnless(DSN, 'local audit database required')
class CallbackConcurrencyTests(DBCase):
    async def race(self, manager_change, director_change):
        pool = self.pool
        task = await pool.fetchval(
            """INSERT INTO callback_tasks(client_id,extension,client_number,kind,due_date,raw,source)
               VALUES(1,'101','79990009001','callback',$1,'synthetic','crm') RETURNING id""",
            self.today())
        async with pool.acquire() as hold, pool.acquire() as monitor:
            async with hold.transaction():
                await hold.fetchval('SELECT id FROM callback_tasks WHERE id=$1 FOR UPDATE', task)
                changes = asyncio.gather(
                    store.change_task(pool, MANAGER, 1, '101', task, *manager_change),
                    store.change_task(pool, HEAD, 1, '101', task, *director_change),
                    return_exceptions=True,
                )

                async def both_updates_waiting():
                    while await monitor.fetchval(
                        """SELECT count(*) FROM pg_stat_activity WHERE datname=current_database()
                           AND wait_event_type='Lock' AND query LIKE 'UPDATE callback_tasks SET %'"""
                    ) != 2:
                        if changes.done():
                            self.fail('Both requests must reach the update with the original row version')

                try:
                    await asyncio.wait_for(both_updates_waiting(), timeout=5)
                except BaseException:
                    changes.cancel()
                    with suppress(asyncio.CancelledError):
                        await changes
                    raise

            results = await changes
            row = await monitor.fetchrow('SELECT status, due_date FROM callback_tasks WHERE id=$1', task)

        self.assertEqual(results.count(None), 1, results)
        failure = next(result for result in results if result is not None)
        self.assertIsInstance(failure, ValueError)
        self.assertIn('закрыт или изменён', str(failure))
        return row, results.index(None)

    async def test_competing_close_preserves_the_successful_action(self):
        row, winner = await self.race(('done',), ('cancelled',))
        self.assertEqual(row['status'], ('done', 'cancelled')[winner])

    async def test_competing_reschedules_preserve_the_successful_date(self):
        dates = self.today() + timedelta(days=1), self.today() + timedelta(days=2)
        row, winner = await self.race(('move', dates[0]), ('move', dates[1]))
        self.assertEqual(row['status'], 'open')
        self.assertEqual(row['due_date'], dates[winner])
