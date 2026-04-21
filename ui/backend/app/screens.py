import base64
import json
import os
import re
import shutil

from fastapi import APIRouter, HTTPException, Depends
from .auth_guard import require_admin

DATA_DIR     = "/app/data/screens"
PROJECT_FILE = "/app/data/project.json"

router = APIRouter()


def _validate_path(path: str) -> None:
    for seg in path.split("/"):
        if not seg or not re.match(r"^[a-zA-Z0-9_-]+$", seg):
            raise HTTPException(status_code=400, detail=f"Invalid path segment: '{seg}'")


# ── public read-only (no auth) ────────────────────────────────────────────────

@router.get("/api/pub/project")
async def pub_get_project():
    if not os.path.exists(PROJECT_FILE):
        return {"screens": []}
    with open(PROJECT_FILE, encoding="utf-8") as f:
        return json.load(f)


@router.get("/api/pub/screens/{screen_path:path}")
async def pub_get_screen(screen_path: str):
    _validate_path(screen_path)
    path = os.path.join(DATA_DIR, screen_path, "screen.json")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Screen not found")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── project ───────────────────────────────────────────────────────────────────

@router.get("/api/project")
async def get_project(_: dict = Depends(require_admin)):
    if not os.path.exists(PROJECT_FILE):
        return {"screens": []}
    with open(PROJECT_FILE, encoding="utf-8") as f:
        return json.load(f)


@router.put("/api/project")
async def put_project(data: dict, _: dict = Depends(require_admin)):
    os.makedirs(os.path.dirname(PROJECT_FILE), exist_ok=True)
    with open(PROJECT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return {"ok": True}


# ── screens ───────────────────────────────────────────────────────────────────

@router.get("/api/screens/{screen_path:path}")
async def get_screen(screen_path: str, _: dict = Depends(require_admin)):
    _validate_path(screen_path)
    path = os.path.join(DATA_DIR, screen_path, "screen.json")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Screen not found")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@router.put("/api/screens/{screen_path:path}")
async def put_screen(screen_path: str, data: dict, _: dict = Depends(require_admin)):
    _validate_path(screen_path)
    screen_dir = os.path.join(DATA_DIR, screen_path)
    os.makedirs(screen_dir, exist_ok=True)
    bg_dir = os.path.join(screen_dir, "background")
    os.makedirs(bg_dir, exist_ok=True)
    # якщо bgImage — SVG у base64, зберігаємо як background/bg.svg
    bg_image = data.get("screen", {}).get("bgImage", "")
    prefix = "data:image/svg+xml;base64,"
    if bg_image.startswith(prefix):
        svg_bytes = base64.b64decode(bg_image[len(prefix):])
        with open(os.path.join(bg_dir, "bg.svg"), "wb") as f:
            f.write(svg_bytes)
    with open(os.path.join(screen_dir, "screen.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return {"ok": True}


@router.delete("/api/screens/{screen_path:path}")
async def delete_screen(screen_path: str, _: dict = Depends(require_admin)):
    _validate_path(screen_path)
    screen_dir = os.path.join(DATA_DIR, screen_path)
    if not os.path.exists(screen_dir):
        raise HTTPException(status_code=404, detail="Screen not found")
    shutil.rmtree(screen_dir)
    # прибираємо порожню батьківську папку (namespace кореневого екрана)
    parent = os.path.dirname(screen_dir)
    if parent != DATA_DIR and os.path.isdir(parent) and not os.listdir(parent):
        os.rmdir(parent)
    return {"ok": True}
