from fastapi import FastAPI
from app.redis_client import redis_client

app = FastAPI()


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


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/points")
async def get_points():
    data = await redis_client.get_all_points()

    return {
        "count": len(data),
        "points": data
    }