#!/usr/bin/env bash
# uninstall.sh — чисте видалення Infrabox з вузлів (мультихост)
#
# Usage:
#   bash uninstall.sh                    → інтерактивно (питає про critical дані)
#   bash uninstall.sh --keep-data        → тільки зупинити контейнери
#   bash uninstall.sh --full             → видалити все (з підтвердженням)
#   bash uninstall.sh --full --force     → видалити все без питань
#   bash uninstall.sh ui arch            → тільки вказані підсистеми
#   bash uninstall.sh --topology file    → альтернативний topology файл
#
# Режими:
#   default     — compose down + видалення некритичних даних + питання про critical
#   --keep-data — тільки compose down, дані не чіпаємо
#   --full      — compose down + ВСІ дані + мережа + deploy_dir (на всіх вузлах)
#
# Порядок: undeploy_order. Кожна підсистема знімається на своєму вузлі.

set -euo pipefail

# ── Кольори ───────────────────────────────────────────────────────────────────
R='\033[0;31m'; G='\033[0;32m'; Y='\033[1;33m'; B='\033[0;34m'; C='\033[0;36m'; N='\033[0m'
ok()   { echo -e "${G}✓${N} $*"; }
info() { echo -e "${B}→${N} $*"; }
warn() { echo -e "${Y}!${N} $*"; }
fail() { echo -e "${R}✗ ПОМИЛКА:${N} $*" >&2; exit 1; }
step() { echo -e "\n${C}── $* ──${N}"; }

# ── Шлях до скрипта ───────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Аргументи ─────────────────────────────────────────────────────────────────
TOPOLOGY="${TOPOLOGY_FILE:-topology.yml}"
MODE="interactive"   # interactive | keep-data | full
FORCE=0
FILTER=""

while [ $# -gt 0 ]; do
    case "$1" in
        --keep-data)   MODE="keep-data";  shift   ;;
        --full)        MODE="full";       shift   ;;
        --force)       FORCE=1;           shift   ;;
        --topology)    TOPOLOGY="$2";     shift 2 ;;
        --*) fail "Невідомий аргумент: $1" ;;
        *)   FILTER="${FILTER:+$FILTER }$1"; shift ;;
    esac
done

[ -f "$TOPOLOGY" ] || fail "Не знайдено: $TOPOLOGY"
command -v python3 &>/dev/null || fail "python3 не знайдено"
[ -f "$SCRIPT_DIR/_topo.py" ] || fail "Не знайдено: $SCRIPT_DIR/_topo.py"

TOPO() { python3 "$SCRIPT_DIR/_topo.py" "$TOPOLOGY" "$@"; }

eval "$(TOPO system)"

# ── Список підсистем (undeploy_order ∩ FILTER) ────────────────────────────────
UNDEPLOY_ORDER="$(TOPO order undeploy | tr '\n' ' ')"

if [ -n "$FILTER" ]; then
    ACTIVE_SUBS=""
    for s in $UNDEPLOY_ORDER; do
        for f in $FILTER; do
            [ "$s" = "$f" ] && ACTIVE_SUBS="${ACTIVE_SUBS:+$ACTIVE_SUBS }$s" && break
        done
    done
    [ -z "$ACTIVE_SUBS" ] && fail "Жодна підсистема не відповідає фільтру: $FILTER"
    PARTIAL=1
else
    ACTIVE_SUBS="$UNDEPLOY_ORDER"
    PARTIAL=0
fi

ACTIVE_NODES="$(TOPO nodes-for $ACTIVE_SUBS | tr '\n' ' ')"

# ── SSH-хелпери (поточний вузол) ──────────────────────────────────────────────
use_node() {
    eval "$(TOPO node "$1")"
    CUR_KEY="${NODE_KEY/#\~/$HOME}"
}
_ssh_opt() {
    [ -f "$CUR_KEY" ] && echo "-i $CUR_KEY -o StrictHostKeyChecking=accept-new" \
                      || echo "-o StrictHostKeyChecking=accept-new"
}
_ssh()   { ssh $(_ssh_opt) "${NODE_USER}@${NODE_HOST}" "$@"; }
_ssh_q() { _ssh "$@" 2>/dev/null || true; }

