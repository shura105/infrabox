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

    # INIT → перший стан
    if old_state == "INIT":
        event = f"FIRST_VALID_{new_state}"

    # будь-який → UNCERT
    elif new_state == "UNCERT":
        if old_state != "UNCERT":
            event = "UNCERT"

    # UNCERT → будь-який валідний
    elif old_state == "UNCERT":
        event = f"CLEAR_UNCERT_TO_{new_state}"

    # будь-який → ALARM
    elif new_state == "ALARM":
        if old_state != "ALARM":
            event = "ALARM"

    # ALARM → GOOD з recovery
    elif old_state == "ALARM" and new_state == "GOOD":
        if now - meta["last_change_ts"] >= recovery_time:
            event = "CLEAR_ALARM"
        else:
            return None

    # будь-який → WARN (окрім WARN→WARN)
    elif new_state == "WARN":
        if old_state != "WARN":
            event = "WARN"

    # WARN → GOOD з recovery
    elif old_state == "WARN" and new_state == "GOOD":
        if now - meta["last_change_ts"] >= recovery_time:
            event = "CLEAR_WARN"
        else:
            return None

    if event:
        return {
            "event": event,
            "object": meta["object"],
            "drop": meta["drop"],
            "system": meta["system"],
            "point_id": point_id,
            "value": value,
            "old_state": old_state,
            "new_state": new_state,
            "ts": now
        }

    return None
