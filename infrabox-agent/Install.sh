#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════
#  InfraBox Agent — Скрипт встановлення для Armbian/Debian
#  Запуск: bash install.sh
# ══════════════════════════════════════════════════════════
set -e

AGENT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="infrabox-agent"
PYTHON_BIN="$(which python3)"
VENV_DIR="$AGENT_DIR/venv"

echo "════════════════════════════════════════"
echo " InfraBox Agent — встановлення"
echo " Директорія: $AGENT_DIR"
echo "════════════════════════════════════════"

# ── 1. Системні залежності ──────────────────────────────
echo ""
echo "▶ [1/5] Встановлення системних пакетів..."
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-pip python3-venv python3-dev \
    libffi-dev libssl-dev gcc build-essential

# ── 2. Віртуальне середовище ────────────────────────────
echo ""
echo "▶ [2/5] Створення Python venv..."
$PYTHON_BIN -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"
pip install --upgrade pip --quiet

# ── 3. Python залежності ────────────────────────────────
echo ""
echo "▶ [3/5] Встановлення Python пакетів..."
pip install -r "$AGENT_DIR/requirements.txt" --quiet
echo "   ✅ Пакети встановлено"

# ── 4. Конфігурація .env ────────────────────────────────
echo ""
echo "▶ [4/5] Налаштування конфігурації..."

if [ ! -f "$AGENT_DIR/.env" ]; then
    cp "$AGENT_DIR/.env.example" "$AGENT_DIR/.env"
    echo ""
    echo "   ⚠️  Файл .env створено. Заповніть його перед запуском:"
    echo "   nano $AGENT_DIR/.env"
    echo ""
    echo "   Потрібно вказати:"
    echo "   • TELEGRAM_TOKEN  — від @BotFather у Telegram"
    echo "   • ANTHROPIC_API_KEY — з https://console.anthropic.com"
    echo "   • ALLOWED_CHAT_IDS — ваш Telegram ID (від @userinfobot)"
    echo ""
    read -p "   Відкрити .env для редагування зараз? [y/N]: " EDIT_NOW
    if [[ "$EDIT_NOW" =~ ^[Yy]$ ]]; then
        ${EDITOR:-nano} "$AGENT_DIR/.env"
    fi
else
    echo "   ✅ .env вже існує, пропускаємо"
fi

# ── 5. Systemd service ──────────────────────────────────
echo ""
echo "▶ [5/5] Встановлення systemd сервісу..."

SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

sudo tee "$SERVICE_FILE" > /dev/null << EOF
[Unit]
Description=InfraBox Agent (Telegram + Claude AI)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$AGENT_DIR
ExecStart=$VENV_DIR/bin/python $AGENT_DIR/agent.py
Restart=always
RestartSec=10
StandardOutput=append:/var/log/infrabox-agent.log
StandardError=append:/var/log/infrabox-agent.log
EnvironmentFile=$AGENT_DIR/.env

[Install]
WantedBy=multi-user.target
EOF

# Створюємо файл логів
sudo touch /var/log/infrabox-agent.log
sudo chown $USER:$USER /var/log/infrabox-agent.log

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"

echo ""
echo "════════════════════════════════════════"
echo " ✅ Встановлення завершено!"
echo "════════════════════════════════════════"
echo ""
echo " Команди управління:"
echo "   sudo systemctl start $SERVICE_NAME    # запустити"
echo "   sudo systemctl stop $SERVICE_NAME     # зупинити"
echo "   sudo systemctl restart $SERVICE_NAME  # перезапустити"
echo "   sudo systemctl status $SERVICE_NAME   # статус"
echo "   tail -f /var/log/infrabox-agent.log   # логи"
echo ""

# Пропонуємо запустити
if [ -f "$AGENT_DIR/.env" ]; then
    # Перевіряємо чи заповнений .env
    if grep -q "TELEGRAM_TOKEN=1234567890" "$AGENT_DIR/.env" 2>/dev/null; then
        echo "   ⚠️  Спочатку заповніть .env, потім запустіть:"
        echo "   sudo systemctl start $SERVICE_NAME"
    else
        read -p " Запустити агента зараз? [y/N]: " START_NOW
        if [[ "$START_NOW" =~ ^[Yy]$ ]]; then
            sudo systemctl start "$SERVICE_NAME"
            sleep 2
            sudo systemctl status "$SERVICE_NAME" --no-pager
        fi
    fi
fi