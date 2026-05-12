import base64
import json
import os
import re
import shutil

from fastapi import APIRouter, HTTPException, Depends
from .auth_guard import require_admin
from .redis_client import redis_client


async def _sync_scheduler(screen_path: str, elements: list):
    """Sync multi_timer schedules to Redis for server-side (core) execution.

    Called on every screen save and on screen delete (elements=[]).
    Keys written:
      scheduler:{el_id}  → HASH  point_id, schedule (JSON), screen
      scheduler_screen:{screen_path}  → SET of el_ids for this screen
    """
    r = redis_client.redis
    if r is None:
        return

    idx_key = f"scheduler_screen:{screen_path}"

    # remove old entries for this screen
    old_ids = await r.smembers(idx_key)
    if old_ids:
        pipe = r.pipeline()
        for raw in old_ids:
            eid = raw.decode() if isinstance(raw, bytes) else raw
            pipe.delete(f"scheduler:{eid}")
        pipe.delete(idx_key)
        await pipe.execute()

    # build new entries
    new_timers = [
        el for el in elements
        if el.get("type") == "multi_timer"
        and int(el.get("point_id") or 0) > 0
        and el.get("schedule")
    ]
    if not new_timers:
        return

    pipe = r.pipeline()
    for el in new_timers:
        el_id = el["id"]
        pipe.hset(f"scheduler:{el_id}", mapping={
            "point_id": str(int(el["point_id"])),
            "schedule":  json.dumps(el["schedule"]),
            "screen":    screen_path,
        })
        pipe.sadd(idx_key, el_id)
    await pipe.execute()

DATA_DIR     = "/app/data/screens"
PROJECT_FILE = "/app/data/project.json"

router = APIRouter()


def _validate_path(path: str) -> None:
    for seg in path.split("/"):
        if not seg or not re.match(r"^[a-zA-Z0-9_-]+$", seg):
            raise HTTPException(status_code=400, detail=f"Invalid path segment: '{seg}'")


# ── runtime schedule editor ──────────────────────────────────────────────────

@router.patch("/api/scheduler/{el_id}")
async def patch_scheduler(el_id: str, data: dict, _: dict = Depends(require_admin)):
    """Update a multi_timer schedule at runtime.
    Writes to Redis (scheduler picks up within 60 s) and persists to screen.json.
    Body: { "schedule": [...], "screen": "<screen_path>" }
    """
    new_schedule = data.get("schedule", [])
    screen_path  = data.get("screen", "")

    r = redis_client.redis
    if r is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    # update Redis entry if it exists
    key = f"scheduler:{el_id}"
    existing = await r.hgetall(key)
    if existing:
        await r.hset(key, "schedule", json.dumps(new_schedule, ensure_ascii=False))
    else:
        # element not registered (no point_id bound) — create minimal entry
        await r.hset(key, mapping={
            "schedule":  json.dumps(new_schedule, ensure_ascii=False),
            "point_id":  "0",
            "screen":    screen_path,
        })

    # persist to screen.json so change survives Redis restart
    if screen_path:
        _validate_path(screen_path)
        screen_file = os.path.join(DATA_DIR, screen_path, "screen.json")
        if os.path.exists(screen_file):
            with open(screen_file, encoding="utf-8") as f:
                screen_data = json.load(f)
            for el in screen_data.get("elements", []):
                if el.get("id") == el_id:
                    el["schedule"] = new_schedule
                    break
            tmp = screen_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(screen_data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, screen_file)

    return {"ok": True}


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
    # sync multi_timer schedules to Redis for server-side execution
    await _sync_scheduler(screen_path, data.get("elements", []))
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
    # cleanup scheduler entries for deleted screen
    await _sync_scheduler(screen_path, [])
    return {"ok": True}