# ── Підтвердження ──────────────────────────────────────────────────────────────
confirm() {
    [ "$FORCE" = "1" ] && return 0
    printf "  ${Y}%s${N} [y/N]: " "$1"
    read -r ans || true
    case "$ans" in y|Y|yes|YES|т|Т|так|ТАК) return 0 ;; *) return 1 ;; esac
}

# ── Заголовок ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${R}╔══════════════════════════════════════════════════════╗${N}"
echo -e "${R}║             Infrabox  Uninstall                      ║${N}"
echo -e "${R}╚══════════════════════════════════════════════════════╝${N}"
echo ""
printf "  Підсистеми: %s\n" "$ACTIVE_SUBS"
printf "  Вузли:      %s\n" "$ACTIVE_NODES"
printf "  Режим:      ${Y}%s${N}\n" "$MODE"
echo ""

case "$MODE" in
    keep-data) info "Тільки зупинка контейнерів. Дані не видаляються." ;;
    full)      warn "FULL режим — будуть видалені ВСІ дані і deploy_dir на всіх вузлах" ;;
    *)         info "Інтерактивний режим — запит перед видаленням критичних даних." ;;
esac
echo ""

if [ "$MODE" = "full" ]; then
    confirm "Продовжити видалення всього на: ${ACTIVE_NODES}?" || { warn "Скасовано."; exit 0; }
else
    confirm "Продовжити?" || { warn "Скасовано."; exit 0; }
fi

# ── Docker compose down (по підсистемах, кожна на свій вузол) ──────────────────
step "Docker compose down"

for SUB in $ACTIVE_SUBS; do
    eval "$(TOPO sub "$SUB")"   # SUB_NODE, SUB_WORKDIR
    use_node "$SUB_NODE"
    COMPOSE_DIR="${NODE_DEPLOY_DIR}/${SUB_WORKDIR}"

    printf "\n  ${B}[%s]${N}  %s  (%s)\n" "$SUB" "$COMPOSE_DIR" "$NODE_ALIAS"

    if ! _ssh_q "exit 0"; then
        warn "  SSH недоступний: ${NODE_USER}@${NODE_HOST} — пропускаємо"
        continue
    fi

    HAS_COMPOSE=$(_ssh_q \
        "test -f '${COMPOSE_DIR}/docker-compose.yml' || test -f '${COMPOSE_DIR}/compose.yml' && echo yes || echo no")
    if [ "${HAS_COMPOSE:-no}" != "yes" ]; then
        warn "  Пропускаємо: compose-файл не знайдено у ${COMPOSE_DIR}"
        continue
    fi

    DOWN_OUT=$(_ssh "cd '${COMPOSE_DIR}' && docker compose down 2>&1") || true
    if echo "$DOWN_OUT" | grep -qi "error\b"; then
        echo "$DOWN_OUT" | grep -i error | sed 's/^/    /'
        warn "  compose down завершився з помилками (продовжуємо)"
    else
        ok "  ${SUB}: down ✓"
    fi
done

