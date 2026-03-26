import json
import time
import os
import psutil
import paho.mqtt.client as mqtt

MQTT_HOST = os.environ.get("MQTT_HOST", "infrabox-mosquitto-sim")
MQTT_PORT = int(os.environ.get("MQTT_PORT", 1883))
OBJECT = os.environ.get("OBJECT", "home")
DROP = os.environ.get("DROP", "homeServ-1")
SYSTEM = "selfDiag"

# інтервали публікації
FAST_INTERVAL = 1    # cpu, net
SLOW_INTERVAL = 5    # mem, disk

POINTS = {
    "cpu_load":  {"id": 1, "interval": FAST_INTERVAL},
    "mem_used":  {"id": 2, "interval": SLOW_INTERVAL},
    "disk_used": {"id": 3, "interval": SLOW_INTERVAL},
    "net_rx":    {"id": 4, "interval": FAST_INTERVAL},
    "net_tx":    {"id": 5, "interval": FAST_INTERVAL},
}


def build_topic(pointname, point_id):
    return f"{OBJECT}/{SYSTEM}/{pointname}/{point_id}"


def get_metrics(prev_net, prev_time):
    now = time.time()
    elapsed = now - prev_time

    # CPU
    cpu = round(psutil.cpu_percent(interval=None), 1)

    # MEM
    mem = round(psutil.virtual_memory().percent, 1)

    # DISK
    disk = round(psutil.disk_usage("/").percent, 1)

    # NET
    net = psutil.net_io_counters()
    rx_bytes = net.bytes_recv - prev_net.bytes_recv
    tx_bytes = net.bytes_sent - prev_net.bytes_sent

    net_rx = round((rx_bytes * 8) / (elapsed * 1_000_000), 2)  # Mbps
    net_tx = round((tx_bytes * 8) / (elapsed * 1_000_000), 2)  # Mbps

    return {
        "cpu_load":  cpu,
        "mem_used":  mem,
        "disk_used": disk,
        "net_rx":    max(0, net_rx),
        "net_tx":    max(0, net_tx),
    }, net, now


def publish(client, pointname, value, ts):
    point = POINTS[pointname]
    topic = build_topic(pointname, point["id"])
    payload = json.dumps({
        "id": point["id"],
        "value": value,
        "ts": int(ts)
    })
    client.publish(topic, payload)
    print(f"[DIAG] {topic} → {value}")


def main():
    print(f"SELFDIAGNOSTIC STARTED → {MQTT_HOST}:{MQTT_PORT}")

    client = mqtt.Client()
    client.connect(MQTT_HOST, MQTT_PORT, 60)
    client.loop_start()

    # ініціалізація net counters
    prev_net = psutil.net_io_counters()
    prev_time = time.time()
    last_slow = 0

    # прогрів CPU лічильника
    psutil.cpu_percent(interval=None)

    while True:
        time.sleep(FAST_INTERVAL)

        metrics, prev_net, prev_time = get_metrics(prev_net, prev_time)
        now = prev_time
        ts = int(now)

        # --- FAST: cpu, net ---
        publish(client, "cpu_load", metrics["cpu_load"], ts)
        publish(client, "net_rx",   metrics["net_rx"],   ts)
        publish(client, "net_tx",   metrics["net_tx"],   ts)

        # --- SLOW: mem, disk ---
        if now - last_slow >= SLOW_INTERVAL:
            publish(client, "mem_used",  metrics["mem_used"],  ts)
            publish(client, "disk_used", metrics["disk_used"], ts)
            last_slow = now


if __name__ == "__main__":
    main()
