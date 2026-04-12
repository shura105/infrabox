from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import asyncio
from app.redis_client import redis_client

router = APIRouter()


async def fallback_loop(ws):
    while True:
        data = await redis_client.get_all_points()
        await ws.send_json({
            "type": "update",
            "data": data
        })
        await asyncio.sleep(1)


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()

    try:
        # 🔹 snapshot
        data = await redis_client.get_all_points()
        await ws.send_json({
            "type": "snapshot",
            "data": data
        })

        # 🔹 pub/sub
        await redis_client.subscribe()

        async for update in redis_client.listen():
            await ws.send_json({
                "type": "update",
                "data": [update]
            })

    except Exception as e:
        print("WS error:", e)
        await fallback_loop(ws)

    except WebSocketDisconnect:
        print("Client disconnected")