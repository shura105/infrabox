#!/usr/bin/env bash
# 2_host-prep.sh — підготовка хоста до розгортання Infrabox підсистем.
#
#   bash 2_host-prep.sh          (без ключів — скрипт питає все в процесі)
#
# Модель: скрипт ТРИМАЄ вимоги до хоста, ПЕРЕВІРЯЄ наявність кожної, за технічної
# можливості ВСТАНОВЛЮЄ, а якщо встановити не може — додає ЗАДАЧУ АДМІНУ з описом
# (а не падає). Параметри (deploy_dir, SSL) питає інтерактивно з поясненнями.
# Наприкінці — або «готово», або список задач для ручного виконання.
#
# Двоплатформний: Linux (apt-встановлення) і macOS (Docker Desktop — делегування).
# Ідемпотентний. Linux-специфічне (docker-група, tmpfs, logrotate) на macOS пропускається.

set -euo pipefail

# ── Кольори ───────────────────────────────────────────────────────────────────
R='\033[0;31m'; G='\033[0;32m'; Y='\033[1;33m'; B='\033[0;34m'; C='\033[0;36m'; N='\033[0m'
ok()   { echo -e "${G}✓${N} $*"; }
info() { echo -e "${B}→${N} $*"; }
warn() { echo -e "${Y}!${N} $*"; }
err()  { echo -e "${R}✗${N} $*"; }
step() { echo -e "\n${C}── $* ──${N}"; }

# ── Задачі адміну (накопичувач) ───────────────────────────────────────────────
TODO_N=0
TODO_TEXT=""
add_todo() {
    # add_todo <короткий заголовок> <що зробити (можна багаторядково)>
    TODO_N=$((TODO_N + 1))
    TODO_TEXT="${TODO_TEXT}
  ${R}[${TODO_N}]${N} ${B}$1${N}
$2
"
}

# ── Інтерактивні запити (скрипт питає сам, без ключів запуску) ────────────────
ask() {
    # ask "Питання (з поясненням)" "дефолт" VAR
    local prompt="$1" default="$2" varname="$3" result=""
    if [ -n "$default" ]; then printf "  ${B}%s${N} [${default}]: " "$prompt"
    else printf "  ${B}%s${N}: " "$prompt"; fi
    read -r result || true
    printf -v "$varname" '%s' "${result:-$default}"
}
ask_yn() {
    local prompt="$1" default="${2:-y}" varname="$3" opts ans=""
    [ "$default" = "y" ] && opts="Y/n" || opts="y/N"
    printf "  ${B}%s${N} [%s]: " "$prompt" "$opts"
    read -r ans || true
    ans="${ans:-$default}"
    case "$(echo "$ans" | tr '[:upper:]' '[:lower:]')" in
        y|yes|т|так) printf -v "$varname" '%s' "1" ;;
        *)            printf -v "$varname" '%s' "0" ;;
    esac
}

DEPLOY_USER="${USER:-$(id -un)}"
# DEPLOY_DIR / SSL_DIR / SKIP_SSL / HOSTNAME_ARG — питаються в процесі нижче

# ── Утиліти ───────────────────────────────────────────────────────────────────
_cmd()      { command -v "$1" >/dev/null 2>&1; }
_try()      { "$@" 2>/dev/null || true; }
_has_sudo() { sudo -n true 2>/dev/null; }
require_sudo() {
    _has_sudo && return 0
    warn "Потрібен sudo — введіть пароль за запитом."
    sudo true 2>/dev/null
}

# ── Платформа ──────────────────────────────────────────────────────────────────
step "Платформа"
UNAME_S=$(uname -s)
case "$UNAME_S" in
    Linux)  PLATFORM="linux"  ;;
    Darwin) PLATFORM="macos"  ;;
    *) err "Непідтримувана ОС: $UNAME_S"; exit 1 ;;
esac
ARCH=$(uname -m)
ok "Платформа: ${PLATFORM} (${ARCH})"

