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

export DEBIAN_FRONTEND=noninteractive

build_from_source() {  # в Ubuntu 24.04 и Debian 12 пакета asterisk нет, собираем LTS-версию сами (~30 мин на 1 ядре)
  echo "пакета asterisk нет, собираю из исходников, это займёт около получаса..."
  apt-get install -y -qq build-essential curl pkg-config libedit-dev libjansson-dev libxml2-dev uuid-dev \
    libsqlite3-dev libssl-dev >/dev/null
  rm -rf /usr/src/asterisk-build && mkdir -p /usr/src/asterisk-build && cd /usr/src/asterisk-build
  curl -fsSL -o src.tar.gz https://downloads.asterisk.org/pub/telephony/asterisk/asterisk-22-current.tar.gz \
    || curl -fsSL -o src.tar.gz https://github.com/asterisk/asterisk/archive/refs/tags/22.11.0.tar.gz
  tar xzf src.tar.gz --strip-components=1
  ./configure --quiet >/dev/null
  make -s menuselect.makeopts >/dev/null
  menuselect/menuselect --disable BUILD_NATIVE --disable-category MENUSELECT_CORE_SOUNDS \
    --disable-category MENUSELECT_MOH --disable-category MENUSELECT_EXTRA_SOUNDS menuselect.makeopts >/dev/null  # звуки роботу не нужны
  make -s -j"$(nproc)" >/dev/null 2>&1
  make -s install >/dev/null 2>&1
  [ -d /etc/asterisk ] && [ -n "$(ls -A /etc/asterisk)" ] || make -s samples >/dev/null 2>&1
  ldconfig
  id asterisk >/dev/null 2>&1 || useradd -r -d /var/lib/asterisk -s /usr/sbin/nologin asterisk
  chown -R asterisk: /var/lib/asterisk /var/log/asterisk /var/spool/asterisk /var/run/asterisk 2>/dev/null || true
  cat > /etc/systemd/system/asterisk.service <<'UNIT'
[Unit]
Description=Asterisk PBX
After=network-online.target
Wants=network-online.target

[Service]
User=asterisk
Group=asterisk
RuntimeDirectory=asterisk
ExecStart=/usr/sbin/asterisk -f -C /etc/asterisk/asterisk.conf
ExecReload=/usr/sbin/asterisk -rx "core reload"
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
  cd /
}

if ! command -v asterisk >/dev/null; then
  apt-get update -qq
  # в Ubuntu asterisk лежит в universe, на минимальных образах VPS он бывает выключен
  if ! apt-cache policy asterisk | grep -q 'Candidate: [0-9]' && grep -q '^ID=ubuntu' /etc/os-release; then
    apt-get install -y -qq software-properties-common >/dev/null
    add-apt-repository -y universe >/dev/null
    apt-get update -qq
  fi
  if apt-cache policy asterisk | grep -q 'Candidate: [0-9]'; then
    apt-get install -y -qq asterisk >/dev/null
  else
    build_from_source
  fi
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
