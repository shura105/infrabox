import hashlib
import json
import os
import re

import httpx
from fastapi import APIRouter, HTTPException, Depends, Request
from .auth_guard import require_admin
from .redis_client import redis_client

# ── Централізоване сховище екранів — ФАЙЛИ на core (майстер) ───────────────────
# Екрани статичні → лежать файлами на диску core, НЕ в RAM. Redis тримає лише
# checksum кожного екрана (ui:rev:*), а не вміст. ui-екземпляр на іншому вузлі
# (репліка) при зверненні звіряє локальний checksum із центральним і, якщо
# застарів, підтягує свіжий файл з core. Запис іде в центр (на репліці — forward).
#
#   CORE_URL порожній  → цей вузол МАЙСТЕР (core): працює з локальними файлами
#   CORE_URL заданий    → РЕПЛІКА: читає кеш (оновлює з core за version), пише через core
DATA_DIR     = "/app/data/screens"
PROJECT_FILE = "/app/data/project.json"
CORE_URL     = os.environ.get("CORE_URL", "").rstrip("/")
IS_MASTER    = not CORE_URL

router = APIRouter()


def _validate_path(path: str) -> None:
    for seg in path.split("/"):
        if not seg or not re.match(r"^[a-zA-Z0-9_-]+$", seg):
            raise HTTPException(status_code=400, detail=f"Invalid path segment: '{seg}'")


def _screen_file(path):
    return os.path.join(DATA_DIR, path, "screen.json")


