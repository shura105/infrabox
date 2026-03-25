import json
import time
import random
import os
import paho.mqtt.client as mqtt

CONFIG_PATH = os.environ.get("POINTS_PATH", "/app/points.json")
MQTT_HOST = os.environ.get("MQTT_HOST", "infrabox-mosquitto-sim")
MQTT_PORT = int(os.environ.get("MQTT_PORT", 1883))

# як часто трапляються викиди (секунди)
SPIKE_INTERVAL = 15
SPIKE_DURATION = 5


def load_points():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def build_topic(p):
    return f"{p['object']}/{p['system']}/{p['pointname']}/{p['id']}"


def normal_center(p):
    """центр нормального діапазону"""
    return (p["warn_min"] + p["warn_max"]) / 2


def normal_drift(p):
    """малий крок — 2% від нормального діапазону"""
    return (p["warn_max"] - p["warn_min"]) * 0.02


def spike_value(p, level):
    """викид в warn або alarm зону"""
    if level == "warn":
        # випадково в warn зону (вище або нижче)
        if random.random() > 0.5:
            return random.uniform(p["warn_max"], p["alarm_max"])
        else:
            return random.uniform(p["alarm_min"], p["warn_min"])
    else:  # alarm
        if random.random() > 0.5:
            return random.uniform(p["alarm_max"], p["max"])
        else:
            return random.uniform(p["min"], p["alarm_min"])


def main():
    print("SIMULATOR STARTED")

    points = load_points()
    print(f"Loaded {len(points)} points")

    client = mqtt.Client()
    client.connect(MQTT_HOST, MQTT_PORT, 60)

    # ініціалізація стану кожної точки
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

            # --- визначаємо поточне значення ---
            if now < s["spike_until"]:
                # під час викиду — тримаємо spike значення
                value = s["value"]

            elif now >= s["next_spike"]:
                # час нового викиду
                level = "alarm" if random.random() > 0.6 else "warn"
                value = spike_value(p, level)
                s["value"] = value
                s["spike_until"] = now + SPIKE_DURATION
                s["next_spike"] = now + SPIKE_INTERVAL + random.uniform(-3, 3)
                print(
                    f"[SPIKE {level.upper()}] {p['pointname']} ({pid}) → {value:.2f}")

            else:
                # нормальна робота — малий дрейф
                drift = random.uniform(-normal_drift(p), normal_drift(p))
                value = s["value"] + drift

                # тримаємо в нормальному діапазоні
                value = max(p["warn_min"] * 1.05,
                            min(p["warn_max"] * 0.95, value))
                s["value"] = value

            payload = {
                "id": pid,
                "value": round(value, 2),
                "ts": int(time.time())
            }

            topic = build_topic(p)
            client.publish(topic, json.dumps(payload))

        time.sleep(1)


if __name__ == "__main__":
    main()
