import time
import logging
import threading
import paho.mqtt.client as mqtt

log = logging.getLogger("core.mqtt")


class MqttClient:
    def __init__(self, config, on_message_cb):
        self.config = config
        self.on_message_cb = on_message_cb
        self.client = None
        self.last_message_ts = time.time()
        self._lock = threading.Lock()

        mqtt_cfg = config["bootstrap"]["mqtt"]
        data_source = config["bootstrap"]["data_source"]

        if data_source == "sim":
            self.host = mqtt_cfg["host_sim"]
        else:
            self.host = mqtt_cfg["host_real"]

        self.port = mqtt_cfg["port"]

    def _on_connect(self, client, userdata, flags, rc):
        log.info(f"MQTT connected to {self.host}:{self.port} rc={rc}")
        client.subscribe("#")

    def _on_disconnect(self, client, userdata, rc):
        log.error(f"MQTT disconnected rc={rc}")

    def _on_message(self, client, userdata, msg):
        with self._lock:
            self.last_message_ts = time.time()
        self.on_message_cb(msg.topic, msg.payload.decode())

    def connect(self):
        self.client = mqtt.Client()
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.connect(self.host, self.port, 60)
        self.client.loop_start()

    def reconnect(self):
        log.error("MQTT reconnecting...")
        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass
        self.connect()
        self.last_message_ts = time.time()
        log.info("MQTT reconnected")

    def check(self, timeout_ms):
        with self._lock:
            elapsed = (time.time() - self.last_message_ts) * 1000
        return elapsed < timeout_ms


def start_mqtt(config, on_message_cb):
    client = MqttClient(config, on_message_cb)
    client.connect()
    return client
