#!/usr/bin/env bash
# Режим промта (review/prompts.py, review/prompt_ui.py): точка восстановления, включение, откат.
# Запуск на сервере (root):
#   bash prompt_mode.sh backup               — точка восстановления: код, .env, база -> /root/backups/prompt-mode-<время>
#   bash prompt_mode.sh install ID1,ID2      — миграция 012, исходные промты первой версией, PROMPT_EDITORS, перезапуск
#   bash prompt_mode.sh rollback DIR         — вернуть код и .env из точки DIR (таблицы промтов остаются, они ничему не мешают)
#   bash prompt_mode.sh status               — что сейчас действует
# Только ASCII в аргументах: кириллица в команде через PowerShell портится.
set -euo pipefail
APP=/opt/callbot-astra
cd "$APP"
DATABASE_URL=$(grep -m1 '^DATABASE_URL=' .env | cut -d= -f2- | sed "s/^[\"']//; s/[\"']\$//")
SERVICES="callbot-astra callbot-astra-worker callbot-astra-queue-runner"

case "${1:-}" in
backup)
  DIR="/root/backups/prompt-mode-$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$DIR"
  git rev-parse HEAD > "$DIR/commit"
  git status --short > "$DIR/git-status.txt" || true
  cp .env "$DIR/env"
  tar czf "$DIR/code.tar.gz" --exclude=./.venv --exclude=./__pycache__ --exclude='*.pyc' .
  pg_dump -Fc "$DATABASE_URL" -f "$DIR/db.dump"
  chmod -R go-rwx "$DIR"
  echo "restore point: $DIR (commit $(cat "$DIR/commit"))"
  du -sh "$DIR"/* | sed 's/^/  /'
  ;;
install)
  EDITORS="${2:?usage: install ID1,ID2}"
  psql "$DATABASE_URL" -q -v ON_ERROR_STOP=1 -f migrations/012_prompt_versions.sql
  cp .env "/root/review-v2-rollback/env.before_prompt_mode.$(date +%s)" 2>/dev/null || true
  if grep -q '^PROMPT_EDITORS=' .env; then sed -i "s|^PROMPT_EDITORS=.*|PROMPT_EDITORS=$EDITORS|" .env; else echo "PROMPT_EDITORS=$EDITORS" >> .env; fi
  runuser -u callbot-astra -- .venv/bin/python -m review.prompts seed
  systemctl restart $SERVICES
  sleep 5
  git log -1 --oneline
  grep '^PROMPT_EDITORS=' .env
  systemctl is-active $SERVICES
  ;;
rollback)
  DIR="${2:?usage: rollback /root/backups/prompt-mode-...}"
  git reset --keep "$(cat "$DIR/commit")"
  cp "$DIR/env" .env
  systemctl restart $SERVICES
  sleep 5
  git log -1 --oneline
  systemctl is-active $SERVICES
  ;;
status)
  git log -1 --oneline
  grep '^PROMPT_EDITORS=' .env || echo 'PROMPT_EDITORS not set'
  psql "$DATABASE_URL" -c "SELECT a.kind, a.owner_tg, a.version_id, v.note, a.updated_at FROM prompt_active a JOIN prompt_versions v ON v.id=a.version_id ORDER BY 1,2"
  ;;
*)
  sed -n '2,8p' "$0"
  exit 1
  ;;
esac
