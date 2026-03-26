import json
import time
import os
import psutil
import paho.mqtt.client as mqtt

psutil.PROCFS_PATH = "/host/proc"

MQTT_HOST = os.environ.get("MQTT_HOST", "infrabox-mosquitto-sim")
MQTT_PORT = int(os.environ.get("MQTT_PORT", 1883))
OBJECT = os.environ.get("OBJECT", "home")
SYSTEM = "selfDiag"
POINTS_PATH = os.environ.get("POINTS_PATH", "/app/config/points.json")


def load_points():
    with open(POINTS_PATH) as f:
        all_points = json.load(f)
    return [p for p in all_points if p.get("system") == SYSTEM]


def build_topic(p):
    return f"{p['object']}/{p['system']}/{p['pointname']}/{p['id']}"


def get_metrics(prev_net, prev_time):
    now = time.time()
    elapsed = now - prev_time

    cpu = round(psutil.cpu_percent(interval=None), 1)

    vm = psutil.virtual_memory()
    mem = round((vm.total - vm.available) / vm.total * 100, 1)

    disk_space = round(psutil.disk_usage("/host/root").percent, 1)

    net = psutil.net_io_counters()
    rx_bytes = net.bytes_recv - prev_net.bytes_recv
    tx_bytes = net.bytes_sent - prev_net.bytes_sent
    net_rx = round((rx_bytes * 8) / (elapsed * 1_000_000), 2)
    net_tx = round((tx_bytes * 8) / (elapsed * 1_000_000), 2)

    return {
        "cpu_load":   cpu,
        "mem_used":   mem,
        "disk_space": disk_space,
        "net_rx":     max(0, net_rx),
        "net_tx":     max(0, net_tx),
    }, net, now


def publish(client, p, value, ts):
    topic = build_topic(p)
    payload = json.dumps({
        "id": p["id"],
        "value": value,
        "ts": int(ts)
    })
    client.publish(topic, payload)
    print(f"[DIAG] {topic} → {value}")


def main():
    print(f"SELFDIAGNOSTIC STARTED → {MQTT_HOST}:{MQTT_PORT}")

    points = load_points()
    print(f"Loaded {len(points)} selfDiag points")

    client = mqtt.Client()
    client.connect(MQTT_HOST, MQTT_PORT, 60)
    client.loop_start()

    prev_net = psutil.net_io_counters()
    prev_time = time.time()
    psutil.cpu_percent(interval=None)

    # last publish time для кожної точки
    last_pub = {p["id"]: 0 for p in points}

    while True:
        time.sleep(1)

        metrics, prev_net, prev_time = get_metrics(prev_net, prev_time)
        now = prev_time
        ts = int(now)

        for p in points:
            interval = p.get("interval", 1)
            if now - last_pub[p["id"]] >= interval:
                value = metrics.get(p["pointname"])
                if value is not None:
                    publish(client, p, value, ts)
                    last_pub[p["id"]] = now


if __name__ == "__main__":
    main()
