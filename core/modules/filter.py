def apply_deadband(r, updates):

    result = []

    for obj, drop, sys, pid, value in updates:

        meta = r.hgetall(f"meta:{pid}")
        if not meta:
            continue

        deadband = float(meta.get("deadband", 0))

        key = f"data:{obj}:{drop}:{sys}:{pid}"
        old = r.hget(key, "value")

        if old is None:
            result.append((obj, drop, sys, pid, value))
            continue

        if abs(value - float(old)) >= deadband:
            result.append((obj, drop, sys, pid, value))

    return result