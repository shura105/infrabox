import paho.mqtt.client as mqtt
import json

def start_mqtt(buffer, buffer_lock, host, port):
    def on_message(client, userdata, msg):
        try:
            data = json.loads(msg.payload.decode())

            obj = data["object"]
            drop = data["drop"]
            sys = data["system"]
            pid = data["point_id"]
            value = data["value"]

            with buffer_lock:
                buffer.append((obj, drop, sys, pid, value))

        except Exception as e:
            print("MQTT ERROR:", e)

    client = mqtt.Client()
    client.on_message = on_message

    client.connect(host, port)
    client.subscribe("infrabox/data")

    print(f"MQTT CONFIG → host={host}, port={port}")
    client.loop_start()