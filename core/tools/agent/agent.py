#!/usr/bin/env python3
"""
InfraBox Agent — Telegram-бот з Claude AI для BananaPi M2 Berry (Armbian ARMv7)
Функції: моніторинг системи, керування файлами, виконання команд, планувальник, AI-чат
"""

import asyncio
import os
import re
import subprocess
import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from functools import wraps

import psutil
from anthropic import Anthropic
from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

# ── Завантаження конфігурації ───────────────────────────────────────────────
load_dotenv()

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
ALLOWED_IDS_RAW = os.environ.get("ALLOWED_CHAT_IDS", "")
ALLOWED_CHAT_IDS = {int(x) for x in ALLOWED_IDS_RAW.split(",") if x.strip()}
WORK_DIR = os.environ.get("WORK_DIR", str(Path.home()))
LOG_FILE = os.environ.get("LOG_FILE", "/tmp/infrabox-agent.log")
PROJECT_INFO = os.environ.get("PROJECT_INFO", "")  # Опис проєкту InfraBox

# ── Логування ───────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# ── Claude клієнт ───────────────────────────────────────────────────────────
claude = Anthropic(api_key=ANTHROPIC_API_KEY)
scheduler = AsyncIOScheduler(timezone="Europe/Kyiv")

# Пам'ять розмов (chat_id → список повідомлень)
history: dict[int, list] = {}

# Очікуючі команди для підтвердження (chat_id → список команд)
pending_commands: dict[int, list[str]] = {}

SYSTEM_PROMPT = f"""Ти — InfraBox Agent, AI-асистент що живе на сервері BananaPi M2 Berry під управлінням Armbian.

Твої можливості:
• Відповідати на запитання та допомагати з розробкою
• Аналізувати стан системи (CPU, RAM, диск, температура)
• Допомагати з файлами та командами
• Допомагати розвивати проєкт InfraBox

{f"Інформація про проєкт InfraBox:{chr(10)}{PROJECT_INFO}" if PROJECT_INFO else "Проєкт InfraBox: опис буде додано через /setproject"}

Правила:
• Відповідай українською, якщо не попросять іншою
• Будь стислим та конкретним
• Поточна дата/час береться з системи
• Не виконуй деструктивних дій без явного підтвердження

ВАЖЛИВО — виконання команд:
Коли пропонуєш виконати щось на сервері, ЗАВЖДИ оформлюй команди у блок ```bash
Це дозволяє системі автоматично запропонувати виконання одним натисканням кнопки.
Приклад оформлення:
```bash
git add .
git commit -m "update"
git push origin dev
```
Якщо команда деструктивна (rm, drop, delete тощо) — попередь окремо перед блоком.
"""

# ── Авторизація ─────────────────────────────────────────────────────────────


def authorized(func):
    """Декоратор: перевірка чи дозволений chat_id"""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if ALLOWED_CHAT_IDS and update.effective_chat.id not in ALLOWED_CHAT_IDS:
            log.warning(
                f"Відхилено доступ для chat_id={update.effective_chat.id}")
            return
        return await func(update, context)
    return wrapper

# ── Системний моніторинг ────────────────────────────────────────────────────


def get_system_info() -> dict:
    cpu = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    temp = "N/A"
    for path in [
        "/sys/class/thermal/thermal_zone0/temp",
        "/sys/devices/virtual/thermal/thermal_zone0/temp",
    ]:
        try:
            temp = f"{int(Path(path).read_text()) / 1000:.1f}°C"
            break
        except Exception:
            pass

    boot = datetime.fromtimestamp(psutil.boot_time())
    uptime = str(datetime.now() - boot).split(".")[0]

    return {
        "cpu": f"{cpu:.1f}%",
        "ram_used": f"{mem.used // 1_048_576} MB",
        "ram_total": f"{mem.total // 1_048_576} MB",
        "ram_pct": f"{mem.percent}%",
        "disk_used": f"{disk.used // 1_073_741_824:.1f} GB",
        "disk_total": f"{disk.total // 1_073_741_824:.1f} GB",
        "disk_pct": f"{disk.percent}%",
        "temp": temp,
        "uptime": uptime,
        "time": datetime.now().strftime("%H:%M:%S %d.%m.%Y"),
    }