# Linux: визначити дистрибутив для apt-репозиторію Docker
DOCKER_OS=""; OS_VERSION=""; DOCKER_ARCH=""
if [ "$PLATFORM" = "linux" ]; then
    case "$ARCH" in
        x86_64)  DOCKER_ARCH="amd64" ;; armv7l) DOCKER_ARCH="armhf" ;;
        aarch64) DOCKER_ARCH="arm64" ;; *) DOCKER_ARCH="$ARCH" ;;
    esac
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS_ID="${ID:-unknown}"; OS_VERSION="${VERSION_CODENAME:-${VERSION_ID:-}}"
        case "$OS_ID" in
            debian|ubuntu) DOCKER_OS="$OS_ID" ;;
            armbian)       DOCKER_OS="debian" ;;
            *) case "${ID_LIKE:-}" in
                   *debian*) DOCKER_OS="debian" ;;
                   *ubuntu*) DOCKER_OS="ubuntu" ;;
                   *)        DOCKER_OS="$OS_ID" ;;
               esac ;;
        esac
        ok "ОС: ${PRETTY_NAME:-$OS_ID}"
    fi
fi

# ── Параметри розгортання (інтерактивно) ─────────────────────────────────────
step "Параметри"
echo "  deploy_dir — локальна тека цього вузла, куди 3_deploy склонує репозиторій."
echo "  (Не SMB-маунт іншого вузла — саме локальний шлях цієї машини.)"
ask "Куди розгортати (deploy_dir)" "${INFRABOX_DEPLOY_DIR:-${HOME}/infrabox}" DEPLOY_DIR
SSL_DIR="${DEPLOY_DIR}/ui/frontend/ssl"
ok "deploy_dir: ${DEPLOY_DIR}"

# ── Ресурси (інформативно; критичний брак → задача адміну) ────────────────────
step "Ресурси"
if [ "$PLATFORM" = "linux" ]; then
    RAM_MB=$(awk '/MemTotal/{printf "%d", $2/1024}' /proc/meminfo)
    DISK_TARGET="$DEPLOY_DIR"; [ -d "$DISK_TARGET" ] || DISK_TARGET=$(dirname "$DISK_TARGET")
    DISK_FREE_GB=$(df -BG "$DISK_TARGET" 2>/dev/null | awk 'NR==2{v=$4; gsub("G","",v); print v+0}')
    [ -z "$DISK_FREE_GB" ] && DISK_FREE_GB=$(df -BG / | awk 'NR==2{v=$4; gsub("G","",v); print v+0}')
else
    RAM_MB=$(sysctl -n hw.memsize 2>/dev/null | awk '{printf "%d", $1/1024/1024}')
    DISK_FREE_GB=$(df -g / | awk 'NR==2{print $4+0}')
fi
ok "RAM: ${RAM_MB:-?}MB   Диск вільно: ${DISK_FREE_GB:-?}GB"
[ "${RAM_MB:-0}" -lt 512 ] 2>/dev/null && warn "RAM < 512MB — arch-підсистема може потребувати більше"
[ "${DISK_FREE_GB:-99}" -lt 3 ] 2>/dev/null && \
    add_todo "Замало місця на диску (${DISK_FREE_GB}GB)" "  Звільніть місце (мінімум 3GB, рекомендовано 6GB+) і повторіть."

# ── Docker ─────────────────────────────────────────────────────────────────────
step "Docker"
if _cmd docker && docker info >/dev/null 2>&1; then
    ok "Docker працює: $(docker info --format '{{.ServerVersion}}' 2>/dev/null)"
    DOCKER_OK=1
