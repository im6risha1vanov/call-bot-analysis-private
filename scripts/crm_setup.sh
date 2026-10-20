#!/usr/bin/env bash
# CRM (crm/): один сайт на этом сервере за Caddy с HTTPS, входы Директор / Менеджер / Клиент.
# Входы: личные ссылки /p/... из раздела «Доступы» и тестовая https://DOMAIN/t/<CRM_TEST_KEY> с выбором роли.
# Запуск на сервере (root), из /opt/callbot-astra:
#   bash scripts/crm_setup.sh check            — что занимает порты 80/443, есть ли Caddy, файрвол
#   bash scripts/crm_setup.sh install [DOMAIN] — миграции CRM, сервис callbot-crm, Caddy, CRM_URL, тестовая ссылка
#                                                DOMAIN по умолчанию <ip>.sslip.io (бесплатный адрес под IP сервера)
#   bash scripts/crm_setup.sh status           — сервис, адрес, проверка HTTPS и тестовая ссылка
#   bash scripts/crm_setup.sh update           — после git pull: миграции CRM, перезапуск сайта и бота
#   bash scripts/crm_setup.sh new-test-link    — новая тестовая ссылка (старая перестаёт работать)
#   bash scripts/crm_setup.sh test-link-off    — выключить тестовую ссылку (личные ссылки работают)
#   bash scripts/crm_setup.sh public-on        — открыть сайт всем только на просмотр: без ссылки сразу выбор входа
#   bash scripts/crm_setup.sh public-off       — закрыть: снова только личные и тестовая ссылки, входы для просмотра гаснут
#   bash scripts/crm_setup.sh director-link [TG] — личная ссылка директора (по умолчанию владельца из бота): с ней можно всё менять
#   bash scripts/crm_setup.sh dadata-key       — ключ DaData для поиска реквизитов по ИНН в «Договорах» (спросит ключ, на экран не выводит)
#   bash scripts/crm_setup.sh rollback         — выключить сайт и убрать CRM_URL (таблицы CRM остаются, они ничему не мешают)
set -euo pipefail
APP=/opt/callbot-astra
cd "$APP"
DATABASE_URL=$(grep -m1 '^DATABASE_URL=' .env | cut -d= -f2- | sed "s/^[\"']//; s/[\"']\$//")
BOT_SERVICES="callbot-astra callbot-astra-worker callbot-astra-queue-runner"
PORT=8095
UNIT=/etc/systemd/system/callbot-crm.service
STATE=/root/backups/crm

default_domain() {
  local ip
  ip=$(curl -4 -s --max-time 5 https://ifconfig.me || hostname -I | awk '{print $1}')
  echo "${ip//./-}.sslip.io"
}

new_key() { head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24; }
test_link() {
  local url key
  url=$(grep -m1 '^CRM_URL=' .env | cut -d= -f2- || true)
  key=$(grep -m1 '^CRM_TEST_KEY=' .env | cut -d= -f2- || true)
  [ -n "$url" ] && [ -n "$key" ] && echo "test link: $url/t/$key"
}

# Все миграции CRM; каждая только добавляет и повторный запуск ничего не ломает.
migrate() { for f in migrations/014_crm.sql migrations/015_crm_roles.sql migrations/016_crm_access.sql migrations/017_crm_reports.sql migrations/018_crm_questions.sql migrations/019_crm_staff.sql migrations/020_robot.sql migrations/021_manager_memory.sql migrations/022_presentations.sql migrations/023_tg_chats.sql migrations/024_deals.sql migrations/025_contracts.sql; do
  psql "$DATABASE_URL" -q -v ON_ERROR_STOP=1 -f "$f"; done; }

listeners() { ss -ltnpH "( sport = :80 or sport = :443 )" || true; }

case "${1:-}" in
check)
  echo "== ports 80/443"; listeners
  echo "== caddy: $(command -v caddy || echo 'not installed')"
  [ -f /etc/caddy/Caddyfile ] && { echo "== /etc/caddy/Caddyfile"; cat /etc/caddy/Caddyfile; }
  echo "== ufw"; (command -v ufw >/dev/null && ufw status) || echo 'no ufw'
  echo "== default domain: $(default_domain)"
  ;;
