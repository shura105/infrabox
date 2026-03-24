import json
import threading
import paho.mqtt.client as mqtt
from fastapi import FastAPI

app = FastAPI()


def load_system_config():
    with open("/app/config/system.json") as f:
        return json.load(f)


def get_mqtt_host():
    config = load_system_config()
    source = config.get("data_source", "real")

    if source == "sim":
        return "infrabox-mosquitto-sim"
    else:
        return "infrabox-mosquitto-real"


def on_connect(client, userdata, flags, rc):
    print("MQTT connected:", rc)
    client.subscribe("#")


def on_message(client, userdata, msg):
    print(f"[MQTT] {msg.topic} -> {msg.payload.decode()}")


def mqtt_thread():
    host = get_mqtt_host()
    print(f"[MQTT] connecting to {host}")

    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(host, 1883)
    client.loop_forever()


@app.on_event("startup")
def startup():
    thread = threading.Thread(target=mqtt_thread, daemon=True)
    thread.start()


@app.get("/")
def root():
    return {"status": "ok"}