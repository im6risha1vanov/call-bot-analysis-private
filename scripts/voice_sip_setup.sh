#!/usr/bin/env bash
# Ставит Asterisk и подключает его к Mango как сотрудника «Робот» (SIP-учётка).
# Звук звонков Asterisk отдаёт роботу (voice/sip.py) по AudioSocket на 127.0.0.1:9092,
# звонки заказываются через ARI на 127.0.0.1:8088. Наружу открыт только SIP 5060/udp и RTP.
#
# Перед запуском в /opt/callbot-astra/.env должны быть MANGO_SIP_LOGIN, MANGO_SIP_DOMAIN, MANGO_SIP_PASSWORD.
# Запуск: bash scripts/voice_sip_setup.sh   (повторный запуск безопасен, перезаписывает только наши файлы)
# Откат: systemctl disable --now asterisk; старые конфиги лежат в /root/asterisk-backup-*.
set -euo pipefail
trap 'echo "ошибка в строке $LINENO: $BASH_COMMAND"' ERR
ENV=${VOICE_ENV:-/opt/callbot-astra/.env}
# пустая строка, если ключа нет (иначе grep без совпадений молча роняет скрипт из-за set -e)
getenv() { { grep -m1 "^$1=" "$ENV" || true; } | cut -d= -f2- | sed "s/^[\"']//; s/[\"']\$//"; }
LOGIN=$(getenv MANGO_SIP_LOGIN); DOMAIN=$(getenv MANGO_SIP_DOMAIN); PASS=$(getenv MANGO_SIP_PASSWORD)
LOGIN=${LOGIN#sip:}; LOGIN=${LOGIN%%@*}   # логин могли вписать целым адресом robot@vpbx....mangosip.ru
[ -n "$LOGIN" ] && [ -n "$DOMAIN" ] && [ -n "$PASS" ] || { echo "нет MANGO_SIP_LOGIN / MANGO_SIP_DOMAIN / MANGO_SIP_PASSWORD в $ENV"; exit 1; }

if ! command -v asterisk >/dev/null; then
  apt-get update -qq
  # в Ubuntu asterisk лежит в universe, на минимальных образах VPS он бывает выключен
  if ! apt-cache policy asterisk | grep -q 'Candidate: [0-9]' && grep -q '^ID=ubuntu' /etc/os-release; then
    apt-get install -y -qq software-properties-common >/dev/null
    add-apt-repository -y universe >/dev/null
    apt-get update -qq
  fi
  if ! apt-cache policy asterisk | grep -q 'Candidate: [0-9]'; then
    echo "в репозитории этой системы нет пакета asterisk, пришли вывод: cat /etc/os-release"; exit 1
  fi
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq asterisk >/dev/null
fi

BACKUP=/root/asterisk-backup-$(date +%Y%m%d-%H%M%S)
cp -a /etc/asterisk "$BACKUP"

ARI_PASS=$(getenv VOICE_ARI_PASSWORD)
if [ -z "$ARI_PASS" ]; then
  ARI_PASS=$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)
  echo "VOICE_ARI_PASSWORD=$ARI_PASS" >> "$ENV"
fi

cat > /etc/asterisk/pjsip.conf <<CONF
; voicebot: Mango Office, сотрудник «Робот»
[transport-udp]
type=transport
protocol=udp
bind=0.0.0.0:5060

[mango-auth]
type=auth
auth_type=userpass
username=$LOGIN
password=$PASS

[mango-reg]
type=registration
outbound_auth=mango-auth
server_uri=sip:$DOMAIN
client_uri=sip:$LOGIN@$DOMAIN
contact_user=$LOGIN
retry_interval=60
forbidden_retry_interval=300
expiration=300

[mango]
type=aor
contact=sip:$DOMAIN

[mango]
type=endpoint
context=voicebot-in
disallow=all
allow=alaw,ulaw
outbound_auth=mango-auth
aors=mango
from_user=$LOGIN
from_domain=$DOMAIN
direct_media=no
rtp_symmetric=yes
force_rport=yes
rewrite_contact=yes

[mango]
type=identify
endpoint=mango
match=$DOMAIN
CONF

cat > /etc/asterisk/extensions.conf <<'CONF'
; voicebot: ответивший клиент уходит к роботу по AudioSocket
[voicebot]
exten => s,1,NoOp(voicebot ${VOICE_ID})
 same => n,AudioSocket(${VOICE_ID},127.0.0.1:9092)
 same => n,Hangup()

; входящие на робота не принимаем
[voicebot-in]
exten => _X.,1,Hangup()
exten => s,1,Hangup()
CONF

cat > /etc/asterisk/http.conf <<'CONF'
[general]
enabled=yes
bindaddr=127.0.0.1
bindport=8088
CONF

cat > /etc/asterisk/ari.conf <<CONF
[general]
enabled=yes

[voicebot]
type=user
read_only=no
password=$ARI_PASS
CONF
chown -R asterisk: /etc/asterisk 2>/dev/null || true
chmod 640 /etc/asterisk/pjsip.conf /etc/asterisk/ari.conf

if command -v ufw >/dev/null && ufw status | grep -q 'Status: active'; then
  ufw allow 5060/udp >/dev/null
  ufw allow 10000:20000/udp >/dev/null
fi

systemctl enable -q asterisk
systemctl restart asterisk
sleep 8
asterisk -rx "module show like audiosocket" | tail -2
asterisk -rx "pjsip show registrations"
echo "бэкап старых конфигов: $BACKUP"
