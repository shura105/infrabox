import httpx
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

bearer   = HTTPBearer()
AUTH_URL = "http://infrabox-auth:8095/auth/me"


async def require_auth(
    creds: HTTPAuthorizationCredentials = Depends(bearer),
) -> dict:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(
                AUTH_URL,
                headers={"Authorization": f"Bearer {creds.credentials}"},
            )
        if r.status_code != 200:
            raise HTTPException(status_code=401, detail="Unauthorized")
        return r.json()
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Auth service unavailable")


async def require_admin(user: dict = Depends(require_auth)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return user
