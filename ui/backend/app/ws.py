from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.redis_client import RedisClient

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    client = RedisClient()
    await client.connect()

    try:
        data = await client.get_all_points()
        await ws.send_json({"type": "snapshot", "data": data})

        await client.subscribe()

        async for batch in client.listen():
            await ws.send_json({"type": "update", "data": batch})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print("WS error:", e)