def process_state(r, obj, drop, sys, pid, value, quality):

    key = f"state:{obj}:{drop}:{sys}:{pid}"
    prev = r.get(key)

    new_state = "NORMAL"

    if quality == "BAD":
        new_state = "ALARM"

    if prev is None:
        r.set(key, "INIT")
        return None

    if prev == "ALARM" and new_state == "NORMAL":
        r.set(key, "NORMAL")
        return {
            "type": "CLEAR",
            "obj": obj,
            "drop": drop,
            "sys": sys,
            "pid": pid,
            "value": value
        }

    if prev != "ALARM" and new_state == "ALARM":
        r.set(key, "ALARM")
        return {
            "type": "ALARM",
            "obj": obj,
            "drop": drop,
            "sys": sys,
            "pid": pid,
            "value": value
        }

    r.set(key, new_state)
    return None