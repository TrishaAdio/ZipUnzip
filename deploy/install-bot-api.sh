#!/usr/bin/env bash
#
# Build tdlib/telegram-bot-api from source and run it as a systemd service.
#
# This is what raises the ceiling from 20 MB download / 50 MB upload to 2000 MB
# in both directions. Nothing else in this project needs root.
#
#   sudo TELEGRAM_API_ID=123456 TELEGRAM_API_HASH=abc... ./deploy/install-bot-api.sh
#
# Expect the compile to take 10-25 minutes and ~2 GB of RAM. On a 1 GB box add
# swap first or the linker will be killed.

set -Eeuo pipefail

API_ID="${TELEGRAM_API_ID:-}"
API_HASH="${TELEGRAM_API_HASH:-}"
HTTP_PORT="${TELEGRAM_HTTP_PORT:-8081}"
BIND_IP="${TELEGRAM_HTTP_IP:-127.0.0.1}"
DATA_DIR="${TELEGRAM_DATA_DIR:-/var/lib/telegram-bot-api}"
SRC_DIR="${TELEGRAM_SRC_DIR:-/usr/local/src/telegram-bot-api}"
PREFIX="${TELEGRAM_PREFIX:-/usr/local}"
SERVICE_USER="telegram-bot-api"
JOBS="${JOBS:-$(nproc)}"

bold=$(tput bold 2>/dev/null || true)
dim=$(tput dim 2>/dev/null || true)
red=$(tput setaf 1 2>/dev/null || true)
green=$(tput setaf 2 2>/dev/null || true)
cyan=$(tput setaf 6 2>/dev/null || true)
off=$(tput sgr0 2>/dev/null || true)

step() { echo -e "\n${bold}${cyan}── $* ${off}"; }
info() { echo -e "${dim}   $*${off}"; }
ok()   { echo -e "   ${green}done${off} $*"; }
die()  { echo -e "${red}${bold}error${off} $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run this with sudo"
[[ -n $API_ID && -n $API_HASH ]] || die "TELEGRAM_API_ID and TELEGRAM_API_HASH are required (my.telegram.org)"

step "installing build dependencies"
if command -v apt-get >/dev/null; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq make git zlib1g-dev libssl-dev gperf cmake g++ ca-certificates
elif command -v dnf >/dev/null; then
  dnf install -y -q make git zlib-devel openssl-devel gperf cmake gcc-c++ ca-certificates
elif command -v pacman >/dev/null; then
  pacman -Sy --noconfirm --needed make git zlib openssl gperf cmake gcc
else
  die "unsupported package manager; install make git zlib openssl gperf cmake g++ by hand"
fi
ok "toolchain ready"

step "fetching sources"
if [[ -d $SRC_DIR/.git ]]; then
  git -C "$SRC_DIR" fetch --depth 1 origin master
  git -C "$SRC_DIR" reset --hard origin/master
  git -C "$SRC_DIR" submodule update --init --recursive --depth 1
else
  mkdir -p "$(dirname "$SRC_DIR")"
  git clone --recursive --depth 1 https://github.com/tdlib/telegram-bot-api.git "$SRC_DIR"
fi
ok "$(git -C "$SRC_DIR" rev-parse --short HEAD)"

step "compiling (this is the slow part, ${JOBS} jobs)"
mkdir -p "$SRC_DIR/build"
cd "$SRC_DIR/build"
cmake -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX:PATH="$PREFIX" ..
cmake --build . --target install -j "$JOBS"
ok "$("$PREFIX/bin/telegram-bot-api" --version 2>/dev/null | head -1 || echo installed)"

step "creating service account and data dir"
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi
mkdir -p "$DATA_DIR" "$DATA_DIR/tmp"
chown -R "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR"
# The bot process must be able to read what the server writes.
chmod 755 "$DATA_DIR"
ok "$DATA_DIR"

step "writing /etc/telegram-bot-api.env"
umask 077
cat >/etc/telegram-bot-api.env <<EOF
TELEGRAM_API_ID=$API_ID
TELEGRAM_API_HASH=$API_HASH
EOF
chmod 600 /etc/telegram-bot-api.env
ok "credentials stored (mode 600)"

step "installing systemd unit"
cat >/etc/systemd/system/telegram-bot-api.service <<EOF
[Unit]
Description=Telegram Bot API server (local mode)
Documentation=https://github.com/tdlib/telegram-bot-api
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
EnvironmentFile=/etc/telegram-bot-api.env
ExecStart=$PREFIX/bin/telegram-bot-api \\
  --local \\
  --http-ip-address=$BIND_IP \\
  --http-port=$HTTP_PORT \\
  --dir=$DATA_DIR \\
  --temp-dir=$DATA_DIR/tmp \\
  --max-connections=100 \\
  --verbosity=1
Restart=always
RestartSec=5
LimitNOFILE=65536
# Large archives live under DATA_DIR, everything else stays read-only.
ProtectSystem=full
PrivateTmp=false
NoNewPrivileges=true
ReadWritePaths=$DATA_DIR

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now telegram-bot-api.service
sleep 3
systemctl is-active --quiet telegram-bot-api.service || {
  journalctl -u telegram-bot-api.service -n 30 --no-pager
  die "service failed to start"
}
ok "listening on $BIND_IP:$HTTP_PORT"

cat <<EOF

${bold}${green}Local Bot API server is up.${off}

  ${bold}1.${off} Detach the token from api.telegram.org once, or the local server
     will refuse it:

       curl -s "https://api.telegram.org/bot\$BOT_TOKEN/logOut"

  ${bold}2.${off} Point the bot at it (.env):

       API_BASE_URL=http://$BIND_IP:$HTTP_PORT
       API_DIR=$DATA_DIR
       MAX_ARCHIVE_MB=2000

  ${bold}3.${off} The bot process needs read access to $DATA_DIR. Either run it as
     $SERVICE_USER, or add its user to that group:

       usermod -aG $SERVICE_USER <bot-user>

  ${dim}logs:${off}    journalctl -u telegram-bot-api -f
  ${dim}restart:${off} systemctl restart telegram-bot-api
EOF
