#!/usr/bin/env bash
# Asterisk на российском VPS: Манго не пускает SIP-регистрацию с зарубежных IP (403 Forbidden).
# Робот (распознавание, модель, голос) остаётся на этом сервере, на VPS только Asterisk.
# Связь через постоянный SSH-туннель отсюда на VPS (юнит callbot-voice-relay):
#   здесь 127.0.0.1:8088 -> ARI Asterisk на VPS (робот заказывает звонки как раньше),
#   на VPS 127.0.0.1:9092 -> AudioSocket робота здесь (звук звонка идёт сюда).
# Код робота не меняется.
#
# Запуск на этом сервере: bash scripts/voice_relay_setup.sh IP_VPS
# Один раз спросит пароль root от VPS (чтобы положить туда ключ), дальше всё само.
# Откат: systemctl disable --now callbot-voice-relay; на VPS: systemctl disable --now asterisk
set -euo pipefail
trap 'echo "ошибка в строке $LINENO: $BASH_COMMAND"' ERR
VPS=${1:?"укажи IP VPS: bash voice_relay_setup.sh 1.2.3.4"}
ENV=/opt/callbot-astra/.env
HERE=$(cd "$(dirname "$0")" && pwd)
KEY=/root/.ssh/voice_relay
SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=accept-new -o BatchMode=yes "root@$VPS")

[ -f "$KEY" ] || ssh-keygen -q -t ed25519 -N "" -C voice-relay -f "$KEY"
if ! "${SSH[@]}" true 2>/dev/null; then
  echo "сейчас спросит пароль root от VPS (один раз):"
  ssh-copy-id -i "$KEY.pub" -o StrictHostKeyChecking=accept-new "root@$VPS" >/dev/null
fi
"${SSH[@]}" true

# здешний Asterisk больше не нужен и занимает порт 8088
systemctl disable --now asterisk 2>/dev/null || true

# на VPS нужны только логин/пароль Манго и пароль ARI (тот же, что знает робот здесь)
grep -q '^VOICE_ARI_PASSWORD=' "$ENV" || echo "VOICE_ARI_PASSWORD=$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)" >> "$ENV"
grep -E '^(MANGO_SIP_LOGIN|MANGO_SIP_DOMAIN|MANGO_SIP_PASSWORD|VOICE_ARI_PASSWORD)=' "$ENV" \
  | "${SSH[@]}" 'umask 077; mkdir -p /etc/callbot-voice; cat > /etc/callbot-voice/env'
"${SSH[@]}" 'VOICE_ENV=/etc/callbot-voice/env bash -s' < "$HERE/voice_sip_setup.sh"

cat > /etc/systemd/system/callbot-voice-relay.service <<UNIT
[Unit]
Description=voicebot: SSH tunnel to Asterisk on $VPS
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/ssh -N -i $KEY -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -L 127.0.0.1:8088:127.0.0.1:8088 -R 127.0.0.1:9092:127.0.0.1:9092 root@$VPS
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable -q callbot-voice-relay
systemctl restart callbot-voice-relay
sleep 3
echo "туннель: $(systemctl is-active callbot-voice-relay)"
curl -s -o /dev/null -w "ARI через туннель: HTTP %{http_code}\n" http://127.0.0.1:8088/ari/asterisk/info || true
