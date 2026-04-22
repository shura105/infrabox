import redis.asyncio as redis


class RedisClient:
    def __init__(self):
        self.redis = None
        self._pubsub = None

    async def connect(self):
        self.redis = redis.Redis(
            host="infrabox-redis",
            port=6379,
            decode_responses=False
        )

    async def subscribe(self):
        self._pubsub = self.redis.pubsub()
        await self._pubsub.subscribe("bus:data")

    async def listen(self, batch_interval=0.2):
        """Collect changed point_ids over batch_interval seconds, then read+yield as one batch."""
        import asyncio
        pending = set()
        deadline = asyncio.get_event_loop().time() + batch_interval

        async for msg in self._pubsub.listen():
            if msg["type"] != "message":
                continue
            raw = msg["data"]
            pending.add(raw.decode() if isinstance(raw, bytes) else str(raw))

            now = asyncio.get_event_loop().time()
            if now < deadline:
                continue

            deadline = now + batch_interval
            if not pending:
                continue

            pipe = self.redis.pipeline()
            ids = list(pending)
            pending.clear()
            for pid in ids:
                pipe.hgetall(f"point:{pid}")
            results = await pipe.execute()

            batch = []
            for pid, data in zip(ids, results):
                if not data:
                    continue
                decoded = {
                    (k.decode() if isinstance(k, bytes) else k):
                    (v.decode() if isinstance(v, bytes) else v)
                    for k, v in data.items()
                }
                decoded["id"] = pid
                batch.append(decoded)

            if batch:
                yield batch

    async def get_all_points(self):
        keys = await self.redis.keys("point:*")

        result = []

        for key in keys:
            data = await self.redis.hgetall(key)

            decoded = {
                k.decode(): v.decode()
                for k, v in data.items()
            }

            # include numeric id from key "point:NNN"
            key_str = key.decode() if isinstance(key, bytes) else key
            decoded['id'] = key_str.split(':', 1)[1]

            result.append(decoded)

        return result

    async def get_structure(self):
        keys = await self.redis.keys("*")
        result = []

        for key in keys:
            key_str = key.decode() if isinstance(key, bytes) else key
            ktype_raw = await self.redis.type(key)
            ktype = ktype_raw.decode() if isinstance(ktype_raw, bytes) else ktype_raw

            item = {"key": key_str, "type": ktype}

            try:
                if ktype == "hash":
                    raw = await self.redis.hgetall(key)
                    item["value"] = {
                        (k.decode() if isinstance(k, bytes) else k):
                        (v.decode() if isinstance(v, bytes) else v)
                        for k, v in raw.items()
                    }
                elif ktype == "string":
                    val = await self.redis.get(key)
                    item["value"] = val.decode() if isinstance(val, bytes) else val
                elif ktype == "list":
                    vals = await self.redis.lrange(key, 0, -1)
                    item["value"] = [v.decode() if isinstance(v, bytes) else v for v in vals]
                elif ktype == "set":
                    vals = await self.redis.smembers(key)
                    item["value"] = sorted(v.decode() if isinstance(v, bytes) else v for v in vals)
                elif ktype == "zset":
                    vals = await self.redis.zrange(key, 0, -1, withscores=True)
                    item["value"] = [[v.decode() if isinstance(v, bytes) else v, s] for v, s in vals]
                else:
                    item["value"] = None
            except Exception as e:
                item["value"] = f"<error: {e}>"

            result.append(item)

        result.sort(key=lambda x: x["key"])
        return {"total": len(result), "keys": result}


redis_client = RedisClient()