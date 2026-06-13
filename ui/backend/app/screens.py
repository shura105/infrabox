import json
import os
import re

from fastapi import APIRouter, HTTPException, Depends
from .auth_guard import require_admin
from .redis_client import redis_client

# ── Єдине джерело істини — Redis на core ──────────────────────────────────────
# Екрани і проєкт зберігаються в Redis (а не у файлах /app/data), щоб кілька
# ui-екземплярів на різних вузлах бачили ОДНІ Й ТІ САМІ дані.
#   ui:project            STRING  JSON проєкту (список екранів)
#   ui:screen:<path>      STRING  JSON екрана (вміст, разом із bgImage)
#   ui:screens            SET     перелік шляхів екранів
PROJECT_KEY = "ui:project"
SCREENS_SET = "ui:screens"
def _skey(path):
    return f"ui:screen:{path}"

router = APIRouter()


def _validate_path(path: str) -> None:
    for seg in path.split("/"):
        if not seg or not re.match(r"^[a-zA-Z0-9_-]+$", seg):
            raise HTTPException(status_code=400, detail=f"Invalid path segment: '{seg}'")


def _r():
    r = redis_client.redis
    if r is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    return r


async def _get_json(key, default=None):
    raw = await _r().get(key)
    return default if raw is None else json.loads(raw)


async def _put_json(key, data):
    await _r().set(key, json.dumps(data, ensure_ascii=False))


# ── Міграція файли → Redis (одноразово, якщо Redis порожній) ──────────────────
async def migrate_files_to_redis():
    """Завантажує наявні project.json / screens/*/screen.json у Redis при першому
    старті (коли ui:project ще не існує). Зберігає дані старих інсталяцій."""
    r = redis_client.redis
    if r is None or await r.exists(PROJECT_KEY):
        return
    data_dir = "/app/data"
    pf = os.path.join(data_dir, "project.json")
    sd = os.path.join(data_dir, "screens")
    migrated = 0
    try:
        if os.path.exists(pf):
            with open(pf, encoding="utf-8") as f:
                await _put_json(PROJECT_KEY, json.load(f))
            migrated += 1
        if os.path.isdir(sd):
            for root, _dirs, files in os.walk(sd):
                if "screen.json" in files:
                    path = os.path.relpath(root, sd)
                    with open(os.path.join(root, "screen.json"), encoding="utf-8") as f:
                        await _put_json(_skey(path), json.load(f))
                    await r.sadd(SCREENS_SET, path)
                    migrated += 1
        if migrated:
            print(f"📦 Екрани мігровано у Redis: {migrated} об'єктів")
    except Exception as e:
        print("⚠️  Помилка міграції екранів у Redis:", e)


# ── scheduler sync (multi_timer → Redis для серверного виконання) ─────────────
async def _sync_scheduler(screen_path: str, elements: list):
    r = redis_client.redis
    if r is None:
        return
    idx_key = f"scheduler_screen:{screen_path}"
    old_ids = await r.smembers(idx_key)
    if old_ids:
        pipe = r.pipeline()
        for raw in old_ids:
            eid = raw.decode() if isinstance(raw, bytes) else raw
            pipe.delete(f"scheduler:{eid}")
        pipe.delete(idx_key)
        await pipe.execute()
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
        pipe.hset(f"scheduler:{el['id']}", mapping={
            "point_id": str(int(el["point_id"])),
            "schedule":  json.dumps(el["schedule"]),
            "screen":    screen_path,
        })
        pipe.sadd(idx_key, el["id"])
    await pipe.execute()


# ── runtime schedule editor ──────────────────────────────────────────────────
@router.patch("/api/scheduler/{el_id}")
async def patch_scheduler(el_id: str, data: dict, _: dict = Depends(require_admin)):
    new_schedule = data.get("schedule", [])
    screen_path  = data.get("screen", "")
    r = _r()
    key = f"scheduler:{el_id}"
    if await r.hgetall(key):
        await r.hset(key, "schedule", json.dumps(new_schedule, ensure_ascii=False))
    else:
        await r.hset(key, mapping={
            "schedule":  json.dumps(new_schedule, ensure_ascii=False),
            "point_id":  "0",
            "screen":    screen_path,
        })
    # persist у screen (Redis), щоб зміна пережила перезапуск scheduler
    if screen_path:
        _validate_path(screen_path)
        sd = await _get_json(_skey(screen_path))
        if sd:
            for el in sd.get("elements", []):
                if el.get("id") == el_id:
                    el["schedule"] = new_schedule
                    break
            await _put_json(_skey(screen_path), sd)
    return {"ok": True}


# ── public read-only (no auth) ────────────────────────────────────────────────
@router.get("/api/pub/project")
async def pub_get_project():
    return await _get_json(PROJECT_KEY, {"screens": []})


@router.get("/api/pub/screens/{screen_path:path}")
async def pub_get_screen(screen_path: str):
    _validate_path(screen_path)
    d = await _get_json(_skey(screen_path))
    if d is None:
        raise HTTPException(status_code=404, detail="Screen not found")
    return d


# ── project ───────────────────────────────────────────────────────────────────
@router.get("/api/project")
async def get_project(_: dict = Depends(require_admin)):
    return await _get_json(PROJECT_KEY, {"screens": []})


@router.put("/api/project")
async def put_project(data: dict, _: dict = Depends(require_admin)):
    await _put_json(PROJECT_KEY, data)
    return {"ok": True}


# ── screens ───────────────────────────────────────────────────────────────────
@router.get("/api/screens/{screen_path:path}")
async def get_screen(screen_path: str, _: dict = Depends(require_admin)):
    _validate_path(screen_path)
    d = await _get_json(_skey(screen_path))
    if d is None:
        raise HTTPException(status_code=404, detail="Screen not found")
    return d


@router.put("/api/screens/{screen_path:path}")
async def put_screen(screen_path: str, data: dict, _: dict = Depends(require_admin)):
    _validate_path(screen_path)
    await _put_json(_skey(screen_path), data)
    await _r().sadd(SCREENS_SET, screen_path)
    # bgImage зберігається всередині data (фронт читає sc.bgImage) — окремий файл не потрібен
    await _sync_scheduler(screen_path, data.get("elements", []))
    return {"ok": True}


@router.delete("/api/screens/{screen_path:path}")
async def delete_screen(screen_path: str, _: dict = Depends(require_admin)):
    _validate_path(screen_path)
    r = _r()
    if not await r.delete(_skey(screen_path)):
        raise HTTPException(status_code=404, detail="Screen not found")
    await r.srem(SCREENS_SET, screen_path)
    await _sync_scheduler(screen_path, [])
    return {"ok": True}
