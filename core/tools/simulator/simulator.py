import json
import time
import random
import os
import logging
import threading
from logging.handlers import RotatingFileHandler
import paho.mqtt.client as mqtt
import redis as redis_lib

CONFIG_PATH = os.environ.get("POINTS_PATH", "/app/points.json")
MQTT_HOST   = os.environ.get("MQTT_HOST",   "infrabox-mosquitto-sim")
MQTT_PORT   = int(os.environ.get("MQTT_PORT", 1883))
REDIS_HOST  = os.environ.get("REDIS_HOST",  "infrabox-redis")
REDIS_PORT  = int(os.environ.get("REDIS_PORT", 6379))


def _heartbeat_thread():
    r = None
    while True:
        try:
            if r is None:
                r = redis_lib.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
            r.set("heartbeat:infrabox-simulator", int(time.time()), ex=25)
        except Exception:
            r = None
        time.sleep(5)

SPIKE_INTERVAL = 15
SPIKE_DURATION = 5


def setup_logger():
    logger = logging.getLogger("simulator")
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s"
    )

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    os.makedirs("/app/log", exist_ok=True)
    file_handler = RotatingFileHandler(
        "/app/log/simulator.log",
        maxBytes=1048576,
        backupCount=3
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


def load_points():
    with open(CONFIG_PATH) as f:
        points = json.load(f)
    return [p for p in points if p["id"] >= 100 and p.get("type") != "diagnostic"]


def build_topic(p):
    return f"{p['object']}/{p['system']}/{p['pointname']}/{p['id']}"


def normal_center(p):
    return (p["warn_min"] + p["warn_max"]) / 2


def normal_drift(p):
    return (p["warn_max"] - p["warn_min"]) * 0.02


def spike_value(p, level):
    if level == "warn":
        if random.random() > 0.5:
            return random.uniform(p["warn_max"], p["alarm_max"])
        else:
            return random.uniform(p["alarm_min"], p["warn_min"])
    else:
        if random.random() > 0.5:
            return random.uniform(p["alarm_max"], p["max"])
        else:
            return random.uniform(p["min"], p["alarm_min"])


def main():
    log = setup_logger()
    log.info(f"Simulator started → {MQTT_HOST}:{MQTT_PORT}")
    threading.Thread(target=_heartbeat_thread, daemon=True).start()

    points = load_points()
    log.info(f"Loaded {len(points)} points")

    client = mqtt.Client()
    client.connect(MQTT_HOST, MQTT_PORT, 60)
    client.loop_start()

    state = {}
    for p in points:
        pid = p["id"]
        state[pid] = {
            "value": normal_center(p),
            "spike_until": 0,
            "next_spike": time.time() + random.uniform(5, SPIKE_INTERVAL)
        }

    while True:
        now = time.time()

        for p in points:
            pid = p["id"]
            s = state[pid]

            if now < s["spike_until"]:
                value = s["value"]

            elif now >= s["next_spike"]:
                level = "alarm" if random.random() > 0.6 else "warn"
                value = spike_value(p, level)
                s["value"] = value
                s["spike_until"] = now + SPIKE_DURATION
                s["next_spike"] = now + SPIKE_INTERVAL + random.uniform(-3, 3)
                log.debug(
                    f"[SPIKE {level.upper()}] {p['pointname']} ({pid}) → {value:.2f}")

            else:
                drift = random.uniform(-normal_drift(p), normal_drift(p))
                value = s["value"] + drift
                value = max(p["warn_min"] * 1.05,
                            min(p["warn_max"] * 0.95, value))
                s["value"] = value

            payload = {
                "id": pid,
                "value": round(value, 2),
                "ts": int(time.time())
            }

            client.publish(build_topic(p), json.dumps(payload))

        time.sleep(1)


if __name__ == "__main__":
    main()
