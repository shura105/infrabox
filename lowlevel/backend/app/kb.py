"""
kb.py — база знань обладнання.

Кожен запис обладнання — окрема папка:
    <KNOWLEDGE_DIR>/<id>/equipment.json   — тип, додаткова інформація, ім'я файлу схеми
    <KNOWLEDGE_DIR>/<id>/scheme.<ext>      — зображення схеми (опційно)
"""

import json
import os
import re
import shutil
import time
import uuid

from app.store import ValidationError

KNOWLEDGE_DIR = os.environ.get("KNOWLEDGE_DIR", "/app/knowledge")

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp"}


def ensure_dirs():
    os.makedirs(KNOWLEDGE_DIR, exist_ok=True)


def _slug(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[ _]+", "-", s)
    s = re.sub(r"[^a-z0-9-]", "", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s


def _safe_id(eid: str) -> bool:
    return bool(eid) and re.fullmatch(r"[a-z0-9-]+", eid) is not None


def _dir(eid: str) -> str:
    return os.path.join(KNOWLEDGE_DIR, eid)


def _json(eid: str) -> str:
    return os.path.join(_dir(eid), "equipment.json")


def _new_id(type_: str) -> str:
    base = _slug(type_) or ("eq-" + uuid.uuid4().hex[:8])
    eid, n = base, 2
    while os.path.exists(_dir(eid)):
        eid = f"{base}-{n}"
        n += 1
    return eid


def _save(rec: dict):
    with open(_json(rec["id"]), "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)


# ── CRUD ──────────────────────────────────────────────────────────────────────
def _migrate(rec: dict, eid: str) -> dict:
    """Legacy: одиничне поле `scheme` → список `images` (файл у підпапку images/)."""
    images = rec.get("images") or []
    old = rec.pop("scheme", None)
    if old:
        old_path = os.path.join(_dir(eid), old)
        if os.path.isfile(old_path):
            ext = os.path.splitext(old)[1].lower()
            idir = _images_dir(eid)
            os.makedirs(idir, exist_ok=True)
            name = f"img-{uuid.uuid4().hex[:8]}{ext}"
            shutil.move(old_path, os.path.join(idir, name))
            images.append(name)
    rec["images"] = images
    _save(rec)
    return rec


def list_equipment() -> list[dict]:
    ensure_dirs()
    out = []
    for entry in sorted(os.listdir(KNOWLEDGE_DIR)):
        if os.path.isfile(_json(entry)):
            rec = get_equipment(entry)
            if rec:
                out.append(rec)
    return out


def get_equipment(eid: str) -> dict | None:
    if not _safe_id(eid):
        return None
    path = _json(eid)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        rec = json.load(f)
    if "scheme" in rec:            # legacy-запис → мігрувати
        rec = _migrate(rec, eid)
    return rec


def create_equipment(type_: str, info: str = "") -> dict:
    ensure_dirs()
    type_ = (type_ or "").strip()
    if not type_:
        raise ValidationError("Вкажіть тип обладнання")
    eid = _new_id(type_)
    os.makedirs(_dir(eid))
    rec = {
        "id": eid,
        "type": type_,
        "platform": "",
        "info": info or "",
        "images": [],
        "pins": [],
        "created_at": int(time.time()),
    }
    _save(rec)
    return rec


def _clean_pins(pins) -> list[dict]:
    out = []
    for p in (pins or []):
        if not isinstance(p, dict):
            continue
        out.append({
            "pin":      str(p.get("pin", "")).strip(),
            "function": str(p.get("function", "")).strip(),
        })
    return out


def update_equipment(eid: str, type_=None, info=None, pins=None, platform=None) -> dict | None:
    rec = get_equipment(eid)
    if rec is None:
        return None
    if type_ is not None:
        t = (type_ or "").strip()
        if not t:
            raise ValidationError("Вкажіть тип обладнання")
        rec["type"] = t
    if platform is not None:
        rec["platform"] = str(platform).strip()
    if info is not None:
        rec["info"] = info
    if pins is not None:
        rec["pins"] = _clean_pins(pins)
    _save(rec)
    return rec


def delete_equipment(eid: str) -> bool:
    if not _safe_id(eid):
        return False
    folder = _dir(eid)
    if not os.path.isdir(folder):
        return False
    shutil.rmtree(folder)
    return True


# ── зображення (кілька на одиницю обладнання) ─────────────────────────────────
def _images_dir(eid: str) -> str:
    return os.path.join(_dir(eid), "images")


def _safe_name(name: str) -> bool:
    return bool(name) and os.path.basename(name) == name and "/" not in name and ".." not in name


def add_image(eid: str, filename: str, data: bytes) -> dict | None:
    rec = get_equipment(eid)
    if rec is None:
        return None
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in IMAGE_EXT:
        raise ValidationError("Непідтримуваний формат (png, jpg, svg, gif, webp)")
    idir = _images_dir(eid)
    os.makedirs(idir, exist_ok=True)
    name = f"img-{uuid.uuid4().hex[:8]}{ext}"
    with open(os.path.join(idir, name), "wb") as f:
        f.write(data)
    rec.setdefault("images", [])
    rec["images"].append(name)
    _save(rec)
    return rec


def image_path(eid: str, name: str) -> str | None:
    if not _safe_id(eid) or not _safe_name(name):
        return None
    rec = get_equipment(eid)
    if not rec or name not in (rec.get("images") or []):
        return None
    path = os.path.join(_images_dir(eid), name)
    return path if os.path.isfile(path) else None


def delete_image(eid: str, name: str) -> dict | None:
    if not _safe_name(name):
        return None
    rec = get_equipment(eid)
    if rec is None or name not in (rec.get("images") or []):
        return None
    path = os.path.join(_images_dir(eid), name)
    if os.path.isfile(path):
        os.remove(path)
    rec["images"] = [x for x in rec["images"] if x != name]
    _save(rec)
    return rec
