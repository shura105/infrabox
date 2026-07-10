"""
store.py — робота з проєктами пристроїв на диску.

Кожен пристрій — окрема папка проєкту:
    <PROJECTS_DIR>/<device-id>/device.json   — метадані пристрою
    (пізніше: description.md, scheme.svg, firmware.ino …)

device-id формується зі слага назви (лише латиниця).
"""

import json
import os
import re
import shutil
import time

PROJECTS_DIR = os.environ.get("PROJECTS_DIR", "/app/projects")
POINTS_PATH  = os.environ.get("POINTS_PATH", "/app/points/points.json")


class ValidationError(Exception):
    """Некоректний ввід користувача (→ HTTP 400)."""


class ConflictError(Exception):
    """Пристрій із таким id уже існує (→ HTTP 409)."""


# ── назви / слаги ─────────────────────────────────────────────────────────────
# Назва пристрою — лише латинські літери, цифри, пробіл, дефіс, підкреслення.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]*$")


def _slugify(name: str) -> str:
    s = name.strip().lower()
    s = re.sub(r"[ _]+", "-", s)       # пробіли/підкреслення → дефіс
    s = re.sub(r"[^a-z0-9-]", "", s)   # лишити тільки [a-z0-9-]
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s


def validate_name(name: str) -> str:
    """Перевіряє назву, повертає id (slug)."""
    name = (name or "").strip()
    if not name:
        raise ValidationError("Вкажіть назву пристрою")
    if not _NAME_RE.match(name):
        raise ValidationError("Лише латинські літери, цифри, пробіл, - та _")
    slug = _slugify(name)
    if not slug:
        raise ValidationError("Некоректна назва")
    return slug


def _safe_id(device_id: str) -> bool:
    """Захист від path traversal — id має бути чистим слагом."""
    return bool(device_id) and re.fullmatch(r"[a-z0-9-]+", device_id) is not None


# ── файлові операції ──────────────────────────────────────────────────────────
def ensure_dirs():
    os.makedirs(PROJECTS_DIR, exist_ok=True)


def _project_dir(device_id: str) -> str:
    return os.path.join(PROJECTS_DIR, device_id)


def _device_json(device_id: str) -> str:
    return os.path.join(_project_dir(device_id), "device.json")


def list_devices() -> list[dict]:
    ensure_dirs()
    out = []
    for entry in sorted(os.listdir(PROJECTS_DIR)):
        path = os.path.join(PROJECTS_DIR, entry, "device.json")
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    out.append(json.load(f))
            except Exception:
                continue  # пропускаємо биті проєкти
    return out


def get_device(device_id: str) -> dict | None:
    if not _safe_id(device_id):
        return None
    path = _device_json(device_id)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def create_device(name: str) -> dict:
    ensure_dirs()
    device_id = validate_name(name)
    folder = _project_dir(device_id)
    if os.path.exists(folder):
        raise ConflictError(f"Пристрій «{device_id}» вже існує")

    os.makedirs(folder)
    device = {
        "id": device_id,
        "name": name.strip(),
        "created_at": int(time.time()),
    }
    with open(_device_json(device_id), "w", encoding="utf-8") as f:
        json.dump(device, f, ensure_ascii=False, indent=2)
    return device


def delete_device(device_id: str) -> bool:
    if not _safe_id(device_id):
        return False
    folder = _project_dir(device_id)
    if not os.path.isdir(folder):
        return False
    shutil.rmtree(folder)
    return True


def update_description(device_id: str, text: str) -> dict | None:
    """Записує опис у device.json. Повертає оновлений пристрій або None."""
    dev = get_device(device_id)
    if dev is None:
        return None
    dev["description"] = text if text is not None else ""
    with open(_device_json(device_id), "w", encoding="utf-8") as f:
        json.dump(dev, f, ensure_ascii=False, indent=2)
    return dev


def update_hardware(device_id: str, hardware_id) -> dict | None:
    """Записує обране апаратне забезпечення (id запису з бази знань)."""
    dev = get_device(device_id)
    if dev is None:
        return None
    if hardware_id:
        dev["hardware"] = hardware_id
    else:
        dev.pop("hardware", None)
    with open(_device_json(device_id), "w", encoding="utf-8") as f:
        json.dump(dev, f, ensure_ascii=False, indent=2)
    return dev


def update_code(device_id: str, code) -> dict | None:
    """Зберігає основний код пристрою (складений скетч)."""
    dev = get_device(device_id)
    if dev is None:
        return None
    dev["code"] = str(code or "")
    with open(_device_json(device_id), "w", encoding="utf-8") as f:
        json.dump(dev, f, ensure_ascii=False, indent=2)
    return dev


