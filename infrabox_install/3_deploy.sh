#!/usr/bin/env bash
# 3_deploy.sh — мультихостове розгортання Infrabox з topology.yml
#
# Usage:
#   bash 3_deploy.sh                     → всі підсистеми (з deploy_order), на їхні вузли
#   bash 3_deploy.sh core ui             → тільки вказані, в порядку topology
#   bash 3_deploy.sh --dry-run           → показати план без дій
#   bash 3_deploy.sh --topology path.yml → альтернативний topology файл
#
# Що робить:
#   1. Парсить topology.yml (спільний _topo.py)
#   2. Визначає вузли, задіяні для обраних підсистем (поле node:)
#   3. Для кожного вузла: SSH-перевірка → git clone/pull → docker compose up
#      (підсистеми вузла в порядку deploy_order)
#   4. Виводить статус контейнерів по вузлах

set -euo pipefail

# ── Кольори ───────────────────────────────────────────────────────────────────
R='\033[0;31m'; G='\033[0;32m'; Y='\033[1;33m'; B='\033[0;34m'; C='\033[0;36m'; N='\033[0m'
ok()   { echo -e "${G}✓${N} $*"; }
info() { echo -e "${B}→${N} $*"; }
warn() { echo -e "${Y}!${N} $*"; }
fail() { echo -e "${R}✗ ПОМИЛКА:${N} $*" >&2; exit 1; }
step() { echo -e "\n${C}── $* ──${N}"; }

# ── Шлях до скрипта (для _topo.py) ────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Аргументи ─────────────────────────────────────────────────────────────────
TOPOLOGY="${TOPOLOGY_FILE:-topology.yml}"
DRY_RUN=0
FILTER=""           # пробіл-розділені назви підсистем, або "" = всі

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)           DRY_RUN=1;            shift   ;;
        --topology)          TOPOLOGY="$2";         shift 2 ;;
        --*) fail "Невідомий аргумент: $1" ;;
        *)   FILTER="${FILTER:+$FILTER }$1"; shift ;;
    esac
done

[ -f "$TOPOLOGY" ] || fail "Не знайдено: $TOPOLOGY\nЗапустіть спочатку: bash 0_prepare.sh"
command -v python3 &>/dev/null || fail "python3 не знайдено"
[ -f "$SCRIPT_DIR/_topo.py" ] || fail "Не знайдено: $SCRIPT_DIR/_topo.py"

# ── Обгортка парсера ──────────────────────────────────────────────────────────
TOPO() { python3 "$SCRIPT_DIR/_topo.py" "$TOPOLOGY" "$@"; }

# ── Система ───────────────────────────────────────────────────────────────────
eval "$(TOPO system)" || fail "Не вдалося розпарсити $TOPOLOGY"

# ── Список підсистем для цього запуску (deploy_order ∩ FILTER) ────────────────
DEPLOY_ORDER="$(TOPO order deploy | tr '\n' ' ')"
[ -z "$(echo "$DEPLOY_ORDER" | tr -d ' ')" ] && fail "deploy_order порожній у $TOPOLOGY"

if [ -n "$FILTER" ]; then
    ACTIVE_SUBS=""
    for s in $DEPLOY_ORDER; do
        for f in $FILTER; do
            [ "$s" = "$f" ] && ACTIVE_SUBS="${ACTIVE_SUBS:+$ACTIVE_SUBS }$s" && break
        done
    done
    [ -z "$ACTIVE_SUBS" ] && fail "Жодна підсистема не відповідає фільтру: $FILTER"
else
    ACTIVE_SUBS="$DEPLOY_ORDER"
fi

# ── Задіяні вузли (для обраних підсистем, у порядку появи) ────────────────────
ACTIVE_NODES="$(TOPO nodes-for $ACTIVE_SUBS | tr '\n' ' ')"
[ -z "$(echo "$ACTIVE_NODES" | tr -d ' ')" ] && fail "Не визначено вузлів для: $ACTIVE_SUBS"

# ── SSH-хелпери (працюють з поточним вузлом NODE_*/CUR_KEY) ───────────────────
use_node() {
    eval "$(TOPO node "$1")"
    CUR_KEY="${NODE_KEY/#\~/$HOME}"
}
_ssh_args() {
    if [ -f "$CUR_KEY" ]; then
        echo "-i $CUR_KEY -o StrictHostKeyChecking=accept-new"
    else
        echo "-o StrictHostKeyChecking=accept-new"
    fi
}
_ssh()   { ssh $(_ssh_args) "${NODE_USER}@${NODE_HOST}" "$@"; }
_ssh_q() { _ssh "$@" 2>/dev/null || true; }

# ── Чи є елемент у списку ──────────────────────────────────────────────────────
in_list() {
    # in_list <needle> <space-separated-list>
    local needle="$1"; shift
    local item
    for item in $*; do
        [ "$item" = "$needle" ] && return 0
    done
    return 1
}