def _md5_file(p):
    try:
        with open(p, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except OSError:
        return ""


def _md5_bytes(b):
    return hashlib.md5(b).hexdigest()


# ── Redis: лише checksum (version), не вміст ──────────────────────────────────
async def _rev_get(key):
    r = redis_client.redis
    if r is None:
        return ""
    v = await r.get(f"ui:rev:{key}")
    return (v.decode() if isinstance(v, bytes) else v) or ""


async def _rev_set(key, md5):
    r = redis_client.redis
    if r is not None:
        await r.set(f"ui:rev:{key}", md5)


# ── Репліка: підтягнути свіжий файл з центру, якщо локальний застарів ─────────
async def _ensure_fresh(rev_key, local_file, pub_url):
    if IS_MASTER:
        return
    if _md5_file(local_file) == await _rev_get(rev_key):
        return  # локальна версія актуальна
    try:
        async with httpx.AsyncClient(verify=False, timeout=10) as c:
            resp = await c.get(f"{CORE_URL}{pub_url}")
        if resp.status_code == 200:
            os.makedirs(os.path.dirname(local_file), exist_ok=True)
            with open(local_file, "wb") as f:
                f.write(resp.content)
    except Exception as e:
        print(f"⚠️  Не вдалося підтягнути {pub_url} з core: {e}")


async def _forward(method, api_path, data, request):
    """Репліка проксує запис у центр (core) з тим самим токеном адміна."""
    auth = request.headers.get("authorization", "")
    async with httpx.AsyncClient(verify=False, timeout=15) as c:
        return await c.request(method, f"{CORE_URL}{api_path}",
                               json=data, headers={"Authorization": auth})


# ── Майстер: ініціалізація checksum з файлів при старті ───────────────────────
async def init_screen_revs():
    """На майстрі: прибрати старий повний вміст із Redis (якщо лишився) і виставити
    ui:rev:* за наявними файлами, щоб репліки могли звіряти версію."""
    if not IS_MASTER:
        return
    r = redis_client.redis
    if r is None:
        return
    try:
        # прибрати застарілі ключі з повним вмістом (з попередньої версії)
        for pat in ("ui:project", "ui:screens", "ui:screen:*"):
            keys = await r.keys(pat)
            if keys:
                await r.delete(*keys)
        if os.path.exists(PROJECT_FILE):
            await _rev_set("project", _md5_file(PROJECT_FILE))
        if os.path.isdir(DATA_DIR):
            for root, _dirs, files in os.walk(DATA_DIR):
                if "screen.json" in files:
                    rel = os.path.relpath(root, DATA_DIR)
                    await _rev_set(f"screen:{rel}", _md5_file(os.path.join(root, "screen.json")))
        print("📐 Checksum екранів виставлено в Redis (ui:rev:*)")
    except Exception as e:
        print("⚠️  init_screen_revs:", e)


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


# ── project ───────────────────────────────────────────────────────────────────
def _read_project():
    if not os.path.exists(PROJECT_FILE):
        return {"screens": []}
    with open(PROJECT_FILE, encoding="utf-8") as f:
        return json.load(f)


@router.get("/api/pub/project")
async def pub_get_project():
    await _ensure_fresh("project", PROJECT_FILE, "/api/pub/project")
    return _read_project()


@router.get("/api/project")
async def get_project(_: dict = Depends(require_admin)):
    await _ensure_fresh("project", PROJECT_FILE, "/api/pub/project")
    return _read_project()


@router.put("/api/project")
async def put_project(data: dict, request: Request, _: dict = Depends(require_admin)):
    if not IS_MASTER:
        resp = await _forward("PUT", "/api/project", data, request)
        if resp.status_code != 200:
            raise HTTPException(resp.status_code, "core відхилив запис project")
        return {"ok": True}
    os.makedirs(os.path.dirname(PROJECT_FILE), exist_ok=True)
    raw = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
    with open(PROJECT_FILE, "wb") as f:
        f.write(raw)
    await _rev_set("project", _md5_bytes(raw))
    return {"ok": True}


# ── screens ───────────────────────────────────────────────────────────────────
def _read_screen(path):
    sf = _screen_file(path)
    if not os.path.exists(sf):
        raise HTTPException(status_code=404, detail="Screen not found")
    with open(sf, encoding="utf-8") as f:
        return json.load(f)


@router.get("/api/pub/screens/{screen_path:path}")
async def pub_get_screen(screen_path: str):
    _validate_path(screen_path)
    await _ensure_fresh(f"screen:{screen_path}", _screen_file(screen_path),
                        f"/api/pub/screens/{screen_path}")
    return _read_screen(screen_path)


@router.get("/api/screens/{screen_path:path}")
async def get_screen(screen_path: str, _: dict = Depends(require_admin)):
    _validate_path(screen_path)
    await _ensure_fresh(f"screen:{screen_path}", _screen_file(screen_path),
                        f"/api/pub/screens/{screen_path}")
    return _read_screen(screen_path)


@router.put("/api/screens/{screen_path:path}")
async def put_screen(screen_path: str, data: dict, request: Request, _: dict = Depends(require_admin)):
    _validate_path(screen_path)
    if not IS_MASTER:
        resp = await _forward("PUT", f"/api/screens/{screen_path}", data, request)
        if resp.status_code != 200:
            raise HTTPException(resp.status_code, "core відхилив запис екрана")
        return {"ok": True}
    sf = _screen_file(screen_path)
    os.makedirs(os.path.dirname(sf), exist_ok=True)
    raw = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
    with open(sf, "wb") as f:
        f.write(raw)
    await _rev_set(f"screen:{screen_path}", _md5_bytes(raw))
    await _sync_scheduler(screen_path, data.get("elements", []))
    return {"ok": True}


@router.delete("/api/screens/{screen_path:path}")
async def delete_screen(screen_path: str, request: Request, _: dict = Depends(require_admin)):
    _validate_path(screen_path)
    if not IS_MASTER:
        resp = await _forward("DELETE", f"/api/screens/{screen_path}", None, request)
        if resp.status_code != 200:
            raise HTTPException(resp.status_code, "core відхилив видалення екрана")
        return {"ok": True}
    import shutil
    screen_dir = os.path.join(DATA_DIR, screen_path)
    if not os.path.exists(screen_dir):
        raise HTTPException(status_code=404, detail="Screen not found")
    shutil.rmtree(screen_dir)
    parent = os.path.dirname(screen_dir)
    if parent != DATA_DIR and os.path.isdir(parent) and not os.listdir(parent):
        os.rmdir(parent)
    r = redis_client.redis
    if r is not None:
        await r.delete(f"ui:rev:screen:{screen_path}")
    await _sync_scheduler(screen_path, [])
    return {"ok": True}


# ── runtime schedule editor ──────────────────────────────────────────────────
@router.patch("/api/scheduler/{el_id}")
async def patch_scheduler(el_id: str, data: dict, request: Request, _: dict = Depends(require_admin)):
    new_schedule = data.get("schedule", [])
    screen_path  = data.get("screen", "")
    if not IS_MASTER:
        resp = await _forward("PATCH", f"/api/scheduler/{el_id}", data, request)
        if resp.status_code != 200:
            raise HTTPException(resp.status_code, "core відхилив зміну розкладу")
        return {"ok": True}
    r = redis_client.redis
    if r is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    key = f"scheduler:{el_id}"
    if await r.hgetall(key):
        await r.hset(key, "schedule", json.dumps(new_schedule, ensure_ascii=False))
    else:
        await r.hset(key, mapping={
            "schedule":  json.dumps(new_schedule, ensure_ascii=False),
            "point_id":  "0",
            "screen":    screen_path,
        })
    if screen_path:
        _validate_path(screen_path)
        sf = _screen_file(screen_path)
        if os.path.exists(sf):
            sd = _read_screen(screen_path)
            for el in sd.get("elements", []):
                if el.get("id") == el_id:
                    el["schedule"] = new_schedule
                    break
            raw = json.dumps(sd, indent=2, ensure_ascii=False).encode("utf-8")
            with open(sf, "wb") as f:
                f.write(raw)
            await _rev_set(f"screen:{screen_path}", _md5_bytes(raw))
    return {"ok": True}
