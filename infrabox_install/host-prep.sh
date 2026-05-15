#!/usr/bin/env bash
# host-prep.sh — підготовка хоста до розгортання Infrabox
#
# Запуск (від цільового користувача з правами sudo):
#   bash host-prep.sh [--deploy-dir /path] [--hostname myhost.local] [--skip-ssl]
#
# Що робить:
#   1. Перевірка системних вимог
#   2. Встановлення Docker (якщо відсутній)
#   3. Додавання користувача до групи docker
#   4. Створення директорії розгортання
#   5. Створення Docker-мережі infrabox-net
#   6. Генерація SSL-сертифікатів (mkcert або self-signed)
#   7. Налаштування logrotate
#
# Ідемпотентний: повторний запуск не ламає вже налаштоване.

set -euo pipefail

# ── Кольори ───────────────────────────────────────────────────────────────────
R='\033[0;31m'; G='\033[0;32m'; Y='\033[1;33m'; B='\033[0;34m'; N='\033[0m'
ok()   { echo -e "${G}✓${N} $*"; }
info() { echo -e "${B}→${N} $*"; }
warn() { echo -e "${Y}!${N} $*"; }
fail() { echo -e "${R}✗ ПОМИЛКА:${N} $*" >&2; exit 1; }
step() { echo -e "\n${B}── $* ──${N}"; }

# ── Параметри (дефолти, можна перевизначити аргументами) ─────────────────────
DEPLOY_DIR="${INFRABOX_DEPLOY_DIR:-/home/${USER}/infrabox}"
HOSTNAME_ARG=""
SKIP_SSL=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --deploy-dir)  DEPLOY_DIR="$2";   shift 2 ;;
        --hostname)    HOSTNAME_ARG="$2"; shift 2 ;;
        --skip-ssl)    SKIP_SSL=1;        shift   ;;
        *) fail "Невідомий аргумент: $1" ;;
    esac
done

DEPLOY_USER="${USER}"
SSL_DIR="${DEPLOY_DIR}/ui/frontend/ssl"

# ── Утиліти ───────────────────────────────────────────────────────────────────
_cmd()   { command -v "$1" &>/dev/null; }
_try()   { "$@" 2>/dev/null || true; }
_has_sudo() { sudo -n true 2>/dev/null; }

require_sudo() {
    if ! _has_sudo; then
        warn "Потрібен sudo. Введіть пароль якщо буде запит."
        sudo true || fail "Немає доступу до sudo"
    fi
}

# ══════════════════════════════════════════════════════════════════════════════
step "Крок 1 — Перевірка системних вимог"

# Платформа
PLATFORM=$(uname -s)
[ "$PLATFORM" != "Linux" ] && fail "host-prep.sh підтримує тільки Linux (поточна: $PLATFORM)"

# Архітектура
ARCH=$(uname -m)
case "$ARCH" in
    x86_64)  DOCKER_ARCH="amd64"  ;;
    armv7l)  DOCKER_ARCH="armhf"  ;;
    aarch64) DOCKER_ARCH="arm64"  ;;
    *) fail "Непідтримувана архітектура: $ARCH" ;;
esac
ok "Архітектура: $ARCH (Docker: $DOCKER_ARCH)"

# ОС
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS_ID="${ID:-unknown}"
    OS_VERSION="${VERSION_CODENAME:-${VERSION_ID:-unknown}}"
else
    fail "Не вдалося визначити ОС (/etc/os-release відсутній)"
fi

case "$OS_ID" in
    debian|ubuntu|armbian) ok "ОС: ${PRETTY_NAME:-$OS_ID $OS_VERSION}" ;;
    *) warn "ОС '$OS_ID' не тестувалась — продовжую на власний ризик" ;;
esac

# RAM
RAM_MB=$(awk '/MemTotal/{printf "%d", $2/1024}' /proc/meminfo)
[ "$RAM_MB" -lt 256 ] && fail "Недостатньо RAM: ${RAM_MB}MB (мінімум 256MB)"
[ "$RAM_MB" -lt 512 ] && warn "RAM ${RAM_MB}MB — arch-підсистема може не запуститись"
ok "RAM: ${RAM_MB}MB"

