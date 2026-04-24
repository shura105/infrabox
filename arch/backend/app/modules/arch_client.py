import asyncio
import httpx
import os

ARCH_URL = os.environ.get("ARCH_URL", "http://localhost:8100")

# реальний rate limiting — через semaphore, не через пул httpx.
# max_connections=None (без обмежень пулу) + max_keepalive_connections=0
# (без keepalive для localhost) повністю усуває PoolTimeout.
# Semaphore гарантує що arch ніколи не отримає більше 6 запитів одночасно.
_semaphore = asyncio.Semaphore(6)

_client = httpx.AsyncClient(
    base_url=ARCH_URL,
    timeout=httpx.Timeout(connect=5.0, read=60.0, write=5.0, pool=5.0),
    limits=httpx.Limits(
        max_connections=None,
        max_keepalive_connections=0,
    )
)

# окремий клієнт для status/control — не конкурує з пулом range-запитів
_status_client = httpx.AsyncClient(
    base_url=ARCH_URL,
    timeout=httpx.Timeout(connect=3.0, read=5.0, write=3.0, pool=3.0),
    limits=httpx.Limits(max_connections=2, max_keepalive_connections=2)
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
    r = await _status_client.get("/status")
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
                     from_ts: int = None, to_ts: int = None,
                     max_records: int = None):
    params = {}
    if point_id is not None:
        params["point_id"] = point_id
    if from_ts is not None:
        params["from_ts"] = from_ts
    if to_ts is not None:
        params["to_ts"] = to_ts
    if max_records is not None:
        params["max_records"] = max_records
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
    r = await _status_client.post(f"/control/{action}")
    r.raise_for_status()
    return r.json()


async def get_arch_config():
    r = await _status_client.get("/arch-config")
    r.raise_for_status()
    return r.json()


async def set_arch_depth(max_volumes: int):
    r = await _status_client.post("/arch-config/depth",
                                  json={"max_volumes": max_volumes})
    r.raise_for_status()
    return r.json()