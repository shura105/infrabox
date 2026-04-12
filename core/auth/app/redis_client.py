import os
import redis.asyncio as redis

REDIS_HOST = os.getenv("REDIS_HOST", "infrabox-redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))


class RedisClient:
    def __init__(self):
        self.r = None

    async def connect(self):
        self.r = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            decode_responses=True,
        )
        await self.r.ping()

    async def get_user(self, username: str) -> dict | None:
        data = await self.r.hgetall(f"user:{username}")
        return data if data else None

    async def set_user(self, username: str, fields: dict):
        await self.r.hset(f"user:{username}", mapping=fields)

    async def delete_user(self, username: str):
        await self.r.delete(f"user:{username}")

    async def list_users(self) -> list[dict]:
        keys = await self.r.keys("user:*")
        users = []
        for key in keys:
            data = await self.r.hgetall(key)
            username = key.split(":", 1)[1]
            users.append({"username": username, **data})
        return sorted(users, key=lambda u: u["username"])

    async def any_user_exists(self) -> bool:
        keys = await self.r.keys("user:*")
        return bool(keys)


redis_client = RedisClient()
