#!/usr/bin/env bash
# Только чтение: какие поля Mango отдаёт по звонкам (из calls.raw_summary за 7 дней).
# Номера клиентов маскируются. Запуск на сервере из /opt/callbot-astra:
#   bash scripts/mango_fields.sh
set -euo pipefail
cd "${APP_DIR:-/opt/callbot-astra}"
env_value() { grep -m1 "^$1=" .env | cut -d= -f2- | sed "s/^[\"']//; s/[\"']\$//"; }
DATABASE_URL=$(env_value DATABASE_URL)
q() { psql "$DATABASE_URL" -X -q -v ON_ERROR_STOP=1 -P pager=off -c "$1"; }
RECENT="call_started_at > now() - interval '7 days' AND raw_summary IS NOT NULL"
echo "== calls by direction (1 in, 2 out, 3 internal)"
q "SELECT direction, count(*), count(*) FILTER (WHERE duration_seconds > 0) AS talked FROM calls WHERE $RECENT GROUP BY 1 ORDER BY 1"
echo "== call fields"
q "SELECT k, count(*) FROM calls, jsonb_object_keys(raw_summary::jsonb) k WHERE $RECENT GROUP BY 1 ORDER BY 1"
echo "== leg fields (context_calls)"
q "SELECT k, count(*) FROM calls, jsonb_array_elements(CASE WHEN jsonb_typeof(raw_summary::jsonb->'context_calls')='array' THEN raw_summary::jsonb->'context_calls' ELSE '[]'::jsonb END) leg, jsonb_object_keys(leg) k WHERE $RECENT GROUP BY 1 ORDER BY 1"
mask="regexp_replace(raw_summary::text, '([78])[0-9]{6}([0-9]{4})', '\\1XXXXXX\\2', 'g')"
for kind in "direction=1 AND duration_seconds>0" "direction=1 AND coalesce(duration_seconds,0)=0" "direction=2 AND duration_seconds>0"; do
  echo "== example: $kind"
  q "SELECT $mask FROM calls WHERE $RECENT AND $kind ORDER BY id DESC LIMIT 1"
done
