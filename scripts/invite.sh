#!/usr/bin/env bash
# Ссылка-приглашение в бота (invites.py): человек открывает её, жмёт «Старт» и подключается сам.
# Запуск на сервере (root) из /opt/callbot-astra, имя латиницей (из Telegram подставится настоящее):
#   bash scripts/invite.sh head Ivan                          — новый руководитель (extension head:ivan)
#   bash scripts/invite.sh head Ivan --replace head:marozeto  — вместо прежнего: тот отвязывается, его место
#                                                               в REVIEW_COPY_TO занимает новый
#   bash scripts/invite.sh manager 15 Petr                    — менеджер с добавочным 15 в Mango
#   ... --username nick                                       — если известен ник Telegram: хватит простого /start
# Ссылка одноразовая, действует 7 дней. Повторный запуск для того же человека выдаёт новую ссылку.
set -euo pipefail
cd "${APP_DIR:-/opt/callbot-astra}"
env_value() { grep -m1 "^$1=" .env | cut -d= -f2- | sed "s/^[\"']//; s/[\"']\$//"; }
DATABASE_URL=$(env_value DATABASE_URL)
psql "$DATABASE_URL" -q -v ON_ERROR_STOP=1 -f migrations/013_employee_invites.sql

ROLE="${1:?usage: invite.sh head NAME [--replace EXT] | manager EXT NAME}"
case "$ROLE" in
  head) NAME="${2:?name}"; EXT="head:$(echo "$NAME" | tr 'A-Z' 'a-z' | tr -cd 'a-z0-9_.-')"; shift 2 ;;
  manager) EXT="${2:?extension}"; NAME="${3:?name}"; shift 3 ;;
  *) echo "role must be head or manager"; exit 1 ;;
esac
REPLACE=""
USERNAME=""
while [ $# -gt 0 ]; do
  case "$1" in
    --replace) REPLACE="${2:?old extension}"; shift 2 ;;
    --username) USERNAME=$(echo "${2:?telegram username}" | tr 'A-Z' 'a-z' | tr -cd 'a-z0-9_'); shift 2 ;;
    *) echo "unknown option $1"; exit 1 ;;
  esac
done

q() { psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -Atq "$@"; }
CLIENT=$(q -c "SELECT id FROM clients ORDER BY id LIMIT 1")
q -v ext="$EXT" -v client="$CLIENT" -v role_x="$ROLE" -v name_x="$NAME" <<'SQL'
INSERT INTO employees(client_id, extension, role, full_name) VALUES (:client, :'ext', :'role_x', :'name_x')
ON CONFLICT DO NOTHING;
SQL
if [ -n "$(q -v ext="$EXT" -v client="$CLIENT" <<'SQL'
SELECT telegram_user_id FROM employees WHERE client_id=:client AND extension=:'ext' AND telegram_user_id IS NOT NULL;
SQL
)" ]; then
  echo "$EXT already connected to Telegram; nothing to do"; exit 1
fi
CODE=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' | cut -c1-20)
q -v code="$CODE" -v ext="$EXT" -v client="$CLIENT" <<'SQL'
INSERT INTO employee_invites(code, client_id, extension) VALUES (:'code', :client, :'ext');
SQL
if [ -n "$USERNAME" ]; then
  # Ник Telegram: тогда хватит и простого /start (demo_bot._bind_by_username), ссылка — запасной путь.
  q -v ext="$EXT" -v client="$CLIENT" -v uname="$USERNAME" <<'SQL'
UPDATE employees SET telegram_username=:'uname' WHERE client_id=:client AND extension=:'ext';
SQL
  echo "username: @$USERNAME (plain /start is enough)"
fi

if [ -n "$REPLACE" ]; then
  mkdir -p /root/backups
  cp .env "/root/backups/env.before_invite.$(date +%s)"
  q -v old="$REPLACE" -v client="$CLIENT" <<'SQL'
-- Строка остаётся (на неё могут ссылаться), но привязаться к ней уже нельзя и сообщения ей не уходят.
UPDATE employees SET telegram_user_id=NULL, telegram_username=NULL WHERE client_id=:client AND extension=:'old';
SQL
  .venv/bin/python - "$REPLACE" "$EXT" <<'PY'
import re, sys
old, new = sys.argv[1], sys.argv[2]
text = open('.env', encoding='utf-8').read()
def swap(m):
    items = [t.strip() for t in m[1].split(',') if t.strip()]
    items = [new if t == old else t for t in items]
    return 'REVIEW_COPY_TO=' + ','.join(dict.fromkeys(items))
text = re.sub(r'^REVIEW_COPY_TO=(.*)$', swap, text, flags=re.M)
open('.env', 'w', encoding='utf-8').write(text)
PY
  echo "replaced: $REPLACE -> $EXT"
fi

BOT=$(curl -s "https://api.telegram.org/bot$(env_value BOT_TOKEN)/getMe" | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["result"]["username"])')
systemctl restart callbot-astra callbot-astra-worker callbot-astra-queue-runner
sleep 5
systemctl is-active callbot-astra callbot-astra-worker callbot-astra-queue-runner
grep '^REVIEW_COPY_TO=' .env || true
echo
echo "INVITE LINK for $EXT (one-time, 7 days):"
echo "https://t.me/$BOT?start=inv_$CODE"
