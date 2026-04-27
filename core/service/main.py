import json
import time
from threading import Lock

import redis

from modules.mqtt import start_mqtt
from modules.quality import process_quality
from modules.init import load_points
from modules.watchdog import RedisWatchdog
from modules.logger import setup_logger

CONFIG_PATH = "/app/config/sys_params.json"

buffer = {}
buffer_lock = Lock()

# глобальний логер — ініціалізується в main()
log = None


# --- CONFIG ---
def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


# --- REDIS ---
def get_redis(cfg):
    r_cfg = cfg["bootstrap"]["redis"]

    r = redis.Redis(
        host=r_cfg["host"],
        port=r_cfg["port"],
        db=r_cfg.get("db", 0),
        decode_responses=True
    )

    retries = 10
    for attempt in range(retries):
        try:
            r.ping()
            log.info("Redis connection established")
            return r
        except (redis.ConnectionError, redis.TimeoutError):
            log.warning(f"Redis unavailable, retry {attempt + 1}/{retries}...")
            time.sleep(2)

    log.error("Redis unavailable after all retries — exiting")
    raise SystemExit(1)


# --- REDIS ONE SHOT ---
def try_reconnect_redis(cfg):
    r_cfg = cfg["bootstrap"]["redis"]
    r = redis.Redis(
        host=r_cfg["host"],
        port=r_cfg["port"],
        db=r_cfg.get("db", 0),
        decode_responses=True
    )
    r.ping()
    return r


# --- BUILD MQTT TOPIC ---
def build_topic(meta):
    return f"{meta['object']}/{meta['system']}/{meta['pointname']}/{meta['id']}"


# --- CLOCK ---
def tick_clock(r):
    ts = int(time.time())
    r.set("system:clock", ts)
    r.publish("bus:clock", ts)


# --- MQTT CALLBACK ---
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


# --- RESET TO INIT ---
def reset_meta(meta_cache):
    for meta in meta_cache.values():
        meta["state"] = "INIT"
        meta["last_update_ts"] = 0
        meta["last_value"] = None
    log.info("All points reset to INIT")


# --- CLEAR REDIS STRUCTURE ---
def clear_redis(r, meta_cache):
    keys = [f"point:{pid}" for pid in meta_cache]
    keys += ["system:clock", "system:buffer_size", "system:passed_deadband"]
    r.delete(*keys)
    log.info("Redis structure cleared")