elif _cmd docker; then
    # Docker встановлений, але daemon не відповідає
    DOCKER_OK=0
    warn "Docker встановлений, але daemon не відповідає (docker info)"
    if [ "$PLATFORM" = "linux" ]; then
        info "Docker встановлений, але не запущений — стартую..."
        require_sudo && sudo systemctl enable --now docker 2>/dev/null || true
        docker info >/dev/null 2>&1 && { ok "Docker запущено"; DOCKER_OK=1; } \
            || add_todo "Docker встановлений, але не стартує" "  Перевірте: sudo systemctl status docker"
    else
        add_todo "Docker Desktop не запущено" "  Відкрийте Docker Desktop і дочекайтесь статусу «running», потім повторіть."
    fi
else
    # Docker відсутній
    DOCKER_OK=0
    if [ "$PLATFORM" = "linux" ]; then
        info "Docker відсутній — встановлюю (apt)..."
        if require_sudo; then
            for pkg in docker.io docker-doc docker-compose podman-docker containerd runc; do
                sudo apt-get remove -y "$pkg" >/dev/null 2>&1 || true
            done
            sudo apt-get update -qq && \
            sudo apt-get install -y -qq ca-certificates curl gnupg && \
            sudo install -m 0755 -d /usr/share/keyrings && \
            curl -fsSL "https://download.docker.com/linux/${DOCKER_OS}/gpg" \
                | sudo gpg --dearmor -o /usr/share/keyrings/docker.gpg && \
            sudo chmod a+r /usr/share/keyrings/docker.gpg && \
            echo "deb [arch=${DOCKER_ARCH} signed-by=/usr/share/keyrings/docker.gpg] https://download.docker.com/linux/${DOCKER_OS} ${OS_VERSION} stable" \
                | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null && \
            sudo apt-get update -qq && \
            sudo apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin && \
            sudo systemctl enable --now docker
            if docker info >/dev/null 2>&1; then ok "Docker встановлено й запущено"; DOCKER_OK=1
            else add_todo "Не вдалося встановити Docker автоматично" "  Встановіть вручну за https://docs.docker.com/engine/install/${DOCKER_OS}/"; fi
        else
            add_todo "Немає sudo для встановлення Docker" "  Надайте sudo-доступ або встановіть Docker вручну."
        fi
    else
        add_todo "Docker Desktop не встановлено" \
"  macOS не дозволяє встановити Docker зі скрипта (GUI-застосунок).
     1) Завантажте Docker Desktop: https://www.docker.com/products/docker-desktop/
     2) Встановіть і запустіть, дочекайтесь статусу «running».
     3) Повторіть: bash 2_host-prep.sh"
    fi
fi

# ── Docker Compose v2 ──────────────────────────────────────────────────────────
step "Docker Compose"
if docker compose version >/dev/null 2>&1; then
    ok "Compose v2: $(docker compose version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
elif [ "$PLATFORM" = "linux" ]; then
    info "Встановлюю docker-compose-plugin..."
    require_sudo && sudo apt-get install -y -qq docker-compose-plugin 2>/dev/null || true
    docker compose version >/dev/null 2>&1 && ok "Compose v2 встановлено" \
        || add_todo "Docker Compose v2 відсутній" "  Встановіть docker-compose-plugin (apt) або оновіть Docker."
else
    add_todo "Docker Compose v2 відсутній" "  Йде в комплекті Docker Desktop — переконайтесь, що Desktop встановлено й оновлено."
fi

# ── deploy_dir ─────────────────────────────────────────────────────────────────
step "Директорія розгортання"
mkdir -p "$DEPLOY_DIR" && ok "deploy_dir: $DEPLOY_DIR" \
    || add_todo "Не вдалося створити deploy_dir" "  Створіть вручну: mkdir -p $DEPLOY_DIR"

