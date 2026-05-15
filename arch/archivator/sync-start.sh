#!/bin/sh
# sync-start.sh — запуск archivator з tmpfs і розумною синхронізацією
#
# /app/data          → tmpfs (апаратний максимум, задається в docker-compose)
# /app/data-disk     → bind-mount на диск (постійне сховище)
# /app/core_config/sys_params.json → звідси читається arch_ram_budget_mb
#
# Логіка sync:
#   • По часу:       кожні ARCH_SYNC_INTERVAL секунд (дефолт 600)
#   • По заповненню: якщо used >= arch_ram_budget_mb × FILL_TRIGGER%
#                    → sync на диск + purge найстарішого тому з RAM
#   • При зупинці:   SIGTERM/SIGINT → фінальний sync перед виходом

set -e

RAM_DIR="/app/data"
DISK_DIR="/app/data-disk"
SYS_PARAMS="/app/core_config/sys_params.json"

SYNC_INTERVAL="${ARCH_SYNC_INTERVAL:-600}"  # секунд між плановими sync
MAX_DAYS="${ARCH_MAX_DAYS:-4}"              # діб для відновлення з диска
FILL_TRIGGER=80                             # % бюджету для примусового sync
CHECK_INTERVAL=60                           # секунд між перевірками заповнення

log() { echo "[sync] $(date '+%H:%M:%S') $*"; }

# ── Читаємо бюджет RAM з sys_params.json (динамічно — підхоплює зміни з адмінки) ──
get_budget_mb() {
    python3 -c "
import json
try:
    with open('$SYS_PARAMS') as f:
        c = json.load(f)
    print(int(c['system'].get('arch_ram_budget_mb', 200)))
except Exception:
    print(200)
" 2>/dev/null || echo 200
}

# ── Відновлення disk → RAM при старті ────────────────────────────────────────
log "Відновлення даних з диска в RAM (останні ${MAX_DAYS} дні)..."
if [ -d "$DISK_DIR" ] && [ "$(ls -A "$DISK_DIR" 2>/dev/null)" ]; then
    [ -f "$DISK_DIR/sessions.json" ] && \
        cp "$DISK_DIR/sessions.json" "$RAM_DIR/" 2>/dev/null || true

    find "$DISK_DIR" -maxdepth 1 -mindepth 1 -type d -mtime "-${MAX_DAYS}" \
        | sort | while read -r vol; do
            if cp -a "$vol" "$RAM_DIR/" 2>/dev/null; then
                true
            else
                log "WARN: tmpfs повний при відновленні — $(basename "$vol") пропущено"
                break
            fi
        done
    log "Відновлено: $(du -sh "$RAM_DIR" 2>/dev/null | cut -f1)"
else
    log "Диск порожній — починаємо чистий архів"
fi

# ── Sync RAM → disk ───────────────────────────────────────────────────────────
do_sync() {
    cp -a "$RAM_DIR/." "$DISK_DIR/" 2>/dev/null && \
        log "Sync на диск: $(du -sh "$RAM_DIR" 2>/dev/null | cut -f1)" || \
        log "ERROR: sync не вдався"
}

# ── Видалити найстаріший том з RAM (він вже є на диску після sync) ────────────
purge_oldest_from_ram() {
    OLDEST=$(ls "$RAM_DIR" 2>/dev/null | grep -E '^[0-9]{4}-' | sort | head -1)
    if [ -n "$OLDEST" ]; then
        rm -rf "${RAM_DIR:?}/$OLDEST"
        log "Purge з RAM: $OLDEST → RAM тепер $(du -sh "$RAM_DIR" 2>/dev/null | cut -f1)"
    fi
}

# ── Фінальний sync при зупинці контейнера ────────────────────────────────────
cleanup() {
    log "Зупинка — фінальний sync..."
    do_sync
    log "Готово."
    [ -n "${ARCH_PID:-}" ] && kill "$ARCH_PID" 2>/dev/null || true
    exit 0
}
trap cleanup TERM INT

# ── Фоновий монітор: час + заповнення ────────────────────────────────────────
(
    LAST_SYNC=$(date +%s)
    while true; do
        sleep "$CHECK_INTERVAL"
        NOW=$(date +%s)
        USED_MB=$(du -sm "$RAM_DIR" 2>/dev/null | awk '{print $1}')
        BUDGET_MB=$(get_budget_mb)
        FILL_PCT=$(( USED_MB * 100 / (BUDGET_MB > 0 ? BUDGET_MB : 200) ))

        if [ "$FILL_PCT" -ge "$FILL_TRIGGER" ]; then
            log "RAM ${USED_MB}MB / бюджет ${BUDGET_MB}MB = ${FILL_PCT}% → sync + purge"
            do_sync
            purge_oldest_from_ram
            LAST_SYNC=$NOW
        elif [ $(( NOW - LAST_SYNC )) -ge "$SYNC_INTERVAL" ]; then
            log "Плановий sync (${USED_MB}MB / ${BUDGET_MB}MB, ${FILL_PCT}%)"
            do_sync
            LAST_SYNC=$NOW
        fi
    done
) &
log "Монітор запущено — інтервал ${SYNC_INTERVAL}s, тригер ${FILL_TRIGGER}% від бюджету"

# ── Запуск archivator ─────────────────────────────────────────────────────────
log "Запуск archivator..."
python /app/main.py &
ARCH_PID=$!
wait "$ARCH_PID"