# ── Дані (тільки повне видалення, не partial, не keep-data) ───────────────────
if [ "$MODE" != "keep-data" ] && [ "$PARTIAL" = "0" ]; then

    # — Bind mounts: застосувати на кожному задіяному вузлі —
    step "Bind mounts"
    while IFS= read -r ENTRY; do
        [ -z "$ENTRY" ] && continue
        PATH_REL="${ENTRY%%|||*}"
        REST="${ENTRY#*|||}"
        DESC="${REST%%|||*}"
        CRIT="${ENTRY##*|||}"

        DO_DELETE=0
        if [ "$CRIT" = "0" ]; then
            DO_DELETE=1
        else
            echo ""
            printf "  ${Y}CRITICAL${N}: %s — %s\n" "$PATH_REL" "$DESC"
            if [ "$MODE" = "full" ] || confirm "  Видалити ${PATH_REL} на всіх вузлах?"; then
                DO_DELETE=1
            fi
        fi

        if [ "$DO_DELETE" = "1" ]; then
            for NODE in $ACTIVE_NODES; do
                use_node "$NODE"
                _ssh_q "rm -rf '${NODE_DEPLOY_DIR}/${PATH_REL}'" || true
            done
            ok "Видалено (всі вузли): ${PATH_REL}"
        else
            info "Збережено: ${PATH_REL}"
        fi
    done <<< "$(TOPO data-binds)"

    # — Docker volumes: на кожному вузлі де існує —
    step "Docker volumes"
    while IFS= read -r ENTRY; do
        [ -z "$ENTRY" ] && continue
        VOL_NAME="${ENTRY%%|||*}"
        REST="${ENTRY#*|||}"
        BACKUP_CMD="${REST%%|||*}"
        CRIT="${ENTRY##*|||}"

        DO_DELETE=0
        if [ "$MODE" = "full" ] || [ "$CRIT" = "0" ]; then
            DO_DELETE=1
        else
            echo ""
            printf "  ${Y}CRITICAL Volume${N}: %s\n" "$VOL_NAME"
            [ -n "$BACKUP_CMD" ] && printf "  ${B}Бекап:${N} %s\n" "$BACKUP_CMD"
            confirm "  Видалити volume ${VOL_NAME}?" && DO_DELETE=1
        fi

        if [ "$DO_DELETE" = "1" ]; then
            for NODE in $ACTIVE_NODES; do
                use_node "$NODE"
                EXISTS=$(_ssh_q "docker volume inspect '${VOL_NAME}' &>/dev/null && echo yes || echo no")
                [ "${EXISTS:-no}" = "yes" ] || continue
                _ssh_q "docker volume rm --force '${VOL_NAME}'" || true
                ok "Volume видалено на ${NODE}: ${VOL_NAME}"
            done
        else
            info "Volume збережено: ${VOL_NAME}"
        fi
    done <<< "$(TOPO data-vols)"

    # — Docker мережа + deploy_dir + logrotate: на кожному вузлі —
    step "Мережа / deploy_dir / logrotate (по вузлах)"
    for NODE in $ACTIVE_NODES; do
        use_node "$NODE"
        printf "\n  ${B}%s${N} (%s):\n" "$NODE" "$NODE_HOST"

        NET_EXISTS=$(_ssh_q "docker network inspect infrabox-net &>/dev/null && echo yes || echo no")
        if [ "${NET_EXISTS:-no}" = "yes" ]; then
            _ssh_q "docker network rm infrabox-net" || true
            ok "  Мережу infrabox-net видалено"
        else
            info "  Мережа infrabox-net: вже відсутня"
        fi

        if [ "$MODE" = "full" ]; then
            if confirm "  Видалити deploy_dir ${NODE_DEPLOY_DIR} на ${NODE}?"; then
                _ssh_q "rm -rf '${NODE_DEPLOY_DIR}'" || true
                ok "  deploy_dir видалено"
            else
                info "  deploy_dir збережено"
            fi
            LR=$(_ssh_q "test -f /etc/logrotate.d/infrabox && echo yes || echo no")
            [ "${LR:-no}" = "yes" ] && warn "  Видаліть вручну: sudo rm /etc/logrotate.d/infrabox"
        fi
    done
fi

# ── Залишкові контейнери по вузлах ────────────────────────────────────────────
step "Перевірка залишків"
for NODE in $ACTIVE_NODES; do
    use_node "$NODE"
    LEFT=$(_ssh_q "docker ps -a --format '{{.Names}}' | grep -i infrabox" || true)
    if [ -n "$LEFT" ]; then
        warn "${NODE}: залишились контейнери infrabox:"
        echo "$LEFT" | sed 's/^/    /'
    else
        ok "${NODE}: контейнери infrabox відсутні"
    fi
done

# ── Підсумок ──────────────────────────────────────────────────────────────────
echo ""
echo -e "${G}══════════════════════════════════════════${N}"
echo -e "${G}  Видалення завершено ✓${N}"
echo -e "${G}══════════════════════════════════════════${N}"
echo ""
printf "  Знято:  %s\n" "$ACTIVE_SUBS"
printf "  Вузли:  %s\n" "$ACTIVE_NODES"
printf "  Режим:  %s\n" "$MODE"
echo ""
