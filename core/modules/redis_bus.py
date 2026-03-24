import redis


def start_listener():

    r = redis.Redis(host="infrabox-redis", port=6379, decode_responses=True)

    pubsub = r.pubsub()

    pubsub.subscribe("bus:data")

    for msg in pubsub.listen():

        if msg["type"] != "message":
            continue

        pid = msg["data"]

        data = r.hgetall(f"data:{pid}")

        print("POINT UPDATE", pid, data)