#!/usr/bin/env bash
# CRM менеджеров (crm/): сайт на этом сервере за Caddy с HTTPS, вход по /crm из бота.
# Запуск на сервере (root), из /opt/callbot-astra:
#   bash scripts/crm_setup.sh check            — что занимает порты 80/443, есть ли Caddy, файрвол
#   bash scripts/crm_setup.sh install [DOMAIN] — миграция 014, сервис callbot-crm, Caddy, CRM_URL, перезапуск бота
#                                                DOMAIN по умолчанию <ip>.sslip.io (бесплатный адрес под IP сервера)
#   bash scripts/crm_setup.sh status           — сервис, адрес и проверка HTTPS
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
  psql "$DATABASE_URL" -q -v ON_ERROR_STOP=1 -f migrations/014_crm.sql
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
  if [ -s /etc/caddy/Caddyfile ] && ! grep -q '/usr/share/caddy\|callbot-crm' /etc/caddy/Caddyfile; then
    echo "/etc/caddy/Caddyfile already has other sites, not touching it. Add this block by hand:"
    echo "$DOMAIN { reverse_proxy 127.0.0.1:$PORT }"; exit 1
  fi
  cat > /etc/caddy/Caddyfile <<CADDY
# callbot-crm: сайт CRM менеджеров (scripts/crm_setup.sh)
$DOMAIN {
	encode gzip
	reverse_proxy 127.0.0.1:$PORT
	header Strict-Transport-Security "max-age=31536000"
	log {
		output discard
	}
}
CADDY
  if command -v ufw >/dev/null && ufw status | grep -q 'Status: active'; then ufw allow 80/tcp; ufw allow 443/tcp; fi
  systemctl enable caddy >/dev/null 2>&1 || true
  systemctl reload caddy 2>/dev/null || systemctl restart caddy
  if grep -q '^CRM_URL=' .env; then sed -i "s|^CRM_URL=.*|CRM_URL=https://$DOMAIN|" .env; else echo "CRM_URL=https://$DOMAIN" >> .env; fi
  systemctl restart $BOT_SERVICES callbot-crm
  sleep 8
  git log -1 --oneline
  systemctl is-active $BOT_SERVICES callbot-crm caddy
  echo "local: $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$PORT/api/me)  (401 is correct)"
  echo "https: $(curl -s -o /dev/null -w '%{http_code}' --max-time 30 https://$DOMAIN/api/me)  (401 is correct; certificate may take a minute)"
  echo "CRM_URL=https://$DOMAIN"
  ;;
status)
  git log -1 --oneline
  grep '^CRM_URL=' .env || echo 'CRM_URL not set'
  systemctl is-active callbot-crm caddy || true
  URL=$(grep -m1 '^CRM_URL=' .env | cut -d= -f2- || true)
  [ -n "$URL" ] && echo "https: $(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$URL/api/me") (401 is correct)"
  psql "$DATABASE_URL" -At -c "SELECT 'sessions '||count(*) FROM crm_sessions WHERE expires_at>now()" \
                          -c "SELECT 'leads '||count(*) FROM crm_leads" \
                          -c "SELECT 'dials today '||count(*) FROM crm_dials WHERE created_at>current_date"
  journalctl -u callbot-crm -n 5 --no-pager -o cat || true
  ;;
rollback)
  systemctl disable --now callbot-crm || true
  sed -i '/^CRM_URL=/d' .env
  systemctl restart $BOT_SERVICES
  sleep 5
  systemctl is-active $BOT_SERVICES
  echo "CRM is off. Caddy keeps running but has nothing behind it; stop it with: systemctl disable --now caddy"
  ;;
*)
  sed -n '2,8p' "$0"
  exit 1
  ;;
esac
