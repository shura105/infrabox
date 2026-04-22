import json
import os
import threading
import time

import jwt
from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.models import LoginRequest, TokenResponse, UserCreate, UserUpdate, UserPublic, PermissionsUpdate
from app.security import hash_password, verify_password, create_token, decode_token, VALID_ROLES
from app.redis_client import redis_client

app = FastAPI(title="infrabox-auth")
bearer = HTTPBearer()


# ─── helpers ─────────────────────────────────────────────────────────────────

def _parse_perms(raw: str) -> dict:
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


async def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(bearer),
) -> dict:
    try:
        return decode_token(creds.credentials)
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return user


# ─── lifecycle ───────────────────────────────────────────────────────────────

def _heartbeat_thread():
    import redis as redis_sync
    r = None
    while True:
        try:
            if r is None:
                r = redis_sync.Redis(
                    host=os.getenv("REDIS_HOST", "infrabox-redis"),
                    port=int(os.getenv("REDIS_PORT", 6379)),
                    decode_responses=True
                )
            r.set("heartbeat:infrabox-auth", int(time.time()), ex=25)
        except Exception:
            r = None
        time.sleep(5)


@app.on_event("startup")
async def startup():
    await redis_client.connect()
    print("✅ Redis connected")
    threading.Thread(target=_heartbeat_thread, daemon=True).start()

    if not await redis_client.any_user_exists():
        await redis_client.set_user("admin", {
            "role":          "admin",
            "display_name":  "Administrator",
            "password_hash": hash_password("admin"),
            "active":        "1",
        })
        print("⚠️  Default admin created (admin/admin) — CHANGE PASSWORD!")


# ─── public ──────────────────────────────────────────────────────────────────

@app.get("/auth/health")
async def health():
    return {"status": "ok"}


@app.post("/auth/login", response_model=TokenResponse)
async def login(req: LoginRequest):
    user = await redis_client.get_user(req.username)

    if not user or user.get("active") != "1":
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    perms = _parse_perms(user.get("permissions", ""))
    token = create_token(req.username, user["role"], user["display_name"], perms)
    return TokenResponse(
        access_token=token,
        role=user["role"],
        display_name=user["display_name"],
    )


@app.get("/auth/me")
async def me(user: dict = Depends(get_current_user)):
    # always fetch fresh permissions from Redis (take effect without re-login on backend)
    db_user = await redis_client.get_user(user["sub"])
    perms = _parse_perms(db_user.get("permissions", "")) if db_user else {}
    return {
        "username":     user["sub"],
        "role":         user["role"],
        "display_name": user["display_name"],
        "permissions":  perms,
    }


# ─── admin: user management ──────────────────────────────────────────────────

@app.get("/auth/users", response_model=list[UserPublic])
async def list_users(_: dict = Depends(require_admin)):
    users = await redis_client.list_users()
    return [
        UserPublic(
            username=u["username"],
            role=u["role"],
            display_name=u["display_name"],
            active=u.get("active", "1"),
            permissions=_parse_perms(u.get("permissions", "")) or None,
        )
        for u in users
    ]


@app.post("/auth/users", status_code=201)
async def create_user(req: UserCreate, _: dict = Depends(require_admin)):
    if req.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role. Valid: {VALID_ROLES}")
    if await redis_client.get_user(req.username):
        raise HTTPException(status_code=409, detail="User already exists")

    await redis_client.set_user(req.username, {
        "role":          req.role,
        "display_name":  req.display_name,
        "password_hash": hash_password(req.password),
        "active":        "1",
    })
    return {"ok": True}


@app.put("/auth/users/{username}")
async def update_user(
    username: str,
    req: UserUpdate,
    _: dict = Depends(require_admin),
):
    if not await redis_client.get_user(username):
        raise HTTPException(status_code=404, detail="User not found")

    updates: dict = {}
    if req.password:
        updates["password_hash"] = hash_password(req.password)
    if req.role:
        if req.role not in VALID_ROLES:
            raise HTTPException(status_code=400, detail=f"Invalid role. Valid: {VALID_ROLES}")
        updates["role"] = req.role
    if req.display_name:
        updates["display_name"] = req.display_name
    if req.active in ("0", "1"):
        updates["active"] = req.active

    if updates:
        await redis_client.set_user(username, updates)
    return {"ok": True}


@app.patch("/auth/users/{username}/permissions")
async def set_permissions(
    username: str,
    req: PermissionsUpdate,
    _: dict = Depends(require_admin),
):
    if not await redis_client.get_user(username):
        raise HTTPException(status_code=404, detail="User not found")

    perms: dict = {}
    if req.pages is not None:
        perms["pages"] = req.pages
    if req.objects is not None:
        perms["objects"] = req.objects

    await redis_client.set_user(username, {"permissions": json.dumps(perms)})
    return {"ok": True}


@app.delete("/auth/users/{username}")
async def delete_user(username: str, current: dict = Depends(require_admin)):
    if username == current["sub"]:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    if not await redis_client.get_user(username):
        raise HTTPException(status_code=404, detail="User not found")

    await redis_client.delete_user(username)
    return {"ok": True}