install)
  DOMAIN="${2:-$(default_domain)}"
  if listeners | grep -v caddy | grep -q .; then
    echo "ports 80/443 are used by another program, stop here:"; listeners; exit 1
  fi
  mkdir -p "$STATE"; chmod 700 "$STATE"
  git rev-parse HEAD > "$STATE/commit.before"
  cp .env "$STATE/env.before.$(date +%s)"
  migrate
  grep -q '^CRM_TEST_KEY=' .env || echo "CRM_TEST_KEY=$(new_key)" >> .env
  cat > "$UNIT" <<UNIT
[Unit]
Description=Averon CRM for managers (crm/web.py)
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=simple
User=callbot-astra
WorkingDirectory=$APP
EnvironmentFile=$APP/.env
Environment=CRM_HOST=127.0.0.1 CRM_PORT=$PORT
ExecStart=$APP/.venv/bin/python -m crm.web
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
  systemctl enable --now callbot-crm
  if ! command -v caddy >/dev/null; then
    apt-get update -q
    apt-get install -y -q caddy || {
      apt-get install -y -q debian-keyring debian-archive-keyring apt-transport-https curl gpg
      curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
      curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
      apt-get update -q && apt-get install -y -q caddy; }
  fi
  # Caddy уже может обслуживать другие сайты (callbot-analysis.ru): блок CRM дописываем, чужое не трогаем.
  CF=/etc/caddy/Caddyfile
  BLOCK="# callbot-crm: CRM for managers (scripts/crm_setup.sh)
$DOMAIN {
	encode gzip
	reverse_proxy 127.0.0.1:$PORT
	header Strict-Transport-Security \"max-age=31536000\"
	log {
		output discard
	}
}"
  if [ ! -s "$CF" ] || grep -q '/usr/share/caddy' "$CF"; then
    printf '%s\n' "$BLOCK" > "$CF"
  elif ! grep -q "^$DOMAIN {" "$CF"; then
    cp "$CF" "$STATE/Caddyfile.before.$(date +%s)"
    printf '\n%s\n' "$BLOCK" >> "$CF"
  fi
  if ! caddy validate --config "$CF" --adapter caddyfile >/dev/null 2>&1; then
    echo "Caddyfile is invalid after the change, restoring it:"; caddy validate --config "$CF" --adapter caddyfile 2>&1 | tail -3
    LAST=$(ls -t "$STATE"/Caddyfile.before.* 2>/dev/null | head -1); [ -n "$LAST" ] && cp "$LAST" "$CF"; exit 1
  fi
  if command -v ufw >/dev/null && ufw status | grep -q 'Status: active'; then ufw allow 80/tcp; ufw allow 443/tcp; fi
  systemctl enable caddy >/dev/null 2>&1 || true
  systemctl reload caddy 2>/dev/null || systemctl restart caddy
  if grep -q '^CRM_URL=' .env; then sed -i "s|^CRM_URL=.*|CRM_URL=https://$DOMAIN|" .env; else echo "CRM_URL=https://$DOMAIN" >> .env; fi
  # Бот тоже перезапускаем: в этой ветке у него исправлены кнопки перезвонов.
  systemctl restart $BOT_SERVICES callbot-crm
  sleep 8
  git log -1 --oneline
  systemctl is-active $BOT_SERVICES callbot-crm caddy
  echo "local: $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$PORT/api/me)  (401 is correct)"
  echo "https: $(curl -s -o /dev/null -w '%{http_code}' --max-time 30 https://$DOMAIN/api/me)  (401 is correct; certificate may take a minute)"
  echo "CRM_URL=https://$DOMAIN"
  test_link
  ;;
update)
  migrate
  # Бот кладёт отчёты в CRM и берёт из неё список копий разборов, поэтому перезапускаем и его.
  systemctl restart $BOT_SERVICES callbot-crm
  sleep 5
  git log -1 --oneline
  systemctl is-active $BOT_SERVICES callbot-crm
  echo "local: $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$PORT/api/me)  (401 is correct)"
  ;;
test-link-off)
  sed -i '/^CRM_TEST_KEY=/d' .env
  systemctl restart callbot-crm
  echo 'test link is off; personal links keep working'
  ;;
new-test-link)
  sed -i '/^CRM_TEST_KEY=/d' .env
  echo "CRM_TEST_KEY=$(new_key)" >> .env
  systemctl restart callbot-crm
  test_link
  ;;