# Спільний лог-каталог
LOG_DIR="${DEPLOY_DIR}/log"
mkdir -p "$LOG_DIR"
if [ "$PLATFORM" = "linux" ]; then
    # tmpfs — логи в RAM (захист SD-карти від зносу)
    FSTAB_ENTRY="tmpfs  ${LOG_DIR}  tmpfs  defaults,size=64m,noatime,mode=0755  0  0"
    if grep -qF "${LOG_DIR}" /etc/fstab 2>/dev/null; then
        mountpoint -q "$LOG_DIR" 2>/dev/null || { require_sudo && sudo mount "$LOG_DIR" 2>/dev/null; }
        ok "log/ tmpfs (вже в fstab)"
    elif require_sudo; then
        echo "$FSTAB_ENTRY" | sudo tee -a /etc/fstab >/dev/null
        sudo mount "$LOG_DIR" 2>/dev/null && ok "log/ → tmpfs 64MB (RAM)" \
            || warn "fstab оновлено — tmpfs підмонтується після перезавантаження"
    else
        warn "Без sudo — log/ лишається звичайною текою (без tmpfs)"
    fi
else
    ok "log/ готова (на macOS tmpfs не застосовуємо)"
fi

# ── Docker-мережа infrabox-net ────────────────────────────────────────────────
step "Docker-мережа infrabox-net"
if [ "${DOCKER_OK:-0}" = "1" ]; then
    if docker network inspect infrabox-net >/dev/null 2>&1; then
        ok "Мережа infrabox-net вже існує"
    else
        docker network create infrabox-net >/dev/null && ok "Мережу infrabox-net створено" \
            || add_todo "Не вдалося створити мережу infrabox-net" "  Створіть вручну: docker network create infrabox-net"
    fi
else
    warn "Пропущено — Docker недоступний (див. задачі нижче)"
    add_todo "Створити мережу infrabox-net" "  Після запуску Docker: docker network create infrabox-net"
fi

# ── SSL-сертифікати ────────────────────────────────────────────────────────────
step "SSL-сертифікати"
mkdir -p "$SSL_DIR"
if [ -f "${SSL_DIR}/infrabox.crt" ] && [ -f "${SSL_DIR}/infrabox.key" ]; then
    ok "SSL-сертифікати вже є"
else
    echo "  SSL потрібен для HTTPS-доступу до UI (web слухає 443)."
    echo "  Без нього UI працюватиме лише по HTTP — згенерувати можна й пізніше."
    ask_yn "Згенерувати SSL-сертифікат зараз?" "y" GEN_SSL
    if [ "$GEN_SSL" = "0" ]; then
        warn "SSL пропущено — згенеруєте пізніше (UI поки без HTTPS)"
    else
        ask "Ім'я хоста для сертифіката" "$(hostname 2>/dev/null | sed 's/\.local$//')" CERT_HOST
        CERT_FQDN="${CERT_HOST}.local"
        if [ "$PLATFORM" = "linux" ]; then
            CERT_IP=$(ip -4 addr show 2>/dev/null | grep -v '127\.' | awk '/inet /{print $2}' | cut -d/ -f1 | head -1)
        else
            CERT_IP=$(ifconfig 2>/dev/null | grep 'inet ' | grep -v '127\.' | awk '{print $2}' | head -1)
        fi
        if _cmd mkcert; then
            info "Генерація через mkcert..."
            mkcert -install >/dev/null 2>&1 || true
            mkcert -cert-file "${SSL_DIR}/infrabox.crt" -key-file "${SSL_DIR}/infrabox.key" \
                "$CERT_HOST" "$CERT_FQDN" localhost 127.0.0.1 ${CERT_IP:+$CERT_IP} >/dev/null 2>&1 \
                && ok "mkcert сертифікат згенеровано (${CERT_HOST})" \
                || add_todo "mkcert не зміг згенерувати сертифікат" "  Згенеруйте вручну або пропустіть SSL при повторі."
            CA_ROOT=$(mkcert -CAROOT 2>/dev/null)
            [ -n "$CA_ROOT" ] && [ -f "${CA_ROOT}/rootCA.pem" ] && cp "${CA_ROOT}/rootCA.pem" "${SSL_DIR}/rootCA.pem" 2>/dev/null || true
        elif _cmd openssl; then
            info "mkcert відсутній — self-signed через openssl..."
            openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
                -keyout "${SSL_DIR}/infrabox.key" -out "${SSL_DIR}/infrabox.crt" \
                -subj "/C=UA/O=Infrabox/CN=${CERT_HOST}" \
                -addext "subjectAltName=DNS:${CERT_HOST},DNS:${CERT_FQDN},DNS:localhost,IP:127.0.0.1${CERT_IP:+,IP:$CERT_IP}" >/dev/null 2>&1 \
                && ok "Self-signed сертифікат згенеровано (10 років)" \
                || add_todo "openssl не зміг згенерувати сертифікат" "  Згенеруйте вручну або пропустіть SSL при повторі."
        else
            add_todo "Немає інструмента для SSL (mkcert/openssl)" \
