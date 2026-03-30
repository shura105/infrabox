import httpx
import os

ARCH_URL = os.environ.get("ARCH_URL", "http://localhost:8100")
TIMEOUT = httpx.Timeout(30.0)


async def get_status():
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.get(f"{ARCH_URL}/status")
        return r.json()


async def get_volumes():
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.get(f"{ARCH_URL}/volumes")
        return r.json()


async def get_volume_meta(volume: str):
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.get(f"{ARCH_URL}/volumes/{volume}/meta")
        return r.json()


async def get_events(volume: str, point_id: int = None):
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        params = {}
        if point_id:
            params["point_id"] = point_id
        r = await client.get(f"{ARCH_URL}/volumes/{volume}/events", params=params)
        return r.json()


async def get_values(volume: str, point_id: int = None,
                     from_ts: int = None, to_ts: int = None):
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        params = {}
        if point_id:
            params["point_id"] = point_id
        if from_ts:
            params["from_ts"] = from_ts
        if to_ts:
            params["to_ts"] = to_ts
        r = await client.get(f"{ARCH_URL}/volumes/{volume}/values", params=params)
        return r.json()


async def get_selfdiag(volume: str, point_id: int = None):
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        params = {}
        if point_id:
            params["point_id"] = point_id
        r = await client.get(f"{ARCH_URL}/volumes/{volume}/selfdiag", params=params)
        return r.json()


async def get_config(volume: str):
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.get(f"{ARCH_URL}/volumes/{volume}/config")
        return r.json()


async def get_sessions():
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.get(f"{ARCH_URL}/sessions")
        return r.json()


async def control(action: str):
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.post(f"{ARCH_URL}/control/{action}")
        return r.json()
