"""Результаты первичного прозвона Averon для страницы сотрудников."""
from datetime import datetime, timedelta, timezone

from review import kitchen
from tools import home_client_id

DAYS = 14
ROBOT_EXTENSION = '13'
OPERATOR_EXTENSIONS = ('14', '15')
OUTCOMES = ('lead', 'no_lead', 'no_answer', 'error', 'unresolved')


async def summary(pool, viewer, people) -> dict | None:
    if viewer.role != 'director' or viewer.test or viewer.support:
        return None
    client_id = viewer.company
    if client_id is None or client_id != await home_client_id(pool):
        return None
    extensions = {p['ext'] for p in people if p['role'] == 'manager'}
    operators = [ext for ext in OPERATOR_EXTENSIONS if ext in extensions]
    if not operators and ROBOT_EXTENSION not in extensions:
        return None

    since = datetime.now(timezone.utc) - timedelta(days=DAYS)
    counts = {ext: dict.fromkeys(OUTCOMES, 0) for ext in operators}
    if operators:
        for row in await pool.fetch(
            """SELECT extension, outcome, count(*) AS total FROM (
                 SELECT c.extension, CASE
                   WHEN a.status='interested' THEN 'lead'
                   WHEN a.status IN ('callback','not_relevant') THEN 'no_lead'
                   WHEN a.status='ndz' THEN 'no_answer'
                   WHEN c.status IN ('failed','no_recording') OR EXISTS (
                     SELECT 1 FROM tasks t WHERE t.client_id=c.client_id
                       AND t.type='analyze_call' AND t.dedup_key=c.external_id
                       AND t.input->>'call_id'=c.id::text AND t.status='failed'
                   ) THEN 'error'
                   WHEN c.status<>'skipped' THEN 'unresolved'
                 END AS outcome
                 FROM calls c LEFT JOIN lead_attempts a ON a.call_id=c.id AND a.client_id=c.client_id
                 WHERE c.client_id=$1 AND c.extension=ANY($2::text[]) AND c.direction=2 AND c.call_started_at >= $3
               ) outcomes WHERE outcome IS NOT NULL GROUP BY extension, outcome""",
            client_id, operators, since,
        ):
            counts[row['extension']][row['outcome']] = row['total']

    items = [{'ext': ext, 'mode': 'script' if kitchen.handles(ext) else 'unconfigured', **counts[ext]}
             for ext in operators]
    if ROBOT_EXTENSION in extensions:
        robot_counts = dict.fromkeys(OUTCOMES, 0)
        for row in await pool.fetch(
            """SELECT outcome, count(*) AS total FROM (
                 SELECT CASE
                   WHEN outcome='error' OR nullif(data->>'error','') IS NOT NULL THEN 'error'
                   WHEN outcome='interested' THEN 'lead'
                   WHEN outcome IN ('callback','not_relevant','do_not_call') THEN 'no_lead'
                   WHEN outcome='ndz' THEN 'no_answer'
                   ELSE 'unresolved'
                 END AS outcome FROM robot_calls WHERE owner='' AND at >= $1
               ) outcomes GROUP BY outcome""", since,
        ):
            robot_counts[row['outcome']] = row['total']
        items.append({'ext': ROBOT_EXTENSION, 'mode': 'robot', **robot_counts})
    return {'days': DAYS, 'items': items}