# --- MAIN ---
def main():
    global log

    config = load_config()
    log = setup_logger("core", config)
    log.info("Core started")

    meta_cache = load_points()

    r = get_redis(config)

    log.info(f"Loaded {len(meta_cache)} points")

    # start_mqtt(config, mqtt_callback(buffer, buffer_lock), log)
    mqtt_client = start_mqtt(config, mqtt_callback(buffer, buffer_lock))

    # --- WATCHDOG ---
    watchdog = RedisWatchdog(r, timeout_sec=5)
    watchdog.start()

    # --- SYSTEM TICK ---
    tick = config["system"]["system_tick_ms"] / 1000

    log.info(f"System tick: {tick}s")

    _hb_tick = 0
    _HB_INTERVAL = 25  # update heartbeat points every N ticks
    # built dynamically from points.json — system points with hb_service field
    _HB_POINTS = {
        meta["hb_service"]: pid
        for pid, meta in meta_cache.items()
        if meta.get("hb_service")
    }

    # --- MAIN LOOP ---
    while True:

        # --- WATCHDOG CHECK ---
        if not watchdog.check():
            reset_meta(meta_cache)

            log.error("Redis unhealthy — attempting reconnect...")
            try:
                r_new = try_reconnect_redis(config)
                clear_redis(r_new, meta_cache)

                r = r_new
                watchdog.r = r_new
                watchdog.last_heartbeat = time.time()

                log.info("Redis reconnected — ready")

            except Exception as e:
                log.error(f"Reconnect failed: {e}")
                time.sleep(5)

            continue

        try:
            # --- CLOCK ---
            tick_clock(r)

            # --- READ BUFFER ---
            with buffer_lock:
                updates = list(buffer.items())
                buffer.clear()

            # --- PROCESS BATCH ---
            passed = 0
            batch_pipe = r.pipeline()
            batch_has = False

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
                        continue

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

                # --- REDIS WRITE + PUB DATA (queued; flushed once after loop) ---
                key = f"point:{point_id}"
                lim = meta["limits"]
                batch_pipe.hset(key, mapping={
                    "value": value,
                    "ts": ts,
                    "quality": meta["state"],
                    "object": meta["object"],
                    "system": meta["system"],
                    "pointname": meta["pointname"],
                    "unit": meta.get("unit", ""),
                    "min":       lim["min"],
                    "max":       lim["max"],
                    "warn_min":  lim["warn_min"],
                    "warn_max":  lim["warn_max"],
                    "alarm_min": lim["alarm_min"],
                    "alarm_max": lim["alarm_max"],
                })
                batch_pipe.publish("bus:data", point_id)

                if result:
                    batch_pipe.publish("bus:event", json.dumps(result))

                batch_has = True

                # if result:
                #    log.info(f"[EVENT] {result}")

            if batch_has:
                batch_pipe.execute()

            # --- DESYNC GUARD ---
            if config["system"]["desync_guard"]:
                now_ms = int(time.time() * 1000)
                timeout = config["system"]["desync_timeout_ms"]

                for point_id, meta in meta_cache.items():

                    if meta["last_update_ts"] == 0:
                        continue

                    if meta["state"] == "NODATA":
                        continue

                    if now_ms - meta["last_update_ts"] > timeout:
                        old_state = meta["state"]
                        meta["state"] = "NODATA"
                        meta["last_change_ts"] = now_ms

                        key = f"point:{point_id}"

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

                        pipe = r.pipeline()
                        pipe.hset(key, "quality", "NODATA")
                        pipe.publish("bus:event", json.dumps(event))
                        pipe.publish("bus:data", point_id)
                        pipe.execute()

                        # log.warning(f"[DESYNC] point {point_id} → NODATA")

            # --- STATS ---
            r.set("system:buffer_size", len(updates))
            r.set("system:passed_deadband", passed)
            r.set("heartbeat:infrabox-core", int(time.time()), ex=25)

            # --- HEARTBEAT POINTS (every _HB_INTERVAL ticks) ---
            _hb_tick += 1
            if _hb_tick >= _HB_INTERVAL:
                _hb_tick = 0
                now_ms = int(time.time() * 1000)
                hb_get_pipe = r.pipeline()
                for svc in _HB_POINTS:
                    hb_get_pipe.get(f"heartbeat:{svc}")
                hb_statuses = hb_get_pipe.execute()

                hb_pipe = r.pipeline()
                for (svc, pid), raw in zip(_HB_POINTS.items(), hb_statuses):
                    alive   = raw is not None
                    value   = "1" if alive else "0"
                    quality = "GOOD" if alive else "ALARM"
                    meta    = meta_cache[pid]
                    lim     = meta["limits"]
                    hb_pipe.hset(f"point:{pid}", mapping={
                        "value":    value,
                        "ts":       now_ms,
                        "quality":  quality,
                        "object":   meta["object"],
                        "system":   meta["system"],
                        "pointname": meta["pointname"],
                        "unit":     meta.get("unit", ""),
                        "min":      lim["min"],    "max":      lim["max"],
                        "warn_min": lim["warn_min"],"warn_max": lim["warn_max"],
                        "alarm_min":lim["alarm_min"],"alarm_max":lim["alarm_max"],
                    })
                    hb_pipe.publish("bus:data", pid)
                hb_pipe.execute()

        except Exception as e:
            log.error(f"Unexpected error in main loop: {e}")
            time.sleep(1)

        # --- MQTT WATCHDOG ---
        if config["system"].get("mqtt_watchdog"):
            timeout = config["system"]["mqtt_timeout_ms"]
            if not mqtt_client.check(timeout):
                log.error("MQTT heartbeat lost — reconnecting...")
                mqtt_client.reconnect()

        # --- SLEEP ---
        time.sleep(tick)


if __name__ == "__main__":
    main()
