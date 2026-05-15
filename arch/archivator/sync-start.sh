#!/bin/sh
# sync-start.sh — запуск archivator з tmpfs-даними і синхронізацією на диск
#
# /app/data      → tmpfs (RAM), archivator пише сюди
# /app/data-disk → bind-mount на диск, постійне сховище
#
# При старті: disk → RAM (відновлення даних)
# Кожні 10 хв:  RAM → disk (збереження)
# При зупинці SIGTERM/SIGINT: фінальний sync перед виходом

set -e

RAM_DIR="/app/data"
DISK_DIR="/app/data-disk"
SYNC_INTERVAL="${ARCH_SYNC_INTERVAL:-600}"   # 10 хв за замовчуванням

log() { echo "[sync] $(date '+%H:%M:%S') $*"; }

# ── Відновлення disk → RAM ────────────────────────────────────────────────────
log "Відновлення даних з диска в RAM..."
if [ -d "$DISK_DIR" ] && [ "$(ls -A "$DISK_DIR" 2>/dev/null)" ]; then
    cp -a "$DISK_DIR/." "$RAM_DIR/" 2>/dev/null && \
        log "Відновлено: $(du -sh "$RAM_DIR" 2>/dev/null | cut -f1)" || \
        log "WARN: часткове відновлення (можливо tmpfs заповнений)"
else
    log "Диск порожній — починаємо чистий архів"
fi

# ── Функція синхронізації ─────────────────────────────────────────────────────
do_sync() {
    cp -a "$RAM_DIR/." "$DISK_DIR/" 2>/dev/null && \
        log "Збережено на диск: $(du -sh "$RAM_DIR" 2>/dev/null | cut -f1)" || \
        log "ERROR: синхронізація не вдалась"
}

# ── Фінальний sync при зупинці ────────────────────────────────────────────────
cleanup() {
    log "Зупинка — фінальна синхронізація..."
    do_sync
    log "Готово. Завершення."
    # Зупиняємо archivator
    [ -n "${ARCH_PID:-}" ] && kill "$ARCH_PID" 2>/dev/null || true
    exit 0
}
trap cleanup TERM INT

# ── Фоновий цикл синхронізації ────────────────────────────────────────────────
(
    while true; do
        sleep "$SYNC_INTERVAL"
        do_sync
    done
) &
SYNC_PID=$!
log "Синхронізація запущена (інтервал: ${SYNC_INTERVAL}s, PID: $SYNC_PID)"

# ── Запуск archivator ─────────────────────────────────────────────────────────
log "Запуск archivator..."
python /app/main.py &
ARCH_PID=$!

wait "$ARCH_PID"
