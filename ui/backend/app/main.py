import threading
import time

from fastapi import FastAPI, Depends, HTTPException
from app.redis_client import redis_client
from app.screens import router as screens_router
from app.ws import router as ws_router
from app.simulator import router as simulator_router
from app.auth_guard import require_auth

app = FastAPI()
app.include_router(screens_router)
app.include_router(ws_router)
app.include_router(simulator_router)


def _heartbeat_thread():
    import redis as redis_sync, os
    r = None
    while True:
        try:
            if r is None:
                r = redis_sync.Redis(
                    host=os.environ.get("REDIS_HOST", "infrabox-redis"),
                    port=int(os.environ.get("REDIS_PORT", 6379)),
                    decode_responses=True
                )
            r.set("heartbeat:infrabox-backend", int(time.time()), ex=25)
        except Exception:
            r = None
        time.sleep(5)


@app.on_event("startup")
async def startup():
    print("🔌 Connecting to Redis...")

    try:
        await redis_client.connect()
        print("✅ Redis connected")

        data = await redis_client.get_all_points()
        print(f"📊 Points loaded: {len(data)}")

    except Exception as e:
        print("❌ Redis error:", e)

    threading.Thread(target=_heartbeat_thread, daemon=True).start()


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/objects")
async def get_objects(_: dict = Depends(require_auth)):
    data = await redis_client.get_all_points()
    objects = sorted({p["object"] for p in data if p.get("object")})
    return {"objects": objects}


@app.get("/api/redis/structure")
async def get_redis_structure(user: dict = Depends(require_auth)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Тільки для адміністраторів")
    data = await redis_client.get_structure()
    return data


@app.get("/api/points")
async def get_points(user: dict = Depends(require_auth)):
    data = await redis_client.get_all_points()

    # admin sees everything; others respect objects permission
    if user.get("role") != "admin":
        perms   = user.get("permissions") or {}
        allowed = perms.get("objects")
        if allowed and allowed != ["*"]:
            data = [p for p in data if p.get("object") in allowed]

    return {"count": len(data), "points": data}
