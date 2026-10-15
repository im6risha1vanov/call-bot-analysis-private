#!/usr/bin/env bash
# Звонки голосового робота не должны попадать в разборы менеджеров. Аккаунт «Робот» в Mango занял
# добавочный ушедшего менеджера (13), и бот разбирал бы его звонки как звонки менеджера.
# Запуск: ... | bash -s check [ДОБАВОЧНЫЙ]   — только посмотреть
#         ... | bash -s apply [ДОБАВОЧНЫЙ]   — убрать добавочный из разборов
# Откат: psql "$DATABASE_URL" -f /root/review-v2-rollback/sales_extensions.before.sql
set -euo pipefail
ACTION=${1:-check}
EXT=${2:-13}
cd /opt/callbot-astra
DB=$(grep -m1 '^DATABASE_URL=' .env | cut -d= -f2- | sed "s/^[\"']//; s/[\"']\$//")
q() { psql "$DB" -q -v ON_ERROR_STOP=1 -P pager=off "$@"; }

echo "== добавочные, которые разбирает бот"
q -c "SELECT id, name, sales_extensions FROM clients"
echo "== сотрудник на добавочном $EXT"
q -c "SELECT extension, full_name, role, synced_at FROM employees WHERE extension='$EXT'"
echo "== звонки с добавочного $EXT за 3 дня"
q -c "SELECT status, count(*), min(call_started_at), max(call_started_at) FROM calls
      WHERE extension='$EXT' AND call_started_at > now() - interval '3 days' GROUP BY status"

if [ "$ACTION" = apply ]; then
  mkdir -p /root/review-v2-rollback
  q -At -c "SELECT format('UPDATE clients SET sales_extensions=%L WHERE id=%s;', sales_extensions, id) FROM clients" \
    > /root/review-v2-rollback/sales_extensions.before.sql
  q -c "UPDATE clients SET sales_extensions = array_remove(sales_extensions, '$EXT')"
  q -c "UPDATE calls SET status='skipped' WHERE extension='$EXT' AND status IN ('new','awaiting_record')"
  echo "== после"
  q -c "SELECT id, name, sales_extensions FROM clients"
fi
