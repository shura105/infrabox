import redis.asyncio as redis


class RedisClient:
    def __init__(self):
        self.redis = None

    async def connect(self):
        self.redis = redis.Redis(
            host="infrabox-redis",
            port=6379,
            decode_responses=False
        )

    async def get_all_points(self):
        keys = await self.redis.keys("point:*")

        result = []

        for key in keys:
            data = await self.redis.hgetall(key)

            decoded = {
                k.decode(): v.decode()
                for k, v in data.items()
            }

            result.append(decoded)

        return result


redis_client = RedisClient()