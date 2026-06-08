#!/usr/bin/env bash
# gen-ssh-key.sh — генерація SSH-ключа для Infrabox admin-машини
# Запуск: bash gen-ssh-key.sh [ім'я_ключа]
# Приклад: bash gen-ssh-key.sh homeServ2

set -euo pipefail

# ── Кольори ───────────────────────────────────────────────────────────────────
C='\033[0;36m'; G='\033[0;32m'; Y='\033[0;33m'; R='\033[0;31m'; B='\033[1m'; N='\033[0m'
ok()   { echo -e "  ${G}✓${N}  $*"; }
info() { echo -e "  ${C}·${N}  $*"; }
warn() { echo -e "  ${Y}!${N}  $*"; }
fail() { echo -e "  ${R}✗${N}  $*" >&2; exit 1; }
hr()   { echo -e "${C}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${N}"; }
box()  { echo -e "\n${B}$*${N}"; }

# ── Аргументи ─────────────────────────────────────────────────────────────────
KEY_NAME="${1:-}"
SSH_DIR="${HOME}/.ssh"

# ── Вітання ───────────────────────────────────────────────────────────────────
echo ""
hr
echo -e "  ${B}INFRABOX — Генератор SSH-ключа${N}"
hr

# ── Ім'я ключа ────────────────────────────────────────────────────────────────
if [ -z "$KEY_NAME" ]; then
    echo ""
    printf "  Ім'я ключа (наприклад, homeServ2): "
    read -r KEY_NAME
fi
KEY_NAME="${KEY_NAME:-infrabox}"
# залишаємо тільки безпечні символи
KEY_NAME=$(echo "$KEY_NAME" | tr -cd '[:alnum:]_.-')
[ -z "$KEY_NAME" ] && fail "Некоректне ім'я ключа"

KEY_FILE="${SSH_DIR}/infrabox_${KEY_NAME}"
COMMENT="infrabox@${KEY_NAME}"

# ── Перевірка ─────────────────────────────────────────────────────────────────
echo ""
if [ -f "$KEY_FILE" ]; then
    warn "Ключ вже існує: ${KEY_FILE}"
    printf "  Перезаписати? [y/N] "
    read -r ANSWER
    case "$ANSWER" in
        y|Y|yes|YES) rm -f "${KEY_FILE}" "${KEY_FILE}.pub" ;;
        *) info "Скасовано."; exit 0 ;;
    esac
fi

# ── Генерація ─────────────────────────────────────────────────────────────────
mkdir -p "$SSH_DIR"
chmod 700 "$SSH_DIR"

info "Генерація ed25519-ключа..."
ssh-keygen -t ed25519 -C "$COMMENT" -f "$KEY_FILE" -N "" -q
chmod 600 "${KEY_FILE}"
chmod 644 "${KEY_FILE}.pub"

ok "Ключ згенеровано"

# ── Результат ─────────────────────────────────────────────────────────────────
echo ""
hr
box "  Ключі створено:"
hr
echo ""
echo -e "  Приватний ключ  (admin-машина):"
echo -e "  ${B}${KEY_FILE}${N}"
echo ""
echo -e "  Публічний ключ  (скопіювати на цільовий хост):"
echo -e "  ${B}${KEY_FILE}.pub${N}"
echo ""
hr
box "  Публічний ключ (вміст):"
hr
echo ""
cat "${KEY_FILE}.pub"
echo ""

# ── Інструкція ────────────────────────────────────────────────────────────────
hr
box "  Що робити далі:"
hr
echo ""
echo -e "  ${B}1. Розмістити приватний ключ на ADMIN-МАШИНІ${N}"
echo -e "     Вже збережено: ${KEY_FILE}"
echo -e "     Більше нікуди переносити не потрібно."
echo ""
echo -e "  ${B}2. Скопіювати публічний ключ на ЦІЛЬОВИЙ ХОСТ${N}"
echo -e "     Виконати на admin-машині:"
echo ""
echo -e "     ${C}ssh-copy-id -i ${KEY_FILE}.pub USER@HOST${N}"
echo ""
echo -e "     Або вручну (якщо ssh-copy-id недоступний):"
echo -e "     ${C}cat ${KEY_FILE}.pub | ssh USER@HOST 'mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys'${N}"
echo ""
echo -e "  ${B}3. Перевірити підключення${N}"
echo ""
echo -e "     ${C}ssh -i ${KEY_FILE} USER@HOST${N}"
echo ""
echo -e "  ${B}4. Вказати шлях у topology.yml${N}"
echo -e "     1_prepare.sh запитає ssh_key — вказати:"
echo ""
echo -e "     ${C}${KEY_FILE}${N}"
echo ""
hr
echo ""
