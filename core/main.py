import json
import time
import logging
from threading import Lock

import redis

from modules.mqtt import start_mqtt
from modules.quality import process_quality
from modules.init import load_points

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("core")

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
        except json.JSONDecodeError as e:
            log.warning(f"Bad JSON on topic {topic}: {e}")
            return

        point_id = payload.get("id")
        value = payload.get("value")
        ts = payload.get("ts", int(time.time()))

        if point_id is None:
            log.warning(f"Missing point_id on topic {topic}")
            return

        if value is None:
            log.warning(f"Missing value for point {point_id} on topic {topic}")
            return

        try:
            value = float(value)
        except (TypeError, ValueError):
            log.warning(f"Invalid value '{value}' for point {point_id}")
            return

        with lock:
            buffer[point_id] = {
                "value": value,
                "ts": ts,
                "topic": topic
            }

    return on_message


# --- MAIN ---
def main():
    log.info("Core started")

    config = load_config()
    meta_cache = load_points()
    r = get_redis(config)

    log.info(f"Loaded {len(meta_cache)} points")

    # --- MQTT ---
    mqtt_cfg = config["bootstrap"]["mqtt"]
    data_source = config["bootstrap"]["data_source"]

    start_mqtt(config, mqtt_callback(buffer, buffer_lock))

    # --- SYSTEM TICK ---
    tick = config["system"]["system_tick_ms"] / 1000

    log.info(f"System tick: {tick}s")

    # --- MAIN LOOP ---
    while True:
        # --- CLOCK ---
        tick_clock(r)

        # --- READ BUFFER ---
        with buffer_lock:
            updates = list(buffer.items())
            buffer.clear()

        # --- PROCESS BATCH ---
        passed = 0

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
            meta["last_update_ts"] = int(time.time() * 1000)

            # --- DEADBAND ---
            deadband = meta.get("deadband", 0)
            last_value = meta.get("last_value")

            if last_value is not None and deadband > 0:
                if abs(value - last_value) < deadband:
                    continue  # зміна незначна — пропускаємо

            meta["last_value"] = value
            passed += 1

            # --- QUALITY ---
            result = process_quality(
                point_id=point_id,
                value=value,
                meta=meta,
                config=config
            )

            if result:
                meta["state"] = result["new_state"]
                meta["last_change_ts"] = result["ts"]

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
                log.info(f"[EVENT] {result}")

                r.publish("bus:event", json.dumps(result))

        # --- DESYNC GUARD ---
        if config["system"]["desync_guard"]:
            now_ms = int(time.time() * 1000)
            timeout = config["system"]["desync_timeout_ms"]

            for point_id, meta in meta_cache.items():

                # точка ще ніколи не отримувала даних
                if meta["last_update_ts"] == 0:
                    continue

                # точка вже в NODATA — не дублюємо подію
                if meta["state"] == "NODATA":
                    continue

                if now_ms - meta["last_update_ts"] > timeout:
                    old_state = meta["state"]
                    meta["state"] = "NODATA"
                    meta["last_change_ts"] = now_ms

                    key = f"point:{point_id}"
                    r.hset(key, "quality", "NODATA")

                    event = {
                        "event": "DESYNC",
                        "object": meta["object"],
                        "drop": meta["drop"],
                        "system": meta["system"],
                        "point_id": point_id,
                        "value": None,
                        "old_state": old_state,
                        "new_state": "NODATA",
                        "ts": now_ms
                    }

                    log.warning(f"[DESYNC] point {point_id} → NODATA")
                    r.publish("bus:event", json.dumps(event))
                    r.publish("bus:data", point_id)

        # --- STATS ---
        r.set("system:buffer_size", len(updates))
        r.set("system:passed_deadband", passed)

        # --- SLEEP ---
        time.sleep(tick)


if __name__ == "__main__":
    main()
