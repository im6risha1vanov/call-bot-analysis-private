#!/usr/bin/env bash
# Звонки робота для разбора автоответчиков: сводка по итогам и расшифровки звонков без дозвона.
# Номера не печатаем. Запуск из /opt/callbot-astra: bash scripts/robot_ndz.sh [дней] [сколько расшифровок]
set -euo pipefail
[ -f .env ] || cd "$(dirname "$0")/.."  # через git show ... | bash запускаем из /opt/callbot-astra
DATABASE_URL=$(grep -m1 '^DATABASE_URL=' .env | cut -d= -f2- | sed "s/^[\"']//; s/[\"']\$//")
DAYS=${1:-14}
LIMIT=${2:-40}
echo "== итоги за $DAYS дн. =="
psql "$DATABASE_URL" -P pager=off -c "
SELECT outcome, count(*) AS calls, round(avg(duration_s)) AS avg_s, sum(llm_tokens) AS tokens,
       round(avg(llm_tokens)) AS avg_tokens, sum(tts_chars) AS chars
FROM robot_calls WHERE at > now() - interval '$DAYS days'
GROUP BY outcome ORDER BY calls DESC"
echo "== звонки ndz: длительность, токены, разговор =="
psql "$DATABASE_URL" -P pager=off -At -c "
SELECT to_char(at AT TIME ZONE 'Europe/Moscow', 'DD.MM HH24:MI') || ' | ' || duration_s || ' с | ' || llm_tokens || ' ток. | '
       || comment || E'\n' ||
       coalesce((SELECT string_agg(CASE WHEN t->>0 = 'robot' THEN 'Р: ' ELSE 'К: ' END || (t->>1), E'\n')
                 FROM jsonb_array_elements(data->'transcript') t), '') || E'\n---'
FROM robot_calls WHERE outcome = 'ndz' AND at > now() - interval '$DAYS days'
ORDER BY llm_tokens DESC, at DESC LIMIT $LIMIT"