def format_status(info: dict) -> str:
    return (
        f"🖥 *Стан BananaPi M2 Berry*\n\n"
        f"🕐 `{info['time']}`\n"
        f"⏳ Аптайм: `{info['uptime']}`\n\n"
        f"⚡ CPU: `{info['cpu']}`\n"
        f"🧠 RAM: `{info['ram_used']} / {info['ram_total']}` ({info['ram_pct']})\n"
        f"💾 Диск: `{info['disk_used']} / {info['disk_total']}` ({info['disk_pct']})\n"
        f"🌡 Температура: `{info['temp']}`"
    )

# ── Claude: надіслати повідомлення ──────────────────────────────────────────


async def ask_claude(chat_id: int, user_text: str, extra_ctx: str = "") -> str:
    if chat_id not in history:
        history[chat_id] = []

    content = user_text
    if extra_ctx:
        content += f"\n\n[Контекст системи]\n{extra_ctx}"

    history[chat_id].append({"role": "user", "content": content})

    # Зберігаємо лише останні 20 повідомлень (економія RAM)
    if len(history[chat_id]) > 20:
        history[chat_id] = history[chat_id][-20:]

    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(
        None,
        lambda: claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            messages=history[chat_id],
        ),
    )

    reply = response.content[0].text
    history[chat_id].append({"role": "assistant", "content": reply})
    return reply


async def send_long(update: Update, text: str, parse_mode: str = "Markdown",
                    reply_markup=None):
    """Надіслати довге повідомлення частинами"""
    chunks = [text[i: i + 4000] for i in range(0, len(text), 4000)]
    for i, chunk in enumerate(chunks):
        # Клавіатуру додаємо лише до останнього шматка
        markup = reply_markup if i == len(chunks) - 1 else None
        try:
            await update.message.reply_text(chunk, parse_mode=parse_mode,
                                            reply_markup=markup)
        except Exception:
            await update.message.reply_text(chunk, reply_markup=markup)


def extract_bash_commands(text: str) -> list[str]:
    """Витягнути команди з ```bash блоків відповіді Claude"""
    blocks = re.findall(r"```(?:bash|sh|shell)\n(.*?)```", text, re.DOTALL)
    commands = []
    for block in blocks:
        lines = [l.strip() for l in block.strip().splitlines() if l.strip()
                 and not l.strip().startswith("#")]
        if lines:
            commands.append("\n".join(lines))
    return commands

# ── Команди ─────────────────────────────────────────────────────────────────


@authorized
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    history[chat_id] = []
    await update.message.reply_text(
        "🤖 *InfraBox Agent* активний!\n\n"
        "*Команди:*\n"
        "/status — стан системи\n"
        "/run `<cmd>` — виконати команду\n"
        "/ls `[шлях]` — список файлів\n"
        "/df — місце на дисках\n"
        "/ps — процеси (топ 10)\n"
        "/setproject — задати опис проєкту\n"
        "/clear — очистити контекст розмови\n"
        "/help — ця довідка\n\n"
        "💬 Або просто напишіть — відповім!",
        parse_mode="Markdown",
    )


@authorized
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


@authorized
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    info = get_system_info()
    await update.message.reply_text(format_status(info), parse_mode="Markdown")


@authorized
async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Використання: `/run <команда>`", parse_mode="Markdown")
        return

    cmd = " ".join(context.args)
    log.info(f"[RUN] chat={update.effective_chat.id} cmd={cmd!r}")
    await context.bot.send_chat_action(update.effective_chat.id, "typing")

    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=WORK_DIR,
        )
        output = (proc.stdout + proc.stderr).strip() or "(немає виводу)"
        if len(output) > 3800:
            output = output[:3800] + "\n…(обрізано)"
        await update.message.reply_text(f"```\n{output}\n```", parse_mode="Markdown")
    except subprocess.TimeoutExpired:
        await update.message.reply_text("⏰ Час очікування вийшов (60с)")
    except Exception as e:
        await update.message.reply_text(f"❌ Помилка: `{e}`", parse_mode="Markdown")


