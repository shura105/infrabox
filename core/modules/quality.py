import time


def classify(value, limits):
    if value < limits["min"] or value > limits["max"]:
        return "UNCERT"

    if value < limits["alarm_min"] or value > limits["alarm_max"]:
        return "ALARM"

    if value < limits["warn_min"] or value > limits["warn_max"]:
        return "WARN"

    return "GOOD"


def process_quality(point_id, value, meta, config):
    now = int(time.time() * 1000)

    limits = meta["limits"]
    old_state = meta["state"]

    new_state = classify(value, limits)

    recovery_time = config["system"]["quality"]["recovery_time_ms"]

    event = None

    # INIT → first state
    if old_state == "INIT":
        event = f"FIRST_VALID_{new_state}"

    elif new_state == "UNCERT":
        if old_state != "UNCERT":
            event = "UNCERT"

    elif old_state == "UNCERT" and new_state == "GOOD":
        event = "CLEAR_UNCERT"

    elif new_state == "ALARM":
        if old_state != "ALARM":
            event = "ALARM"

    elif old_state == "ALARM" and new_state == "GOOD":
        if now - meta["last_change_ts"] >= recovery_time:
            event = "CLEAR_ALARM"
        else:
            return None

    elif new_state == "WARN":
        if old_state == "GOOD":
            event = "WARN"

    elif old_state == "WARN" and new_state == "GOOD":
        if now - meta["last_change_ts"] >= recovery_time:
            event = "CLEAR_WARN"
        else:
            return None

    if event:
        meta["state"] = new_state
        meta["last_change_ts"] = now

        return {
            "event": event,
            "object": meta["object"],
            "drop": meta["drop"],
            "system": meta["system"],
            "point_id": point_id,
            "value": value,
            "old_state": old_state,
            "new_state": new_state
        }

    return None