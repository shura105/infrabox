def update_points_batch(r, updates):

    pipe = r.pipeline()

    for pid, value, q in updates:
        key = f"data:{pid}"

        pipe.hset(key, mapping={
            "value": value,
            "q": q
        })

        pipe.publish("bus:data", f"{pid}:{value}:{q}")

    pipe.execute()