# Місце на диску
DISK_FREE_GB=$(df -BG "${DEPLOY_DIR%/*/*}" 2>/dev/null || df -BG / | awk 'NR==2{v=$4; gsub("G","",v); print v+0}')
DISK_FREE_GB=$(df -BG / | awk 'NR==2{v=$4; gsub("G","",v); print v+0}')
[ "$DISK_FREE_GB" -lt 3 ] && fail "Недостатньо місця: ${DISK_FREE_GB}GB (мінімум 3GB)"
[ "$DISK_FREE_GB" -lt 6 ] && warn "Місця мало: ${DISK_FREE_GB}GB (рекомендовано 6GB+)"
ok "Диск вільно: ${DISK_FREE_GB}GB"

# ══════════════════════════════════════════════════════════════════════════════
step "Крок 2 — Docker"

if _cmd docker && docker --version &>/dev/null; then
    DOCKER_VER=$(docker --version | grep -oE '[0-9]+\.[0-9]+' | head -1)
    ok "Docker вже встановлений: $DOCKER_VER"
else
    info "Встановлення Docker..."
    require_sudo

    # Видалити старі версії якщо є
    for pkg in docker.io docker-doc docker-compose podman-docker containerd runc; do
        sudo apt-get remove -y "$pkg" &>/dev/null || true
    done

    # Залежності
    sudo apt-get update -qq
    sudo apt-get install -y -qq ca-certificates curl gnupg lsb-release

    # GPG ключ Docker
    sudo install -m 0755 -d /usr/share/keyrings
    curl -fsSL "https://download.docker.com/linux/${OS_ID}/gpg" \
        | sudo gpg --dearmor -o /usr/share/keyrings/docker.gpg
    sudo chmod a+r /usr/share/keyrings/docker.gpg

    # Репозиторій
    echo "deb [arch=${DOCKER_ARCH} signed-by=/usr/share/keyrings/docker.gpg] \
https://download.docker.com/linux/${OS_ID} ${OS_VERSION} stable" \
        | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

    # Встановлення
    sudo apt-get update -qq
    sudo apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin

    sudo systemctl enable docker
    sudo systemctl start docker

    ok "Docker встановлено: $(docker --version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')"
fi

# docker compose plugin
if docker compose version &>/dev/null 2>&1; then
    ok "Docker Compose: $(docker compose version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')"
else
    fail "Docker Compose plugin відсутній. Встановіть вручну: apt install docker-compose-plugin"
fi

# ══════════════════════════════════════════════════════════════════════════════
step "Крок 3 — Користувач і групи"

if id -nG "$DEPLOY_USER" | grep -qw docker; then
    ok "Користувач '$DEPLOY_USER' вже в групі docker"
else
    require_sudo
    sudo usermod -aG docker "$DEPLOY_USER"
    ok "Додано '$DEPLOY_USER' до групи docker"
    warn "Потрібно повторно залогінитись або виконати: newgrp docker"
    NEED_RELOGIN=1
fi

# ══════════════════════════════════════════════════════════════════════════════
step "Крок 4 — Директорії"

# Основна директорія розгортання
if [ -d "$DEPLOY_DIR" ]; then
    ok "deploy_dir вже існує: $DEPLOY_DIR"
else
    mkdir -p "$DEPLOY_DIR"
    ok "Створено: $DEPLOY_DIR"
fi

# Спільний лог-каталог (монтується у всі підсистеми)
LOG_DIR="${DEPLOY_DIR}/log"
if [ -d "$LOG_DIR" ]; then
    ok "log/ вже існує"
else
    mkdir -p "$LOG_DIR"
    ok "Створено: $LOG_DIR"
fi

# SSL-директорія (потрібна до генерації сертифікатів)
mkdir -p "$SSL_DIR"
ok "ssl/ готова: $SSL_DIR"

# ══════════════════════════════════════════════════════════════════════════════
step "Крок 5 — Docker-мережа infrabox-net"

if docker network inspect infrabox-net &>/dev/null 2>&1; then
    ok "Мережа infrabox-net вже існує"
else
    docker network create infrabox-net
    ok "Мережу infrabox-net створено"
fi

# ══════════════════════════════════════════════════════════════════════════════
step "Крок 6 — SSL-сертифікати"

