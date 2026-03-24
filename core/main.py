import json
import time
from threading import Lock

import redis

from modules.mqtt import start_mqtt
from modules.quality import process_quality
from modules.init import load_points

CONFIG_PATH = "/app/config/system.json"

buffer = {}
buffer_lock = Lock()


# --- CONFIG ---
def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


# --- REDIS ---
def get_redis(cfg):
    r_cfg = cfg["bootstrap"]["redis"]

    return redis.Redis(
        host=r_cfg["host"],
        port=r_cfg["port"],
        db=r_cfg.get("db", 0),
        decode_responses=True
    )


# --- BUILD MQTT TOPIC ---
def build_topic(meta):
    return f"{meta['object']}/{meta['system']}/{meta['pointname']}/{meta['id']}"


# --- CLOCK ---
def tick_clock(r):
    ts = int(time.time())
    r.set("system:clock", ts)
    r.publish("bus:clock", ts)


# --- MQTT CALLBACK (тільки ingestion!) ---
def mqtt_callback(buffer, lock):
    def on_message(topic, payload_raw):
        try:
            payload = json.loads(payload_raw)
        except:
            print(f"[ERROR] Bad JSON: {payload_raw}")
            return

        point_id = payload.get("id")
        value = payload.get("value")
        ts = payload.get("ts", int(time.time()))

        if point_id is None:
            return

        # 👉 тільки кладемо в buffer
        with lock:
            buffer[point_id] = {
                "value": value,
                "ts": ts,
                "topic": topic
            }

    return on_message


# --- MAIN ---
def main():
    print("CORE STARTED")

    config = load_config()
    meta_cache = load_points()
    r = get_redis(config)

    print(f"INIT: loaded {len(meta_cache)} points")

    # --- MQTT ---
    mqtt_cfg = config["bootstrap"]["mqtt"]
    data_source = config["bootstrap"]["data_source"]

    if data_source == "sim":
        host = mqtt_cfg["host_sim"]
    else:
        host = mqtt_cfg["host_real"]

    port = mqtt_cfg["port"]

    start_mqtt(config, mqtt_callback(buffer, buffer_lock))

    print(f"[MQTT] Connected to {host}:{port}")

    # --- SYSTEM TICK ---
    tick = config["system"]["system_tick_ms"] / 1000

    print(f"SYSTEM TICK: {tick}s")

    # --- MAIN LOOP ---
    while True:
        # --- CLOCK ---
        tick_clock(r)

        # --- READ BUFFER ---
        with buffer_lock:
            updates = list(buffer.items())
            buffer.clear()

        # --- PROCESS BATCH ---
        for point_id, data in updates:

            if point_id not in meta_cache:
                continue

            meta = meta_cache[point_id]

            # --- topic validation ---
            expected_topic = build_topic(meta)

            if data["topic"] != expected_topic:
                continue

            value = data["value"]
            ts = data["ts"]

            # --- QUALITY ---
            result = process_quality(
                point_id=point_id,
                value=value,
                meta=meta,
                config=config
            )

            # --- REDIS WRITE ---
            key = f"point:{point_id}"

            r.hset(key, mapping={
                "value": value,
                "ts": ts,
                "quality": meta["state"],
                "object": meta["object"],
                "system": meta["system"],
                "pointname": meta["pointname"],
                "unit": meta.get("unit", "")
            })

            # --- PUB DATA ---
            r.publish("bus:data", point_id)

            # --- EVENTS ---
            if result:
                print("[EVENT]", result)

                r.hset(key, "alarm_state", result["new_state"])
                r.publish("bus:event", json.dumps(result))

        # --- STATS ---
        r.set("system:buffer_size", len(updates))

        # --- SLEEP ---
        time.sleep(tick)


if __name__ == "__main__":
    main()