public-on)
  grep -q '^CRM_PUBLIC=1' .env || { sed -i '/^CRM_PUBLIC=/d' .env; echo 'CRM_PUBLIC=1' >> .env; }
  systemctl restart callbot-crm
  sleep 3
  echo "public view-only: $(grep -m1 '^CRM_URL=' .env | cut -d= -f2-)/"
  ;;
public-off)
  sed -i '/^CRM_PUBLIC=/d' .env
  # входы для просмотра (тестовая ссылка и открытый сайт) гаснут сразу, личные остаются
  psql "$DATABASE_URL" -q -c "DELETE FROM crm_sessions WHERE is_test"
  systemctl restart callbot-crm
  echo 'public access is off; personal links keep working'
  ;;
director-link)
  # Ссылка привязана к Telegram руководителя: под ней работают промты, а если его отключат от бота, она погаснет.
  TG="${2:-$(psql "$DATABASE_URL" -At -c "SELECT telegram_user_id FROM employees WHERE role='owner' AND telegram_user_id IS NOT NULL ORDER BY client_id LIMIT 1")}"
  [ -n "$TG" ] || { echo 'no owner linked to the bot; pass the Telegram id: director-link 123456'; exit 1; }
  URL=$(grep -m1 '^CRM_URL=' .env | cut -d= -f2-)
  DATABASE_URL="$DATABASE_URL" TG="$TG" .venv/bin/python - <<'PY' | sed "s|^|$URL/p/|"
import asyncio, os
import asyncpg
from crm import auth

async def main():
    pool = await asyncpg.create_pool(os.environ['DATABASE_URL'], min_size=1, max_size=1)
    tg = int(os.environ['TG'])
    client_id = await pool.fetchval("SELECT client_id FROM employees WHERE telegram_user_id=$1 AND role IN ('owner','head') LIMIT 1", tg)
    if client_id is None:
        raise SystemExit('this Telegram id is not an owner or head in the bot')
    print(await auth.invite_create(pool, client_id, 'director', tg=tg))
    await pool.close()

asyncio.run(main())
PY
  echo 'open this link once on each device; it stays signed in for 90 days. Revoke it in «Доступы».'
  ;;
dadata-key)
  read -rsp 'DaData API key (dadata.ru, API-ключи): ' KEY; echo
  KEY=$(printf '%s' "$KEY" | tr -dc 'A-Za-z0-9')
  [ -n "$KEY" ] || { echo 'empty key, nothing changed'; exit 1; }
  CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 -H "Authorization: Token $KEY" -H 'Content-Type: application/json' \
    -d '{"query":"7707083893","count":1}' https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party || true)
  [ "$CODE" = 200 ] || { echo "DaData answered $CODE to this key, nothing changed"; exit 1; }
  sed -i '/^DADATA_KEY=/d' .env
  echo "DADATA_KEY=$KEY" >> .env
  systemctl restart callbot-crm
  echo 'DaData key saved: search by INN works in «Договоры»'
  ;;
status)
  git log -1 --oneline
  grep -q '^CRM_PUBLIC=1' .env && echo 'public view-only: ON' || echo 'public view-only: off'
  grep '^CRM_URL=' .env || echo 'CRM_URL not set'
  systemctl is-active callbot-crm caddy || true
  URL=$(grep -m1 '^CRM_URL=' .env | cut -d= -f2- || true)
  [ -n "$URL" ] && echo "https: $(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$URL/api/me") (401 is correct)"
  psql "$DATABASE_URL" -At -c "SELECT 'sessions '||count(*) FROM crm_sessions WHERE expires_at>now()" \
                          -c "SELECT 'leads '||count(*) FROM crm_leads" \
                          -c "SELECT 'dials today '||count(*) FROM crm_dials WHERE created_at>current_date"
  test_link || echo 'test link not set'
  journalctl -u callbot-crm -n 5 --no-pager -o cat || true
  ;;
rollback)
  systemctl disable --now callbot-crm || true
  sed -i '/^CRM_URL=/d' .env
  echo "CRM is off. Caddy keeps running but has nothing behind it; stop it with: systemctl disable --now caddy"
  ;;
*)
  sed -n '2,16p' "$0"
  exit 1
  ;;
esac
