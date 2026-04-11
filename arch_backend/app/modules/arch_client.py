import asyncio
import httpx
import os

ARCH_URL = os.environ.get("ARCH_URL", "http://localhost:8100")

# обмежуємо кількість одночасних запитів до arch
# щоб пул з'єднань не вичерпувався при великій кількості томів
_semaphore = asyncio.Semaphore(8)

_client = httpx.AsyncClient(
    base_url=ARCH_URL,
    timeout=httpx.Timeout(
        connect=5.0,
        read=60.0,
        write=5.0,
        pool=30.0      # збільшено — чекає поки семафор звільниться
    ),
    limits=httpx.Limits(
        max_connections=10,        # менше з'єднань, але контрольовано
        max_keepalive_connections=8,
        keepalive_expiry=30.0
    )
)


async def _get(path: str, **kwargs):
    async with _semaphore:
        r = await _client.get(path, **kwargs)
        r.raise_for_status()
        return r.json()


async def _post(path: str, **kwargs):
    async with _semaphore:
        r = await _client.post(path, **kwargs)
        r.raise_for_status()
        return r.json()


async def get_status():
    r = await _client.get("/status")  # без семафору — завжди має проходити
    r.raise_for_status()
    return r.json()


async def get_volumes():
    return await _get("/volumes")


async def get_volume_meta(volume: str):
    return await _get(f"/volumes/{volume}/meta")


async def get_current_values(point_id: int = None):
    params = {}
    if point_id is not None:
        params["point_id"] = point_id
    return await _get("/current/values", params=params)


async def get_events(volume: str, point_id: int = None):
    params = {}
    if point_id is not None:
        params["point_id"] = point_id
    return await _get(f"/volumes/{volume}/events", params=params)


async def get_values(volume: str, point_id: int = None,
                     from_ts: int = None, to_ts: int = None):
    params = {}
    if point_id is not None:
        params["point_id"] = point_id
    if from_ts is not None:
        params["from_ts"] = from_ts
    if to_ts is not None:
        params["to_ts"] = to_ts
    return await _get(f"/volumes/{volume}/values", params=params)


async def get_selfdiag(volume: str, point_id: int = None):
    params = {}
    if point_id is not None:
        params["point_id"] = point_id
    return await _get(f"/volumes/{volume}/selfdiag", params=params)


async def get_config(volume: str):
    return await _get(f"/volumes/{volume}/config")


async def get_sessions():
    return await _get("/sessions")


async def control(action: str):
    return await _post(f"/control/{action}")