CODE_SECTION_IDS = ["includes", "config", "globals", "functions", "setup", "loop"]


def update_code_blocks(device_id: str, blocks) -> dict | None:
    """Користувацькі блоки коду: [{name, sections:{includes,config,…}}]."""
    dev = get_device(device_id)
    if dev is None:
        return None
    clean = []
    for b in (blocks or []):
        if not isinstance(b, dict):
            continue
        sections = b.get("sections") if isinstance(b.get("sections"), dict) else {}
        clean.append({
            "name": str(b.get("name", "")).strip(),
            "sections": {sid: str(sections.get(sid, "")) for sid in CODE_SECTION_IDS},
        })
    dev["code_blocks"] = clean
    with open(_device_json(device_id), "w", encoding="utf-8") as f:
        json.dump(dev, f, ensure_ascii=False, indent=2)
    return dev


def update_scheme_image(device_id: str, image) -> dict | None:
    """Обране зображення схеми (ім'я файлу з images БЗ-контролера)."""
    dev = get_device(device_id)
    if dev is None:
        return None
    if image:
        dev["scheme_image"] = image
    else:
        dev.pop("scheme_image", None)
    with open(_device_json(device_id), "w", encoding="utf-8") as f:
        json.dump(dev, f, ensure_ascii=False, indent=2)
    return dev


def update_pins(device_id: str, pins) -> dict | None:
    """Записує таблицю задіяних портів. Нормалізує рядки до відомих полів."""
    dev = get_device(device_id)
    if dev is None:
        return None
    clean = []
    for p in (pins or []):
        if not isinstance(p, dict):
            continue
        clean.append({
            "pin":      str(p.get("pin", "")).strip(),
            "function": str(p.get("function", "")).strip(),
            "purpose":  str(p.get("purpose", "")).strip(),
            "mode":     str(p.get("mode", "")).strip(),
        })
    dev["pins"] = clean
    with open(_device_json(device_id), "w", encoding="utf-8") as f:
        json.dump(dev, f, ensure_ascii=False, indent=2)
    return dev


def list_points() -> list[dict]:
    """Точки Infrabox (з points.json) — для прив'язки сигналів."""
    try:
        with open(POINTS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def update_program(device_id: str, program) -> dict | None:
    """Записує конфігурацію формування коду у device.json (об'єкт program)."""
    dev = get_device(device_id)
    if dev is None:
        return None
    program = program or {}
    wifi = program.get("wifi") or {}
    mqtt = program.get("mqtt") or {}

    signals = []
    for s in (program.get("signals") or []):
        if not isinstance(s, dict):
            continue
        try:
            pid = int(s.get("point_id"))
        except (TypeError, ValueError):
            pid = None
        try:
            interval = int(s.get("interval_ms") or 1000)
        except (TypeError, ValueError):
            interval = 1000
        signals.append({
            "type":        str(s.get("type", "")).strip() or "discrete_in",
            "pin":         str(s.get("pin", "")).strip(),
            "point_id":    pid,
            "poll":        str(s.get("poll", "timer")).strip() or "timer",
            "interval_ms": interval,
        })

    # додатковий функціонал: піни для ініціалізації, логіку користувач дописує сам
    custom = []
    for c in (program.get("custom") or []):
        if not isinstance(c, dict):
            continue
        cpins = []
        for p in (c.get("pins") or []):
            if not isinstance(p, dict):
                continue
            cpins.append({
                "pin":     str(p.get("pin", "")).strip(),
                "comment": str(p.get("comment", "")).strip(),
            })
        custom.append({"name": str(c.get("name", "")).strip(), "pins": cpins})

    try:
        port = int(mqtt.get("port") or 1883)
    except (TypeError, ValueError):
        port = 1883

    connection = str(program.get("connection", "wifi")).strip() or "wifi"
    # OTA можливе лише через WiFi
    ota = bool(program.get("ota", False)) and connection == "wifi"

    dev["program"] = {
        "purpose":    str(program.get("purpose", "")).strip(),
        "connection": connection,
        "ota":        ota,
        "wifi":       {"ssid": str(wifi.get("ssid", "")), "pass": str(wifi.get("pass", ""))},
        "mqtt":    {
            "host":      str(mqtt.get("host", "")).strip(),
            "port":      port,
            "client_id": str(mqtt.get("client_id", "")).strip(),
        },
        "signals": signals,
        "custom":  custom,
    }
    with open(_device_json(device_id), "w", encoding="utf-8") as f:
        json.dump(dev, f, ensure_ascii=False, indent=2)
    return dev