if [ "$SKIP_SSL" = "1" ]; then
    warn "SSL пропущено (--skip-ssl)"
elif [ -f "${SSL_DIR}/infrabox.crt" ] && [ -f "${SSL_DIR}/infrabox.key" ]; then
    ok "SSL-сертифікати вже є"
    # Перевірка терміну дії
    if _cmd openssl; then
        EXPIRY=$(openssl x509 -enddate -noout -in "${SSL_DIR}/infrabox.crt" 2>/dev/null | cut -d= -f2)
        ok "Термін дії сертифіката: $EXPIRY"
    fi
else
    # Визначити hostname для сертифіката
    if [ -n "$HOSTNAME_ARG" ]; then
        CERT_HOST="$HOSTNAME_ARG"
    else
        CERT_HOST=$(hostname)
        CERT_FQDN=$(hostname -f 2>/dev/null || hostname)
    fi
    CERT_IP=$(ip -4 addr show | grep -v '127\.' | awk '/inet /{print $2}' | cut -d/ -f1 | head -1)

    if _cmd mkcert; then
        info "Генерація сертифіката через mkcert..."
        mkcert -install &>/dev/null || true
        mkcert \
            -cert-file "${SSL_DIR}/infrabox.crt" \
            -key-file  "${SSL_DIR}/infrabox.key" \
            "${CERT_HOST}" "${CERT_FQDN:-$CERT_HOST}" localhost 127.0.0.1 "${CERT_IP}"
        # Зберегти root CA для пристроїв у мережі
        CA_ROOT=$(mkcert -CAROOT)
        [ -f "${CA_ROOT}/rootCA.pem" ] && cp "${CA_ROOT}/rootCA.pem" "${SSL_DIR}/rootCA.pem"
        ok "mkcert сертифікат згенеровано для: ${CERT_HOST} ${CERT_IP}"
    else
        warn "mkcert не знайдено — генерую self-signed (не довіряється браузерами без імпорту)"
        require_sudo
        sudo apt-get install -y -qq openssl &>/dev/null

        SUBJ="/C=UA/ST=Local/L=Local/O=Infrabox/CN=${CERT_HOST}"
        SAN="subjectAltName=DNS:${CERT_HOST},DNS:${CERT_FQDN:-$CERT_HOST},DNS:localhost,IP:127.0.0.1,IP:${CERT_IP}"

        openssl req -x509 -nodes -days 3650 \
            -newkey rsa:2048 \
            -keyout "${SSL_DIR}/infrabox.key" \
            -out    "${SSL_DIR}/infrabox.crt" \
            -subj   "$SUBJ" \
            -addext "$SAN" 2>/dev/null

        ok "Self-signed сертифікат згенеровано (діє 10 років)"
        warn "Для мобільних пристроїв імпортуйте ${SSL_DIR}/infrabox.crt вручну"
    fi

    chmod 600 "${SSL_DIR}/infrabox.key"
    chmod 644 "${SSL_DIR}/infrabox.crt"
fi

# ══════════════════════════════════════════════════════════════════════════════
step "Крок 7 — Logrotate"

LOGROTATE_CONF="/etc/logrotate.d/infrabox"

if [ -f "$LOGROTATE_CONF" ]; then
    ok "Logrotate вже налаштований"
else
    require_sudo
    sudo tee "$LOGROTATE_CONF" > /dev/null <<EOF
${LOG_DIR}/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
EOF
    ok "Logrotate налаштований: $LOGROTATE_CONF"
fi

# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${G}══════════════════════════════════════════${N}"
echo -e "${G}  Підготовку хоста завершено успішно ✓${N}"
echo -e "${G}══════════════════════════════════════════${N}"
echo ""
echo "  deploy_dir:  $DEPLOY_DIR"
echo "  Docker:      $(docker --version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')"
echo "  Мережа:      infrabox-net ✓"
echo "  SSL:         ${SSL_DIR}/infrabox.crt"
echo ""

if [ "${NEED_RELOGIN:-0}" = "1" ]; then
    warn "Виконайте 'newgrp docker' або перелогіньтесь щоб docker-група набрала чинності"
fi

echo "Наступний крок: bash deploy.sh"