# ── Git sync на поточному вузлі (clone або pull + checkout) ───────────────────
git_sync() {
    if ! _ssh_q "test -d '${NODE_DEPLOY_DIR}/.git'"; then
        info "Репо не знайдено — git clone..."
        local parent
        parent=$(dirname "${NODE_DEPLOY_DIR}")
        local out
        out=$(_ssh "mkdir -p '${parent}' && git clone --branch '${SYS_BRANCH}' '${SYS_REPO}' '${NODE_DEPLOY_DIR}' 2>&1") || {
            echo "$out" | tail -10 | sed 's/^/    /'
            fail "git clone failed на ${NODE_ALIAS}"
        }
        ok "git clone виконано (${SYS_BRANCH})"
    else
        local out
        out=$(_ssh "
            cd '${NODE_DEPLOY_DIR}' || exit 1
            git fetch origin 2>&1
            CURRENT=\$(git rev-parse --abbrev-ref HEAD)
            if [ \"\$CURRENT\" != '${SYS_BRANCH}' ]; then
                echo \"Switching branch: \$CURRENT → ${SYS_BRANCH}\"
                git checkout '${SYS_BRANCH}' 2>&1 || git checkout -b '${SYS_BRANCH}' --track 'origin/${SYS_BRANCH}' 2>&1
            fi
            git pull origin '${SYS_BRANCH}' 2>&1
        ")
        if echo "$out" | grep -q "Already up to date"; then
            ok "Already up to date (${SYS_BRANCH})"
        else
            echo "$out" | head -10 | sed 's/^/    /'
            ok "Git sync виконано (${SYS_BRANCH})"
        fi
    fi
}

# ── Заголовок ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${C}╔══════════════════════════════════════════════════════╗${N}"
echo -e "${C}║               Infrabox  Deploy                       ║${N}"
echo -e "${C}╚══════════════════════════════════════════════════════╝${N}"
echo ""
printf "  Система:   ${G}%s${N}\n" "$SYS_NAME"
printf "  Branch:    %s\n"   "$SYS_BRANCH"
printf "  Підсистеми: %s\n"  "$ACTIVE_SUBS"
printf "  Вузли:     %s\n"   "$ACTIVE_NODES"
[ "$DRY_RUN" = "1" ] && warn "DRY-RUN — жодних дій"
echo ""

# ── Деплой по вузлах ──────────────────────────────────────────────────────────
for NODE in $ACTIVE_NODES; do
    use_node "$NODE"

    step "Вузол: ${NODE}  (${NODE_HOST})"

    # підсистеми цього вузла, що входять у вибірку (вже в deploy_order)
    SUBS_HERE=""
    for s in $(TOPO subs-on "$NODE"); do
        in_list "$s" "$ACTIVE_SUBS" && SUBS_HERE="${SUBS_HERE:+$SUBS_HERE }$s"
    done
    [ -z "$SUBS_HERE" ] && { info "немає підсистем для розгортання"; continue; }
    printf "  Підсистеми вузла: %s\n" "$SUBS_HERE"

    # — SSH —
    if [ "$DRY_RUN" = "1" ]; then
        info "[dry] ssh -i $CUR_KEY ${NODE_USER}@${NODE_HOST}"
    elif _ssh_q "exit 0"; then
        ok "SSH OK: ${NODE_USER}@${NODE_HOST}"
    else
        fail "SSH недоступний: ${NODE_USER}@${NODE_HOST}  (ключ: $CUR_KEY)"
    fi

    # — Git sync (раз на вузол) —
    if [ "$DRY_RUN" = "1" ]; then
        info "[dry] git clone/pull ${SYS_REPO} @ ${SYS_BRANCH} → ${NODE_DEPLOY_DIR}"
    else
        git_sync
    fi

    # — Compose up для підсистем цього вузла —
    for SUB in $SUBS_HERE; do
        eval "$(TOPO sub "$SUB")"   # SUB_NODE, SUB_WORKDIR
        COMPOSE_DIR="${NODE_DEPLOY_DIR}/${SUB_WORKDIR}"

        printf "\n  ${B}[%s]${N}  %s\n" "$SUB" "$COMPOSE_DIR"

        if [ "$DRY_RUN" = "1" ]; then
            info "[dry] cd ${COMPOSE_DIR} && docker compose up -d --remove-orphans"
            continue
        fi

        if ! _ssh_q "test -d '${COMPOSE_DIR}' && { test -f '${COMPOSE_DIR}/docker-compose.yml' || test -f '${COMPOSE_DIR}/compose.yml'; }"; then
            warn "Пропускаємо ${SUB}: docker-compose.yml не знайдено у ${COMPOSE_DIR}"
            continue
        fi

        COMPOSE_OUT=$(_ssh "cd '${COMPOSE_DIR}' && docker compose up -d --remove-orphans 2>&1") || {
            echo "$COMPOSE_OUT" | tail -20 | sed 's/^/    /'
            fail "docker compose up -d failed: ${SUB} (${NODE})"
        }

        if echo "$COMPOSE_OUT" | grep -qi "error\b"; then
            echo "$COMPOSE_OUT" | grep -i "error" | sed 's/^/    /'
            warn "${SUB}: compose завершився з помилками (перевірте вище)"
        else
            ok "${SUB}: up ✓"
        fi
    done
done

# ── Статус контейнерів по вузлах ──────────────────────────────────────────────
step "Статус"

if [ "$DRY_RUN" = "0" ]; then
    for NODE in $ACTIVE_NODES; do
        use_node "$NODE"
        STATUS=$(_ssh_q "docker ps --format 'table {{.Names}}\t{{.Status}}' | grep -i infrabox" || true)
        printf "\n  ${B}%s${N} (%s):\n" "$NODE" "$NODE_HOST"
        if [ -n "$STATUS" ]; then
            echo "$STATUS" | sed 's/^/    /'
        else
            warn "  немає запущених контейнерів infrabox"
        fi
    done
    echo ""
fi

# ── Підсумок ──────────────────────────────────────────────────────────────────
echo ""
echo -e "${G}══════════════════════════════════════════${N}"
echo -e "${G}  Розгортання завершено ✓${N}"
echo -e "${G}══════════════════════════════════════════${N}"
echo ""
printf "  Підсистеми: %s\n" "$ACTIVE_SUBS"
printf "  Вузли:      %s\n" "$ACTIVE_NODES"
echo ""
echo "Перевірити стан: bash 4_status.sh"
