import json
import time
import random
import os
import paho.mqtt.client as mqtt

CONFIG_PATH = os.environ.get("POINTS_PATH", "/app/points.json")
MQTT_HOST = os.environ.get("MQTT_HOST", "infrabox-mosquitto-sim")
MQTT_PORT = int(os.environ.get("MQTT_PORT", 1883))


def load_points():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def build_topic(p):
    return f"{p['object']}/{p['system']}/{p['pointname']}/{p['id']}"


def main():
    print("SIMULATOR STARTED")

    points = load_points()
    print(f"Loaded {len(points)} points")

    client = mqtt.Client()
    client.connect(MQTT_HOST, MQTT_PORT, 60)

    while True:
        for p in points:
            value = round(random.uniform(p["min"], p["max"]), 2)

            payload = {
                "id": p["id"],
                "value": value,
                "ts": int(time.time())
            }

            topic = build_topic(p)

            client.publish(topic, json.dumps(payload))

            print(f"[SIM] {topic} -> {value}")

        time.sleep(1)


if __name__ == "__main__":
    main()