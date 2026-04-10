import httpx
import os

ARCH_URL = os.environ.get("ARCH_URL", "http://localhost:8100")

# один persistent клієнт на весь процес
# limits: максимум 20 з'єднань до arch, не більше 10 одночасно на один хост
_client = httpx.AsyncClient(
    base_url=ARCH_URL,
    timeout=httpx.Timeout(
        connect=5.0,   # підключення
        read=60.0,     # читання (великі томи)
        write=5.0,
        pool=10.0      # очікування вільного з'єднання з пулу
    ),
    limits=httpx.Limits(
        max_connections=20,
        max_keepalive_connections=10,
        keepalive_expiry=30.0
    )
)


async def get_status():
    r = await _client.get("/status")
    return r.json()


async def get_volumes():
    r = await _client.get("/volumes")
    return r.json()


async def get_volume_meta(volume: str):
    r = await _client.get(f"/volumes/{volume}/meta")
    return r.json()


async def get_current_values(point_id: int = None):
    params = {}
    if point_id is not None:
        params["point_id"] = point_id
    r = await _client.get("/current/values", params=params)
    return r.json()


async def get_events(volume: str, point_id: int = None):
    params = {}
    if point_id:
        params["point_id"] = point_id
    r = await _client.get(f"/volumes/{volume}/events", params=params)
    return r.json()


async def get_values(volume: str, point_id: int = None,
                     from_ts: int = None, to_ts: int = None):
    params = {}
    if point_id:
        params["point_id"] = point_id
    if from_ts:
        params["from_ts"] = from_ts
    if to_ts:
        params["to_ts"] = to_ts
    r = await _client.get(f"/volumes/{volume}/values", params=params)
    return r.json()


async def get_selfdiag(volume: str, point_id: int = None):
    params = {}
    if point_id:
        params["point_id"] = point_id
    r = await _client.get(f"/volumes/{volume}/selfdiag", params=params)
    return r.json()


async def get_config(volume: str):
    r = await _client.get(f"/volumes/{volume}/config")
    return r.json()


async def get_sessions():
    r = await _client.get("/sessions")
    return r.json()


async def control(action: str):
    r = await _client.post(f"/control/{action}")
    return r.json()