@authorized
async def cmd_ls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    path = " ".join(context.args) if context.args else WORK_DIR
    try:
        p = Path(path).expanduser().resolve()
        items = sorted(p.iterdir(), key=lambda x: (
            not x.is_dir(), x.name.lower()))
        lines = []
        for item in items[:60]:
            if item.is_dir():
                lines.append(f"📁 `{item.name}/`")
            else:
                size = item.stat().st_size
                size_str = f"{size // 1024}K" if size >= 1024 else f"{size}B"
                lines.append(f"📄 `{item.name}` _{size_str}_")
        if not lines:
            lines = ["_(порожня директорія)_"]
        text = f"📂 *{p}*\n\n" + "\n".join(lines)
        await update.message.reply_text(text, parse_mode="Markdown")
    except PermissionError:
        await update.message.reply_text("❌ Немає доступу")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")


@authorized
async def cmd_df(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        result = subprocess.run(["df", "-h"], capture_output=True, text=True)
        await update.message.reply_text(f"```\n{result.stdout}\n```", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")


@authorized
async def cmd_ps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    procs = []
    for p in sorted(psutil.process_iter(["pid", "name", "cpu_percent", "memory_info"]),
                    key=lambda x: x.info.get("cpu_percent", 0), reverse=True)[:10]:
        try:
            mem_mb = p.info["memory_info"].rss // 1_048_576
            procs.append(
                f"`{p.info['pid']:5}` {p.info['name'][:20]:<20} CPU:{p.info['cpu_percent']:5.1f}% RAM:{mem_mb}MB")
        except Exception:
            pass

    text = "⚙️ *Топ процесів (за CPU)*\n\n" + "\n".join(procs)
    await update.message.reply_text(text, parse_mode="Markdown")


@authorized
async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    history[update.effective_chat.id] = []
    await update.message.reply_text("🗑 Контекст розмови очищено.")


@authorized
async def cmd_setproject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Зберегти опис проєкту InfraBox у .env"""
    global SYSTEM_PROMPT, PROJECT_INFO
    if not context.args:
        await update.message.reply_text(
            "Надішліть опис проєкту:\n`/setproject <опис проєкту InfraBox>`",
            parse_mode="Markdown",
        )
        return

    PROJECT_INFO = " ".join(context.args)

    # Оновлюємо .env файл
    env_path = Path(WORK_DIR) / ".env"
    if env_path.exists():
        lines = env_path.read_text().splitlines()
        new_lines = [l for l in lines if not l.startswith("PROJECT_INFO=")]
    else:
        new_lines = []
    new_lines.append(f'PROJECT_INFO="{PROJECT_INFO}"')
    env_path.write_text("\n".join(new_lines) + "\n")

    # Оновлюємо системний промпт
    SYSTEM_PROMPT = SYSTEM_PROMPT.replace(
        "Проєкт InfraBox: опис буде додано через /setproject",
        f"Інформація про проєкт InfraBox:\n{PROJECT_INFO}",
    )

    # Очищаємо всі контексти, щоб новий промпт набрав чинності
    history.clear()

    await update.message.reply_text(
        f"✅ Опис проєкту збережено!\n\n_{PROJECT_INFO}_",
        parse_mode="Markdown",
    )

# ── Обробник кнопок підтвердження ────────────────────────────────────────────


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat.id
    await query.answer()

    if ALLOWED_CHAT_IDS and chat_id not in ALLOWED_CHAT_IDS:
        return

    if query.data == "exec_confirm":
        cmds = pending_commands.pop(chat_id, [])
        if not cmds:
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text("⚠️ Команди вже не актуальні.")
            return

        await query.edit_message_reply_markup(reply_markup=None)

        for cmd in cmds:
            await query.message.reply_text(f"⏳ Виконую:\n```\n{cmd}\n```",
                                           parse_mode="Markdown")
            log.info(f"[EXEC] chat={chat_id} cmd={cmd!r}")
            try:
                proc = subprocess.run(
                    cmd, shell=True, capture_output=True,
                    text=True, timeout=60, cwd=WORK_DIR,
                )
                output = (proc.stdout + proc.stderr).strip() or "(немає виводу)"
                if len(output) > 3800:
                    output = output[:3800] + "\n…(обрізано)"
                await query.message.reply_text(f"```\n{output}\n```",
                                               parse_mode="Markdown")
            except subprocess.TimeoutExpired:
                await query.message.reply_text("⏰ Час очікування вийшов (60с)")
            except Exception as e:
                await query.message.reply_text(f"❌ Помилка: `{e}`",
                                               parse_mode="Markdown")

    elif query.data == "exec_cancel":
        pending_commands.pop(chat_id, None)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("✗ Скасовано.")

# ── Основний обробник повідомлень ────────────────────────────────────────────


@authorized
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text or ""

    await context.bot.send_chat_action(chat_id, "typing")

    # Чи потрібно додати стан системи?
    sys_keywords = ("стан", "температур", "cpu", "ram", "диск", "пам'ять", "аптайм",
                    "навантаж", "system", "memory", "disk", "load", "status")
    include_sys = any(kw in text.lower() for kw in sys_keywords)
    extra = json.dumps(get_system_info(),
                       ensure_ascii=False) if include_sys else ""

    try:
        reply = await ask_claude(chat_id, text, extra)

        # Шукаємо bash-блоки у відповіді Claude
        commands = extract_bash_commands(reply)
        if commands:
            pending_commands[chat_id] = commands
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "▶ Виконати", callback_data="exec_confirm"),
                InlineKeyboardButton(
                    "✗ Скасувати", callback_data="exec_cancel"),
            ]])
            await send_long(update, reply, reply_markup=keyboard)
        else:
            await send_long(update, reply)

    except Exception as e:
        log.error(f"Claude error: {e}")
        await update.message.reply_text(f"❌ Помилка: `{e}`", parse_mode="Markdown")

# ── Scheduled: щоденний звіт ────────────────────────────────────────────────


async def daily_report(app: Application):
    if not ALLOWED_CHAT_IDS:
        return
    info = get_system_info()
    msg = f"📊 *Щоденний звіт*\n\n{format_status(info)}"
    for cid in ALLOWED_CHAT_IDS:
        try:
            await app.bot.send_message(cid, msg, parse_mode="Markdown")
        except Exception as e:
            log.warning(f"Не вдалося надіслати звіт {cid}: {e}")

# ── Запуск ───────────────────────────────────────────────────────────────────


def main():
    log.info("▶ InfraBox Agent запускається…")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Реєструємо команди
    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("help",       cmd_help))
    app.add_handler(CommandHandler("status",     cmd_status))
    app.add_handler(CommandHandler("run",        cmd_run))
    app.add_handler(CommandHandler("ls",         cmd_ls))
    app.add_handler(CommandHandler("df",         cmd_df))
    app.add_handler(CommandHandler("ps",         cmd_ps))
    app.add_handler(CommandHandler("clear",      cmd_clear))
    app.add_handler(CommandHandler("setproject", cmd_setproject))
    app.add_handler(CallbackQueryHandler(handle_callback, pattern="^exec_"))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, handle_message))

    # Планувальник: щоденний звіт о 09:00
    scheduler.add_job(
        daily_report,
        "cron", hour=9, minute=0,
        args=[app],
        id="daily_report",
    )

    # Планувальник: запускається після старту event loop
    async def on_startup(app: Application):
        scheduler.add_job(
            daily_report,
            "cron", hour=9, minute=0,
            args=[app],
            id="daily_report",
            replace_existing=True,
        )
        scheduler.start()
        log.info("✅ InfraBox Agent активний. Polling…")

    app.post_init = on_startup

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
