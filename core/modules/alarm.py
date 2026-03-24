import json
import time


def publish_event(r, event: dict):
    """
    Публікація події в Redis Stream
    """
    r.xadd("stream:event", {
        "data": json.dumps(event)
    })


def build_event(meta, value, old_state, new_state, event_type):
    """
    Формування структури події
    """
    return {
        "ts": int(time.time() * 1000),

        "event": event_type,

        "object": meta["object"],
        "drop": meta["drop"],
        "system": meta["system"],
        "point_id": meta["id"],

        "value": value,

        "old_state": old_state,
        "new_state": new_state
    }


def emit_event(r, meta, value, old_state, new_state, event_type):
    """
    Повний цикл: створити + відправити
    """
    event = build_event(meta, value, old_state, new_state, event_type)

    # DEBUG лог
    print(f"[EVENT] {event}")

    publish_event(r, event)