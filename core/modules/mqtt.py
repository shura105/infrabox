import paho.mqtt.client as mqtt
import logging
log = logging.getLogger("core.mqtt")


def start_mqtt(config, on_message_cb):
    data_source = config["bootstrap"]["data_source"]
    mqtt_cfg = config["bootstrap"]["mqtt"]

    if data_source == "sim":
        host = mqtt_cfg["host_sim"]
    else:
        host = mqtt_cfg["host_real"]

    port = mqtt_cfg["port"]

    client = mqtt.Client()

    def on_connect(client, userdata, flags, rc):
        log.info(f"MQTT connected to {host}:{port} rc={rc}")
        client.subscribe("#")

    def on_message(client, userdata, msg):
        on_message_cb(msg.topic, msg.payload.decode())

    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(host, port, 60)
    client.loop_start()

    return client