"  Встановіть mkcert (рекомендовано) або openssl, потім повторіть.
     Linux: apt install openssl   macOS: brew install mkcert"
        fi
        [ -f "${SSL_DIR}/infrabox.key" ] && chmod 600 "${SSL_DIR}/infrabox.key" 2>/dev/null || true
    fi
fi

# ── Linux-специфічне: docker-група, logrotate ─────────────────────────────────
if [ "$PLATFORM" = "linux" ]; then
    step "Користувач у групі docker"
    if id -nG "$DEPLOY_USER" 2>/dev/null | grep -qw docker; then
        ok "'$DEPLOY_USER' уже в групі docker"
    elif require_sudo; then
        sudo usermod -aG docker "$DEPLOY_USER" && { ok "Додано '$DEPLOY_USER' до групи docker"; NEED_RELOGIN=1; } \
            || add_todo "Не вдалося додати в групу docker" "  sudo usermod -aG docker $DEPLOY_USER"
    else
        add_todo "Користувач не в групі docker" "  sudo usermod -aG docker $DEPLOY_USER  (потім re-login)"
    fi

    step "Logrotate"
    LR="/etc/logrotate.d/infrabox"
    if [ -f "$LR" ]; then
        ok "Logrotate вже налаштований"
    elif require_sudo; then
        _cmd logrotate || sudo apt-get install -y -qq logrotate 2>/dev/null || true
        sudo tee "$LR" >/dev/null <<EOF
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
        ok "Logrotate налаштований"
    else
        warn "Без sudo — logrotate пропущено (некритично)"
    fi
fi

# ══════════════════════════════════════════════════════════════════════════════
echo ""
if [ "$TODO_N" -eq 0 ]; then
    echo -e "${G}══════════════════════════════════════════${N}"
    echo -e "${G}  Хост готовий ✓${N}"
    echo -e "${G}══════════════════════════════════════════${N}"
    echo ""
    echo "  deploy_dir: $DEPLOY_DIR"
    [ "${DOCKER_OK:-0}" = "1" ] && echo "  Docker:     $(docker info --format '{{.ServerVersion}}' 2>/dev/null)"
    echo "  Мережа:     infrabox-net"
    [ -f "${SSL_DIR}/infrabox.crt" ] && echo "  SSL:        ${SSL_DIR}/infrabox.crt"
    echo ""
    [ "${NEED_RELOGIN:-0}" = "1" ] && warn "Виконайте 'newgrp docker' або перелогіньтесь (docker-група)"
    echo "Наступний крок: bash 3_deploy.sh (на admin-машині)"
    exit 0
else
    echo -e "${Y}══════════════════════════════════════════${N}"
    echo -e "${Y}  Потрібні дії адміна: ${TODO_N}${N}"
    echo -e "${Y}══════════════════════════════════════════${N}"
    echo -e "$TODO_TEXT"
    echo -e "Виконайте задачі вище і запустіть ${B}bash 2_host-prep.sh${N} знову."
    exit 2   # не помилка — сигнал «потрібне втручання адміна»
fi
