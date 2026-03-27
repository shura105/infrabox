import json
import time
import threading
import redis


class Writer:
    def __init__(self, config, volume, log):
        self.config = config
        self.volume = volume
        self.log = log
        self.running = False
        self.last_heartbeat = time.time()
        self.timeout_sec = 10

        self.r = self._connect()

    def _connect(self):
        r_cfg = self.config["redis"]
        r = redis.Redis(
            host=r_cfg["host"],
            port=r_cfg["port"],
            db=r_cfg.get("db", 0),
            decode_responses=True
        )
        return r

    def _listen_clock(self):
        """bus:clock → heartbeat"""
        while self.running:
            try:
                pubsub = self.r.pubsub()
                pubsub.subscribe("bus:clock")
                for msg in pubsub.listen():
                    if not self.running:
                        break
                    if msg["type"] == "message":
                        self.last_heartbeat = time.time()
            except Exception as e:
                self.log.error(f"Clock listener error: {e}")
            time.sleep(2)

    def _listen_events(self):
        """bus:event → events.json та selfdiag.json"""
        while self.running:
            try:
                pubsub = self.r.pubsub()
                pubsub.subscribe("bus:event")
                for msg in pubsub.listen():
                    if not self.running:
                        break
                    if msg["type"] != "message":
                        continue
                    event = json.loads(msg["data"])
                    stream = "selfdiag" if event.get(
                        "system") == "selfDiag" else "events"
                    self.volume.write(stream, event)
            except Exception as e:
                self.log.error(f"Writer events error: {e}")
            time.sleep(2)

    def _listen_values(self):
        """bus:data → values.json"""
        prev_values = {}
        while self.running:
            try:
                pubsub = self.r.pubsub()
                pubsub.subscribe("bus:data")
                for msg in pubsub.listen():
                    if not self.running:
                        break
                    if msg["type"] != "message":
                        continue
                    point_id = int(msg["data"])
                    data = self.r.hgetall(f"point:{point_id}")
                    if not data:
                        continue
                    value = float(data.get("value", 0))
                    ts = int(data.get("ts", time.time()))

                    prev = prev_values.get(point_id)
                    if prev is not None:
                        deadband = float(data.get("deadband", 0))
                        if deadband > 0 and abs(value - prev) < deadband:
                            continue

                    prev_values[point_id] = value
                    self.volume.write("values", {
                        "ts": ts,
                        "point_id": point_id,
                        "value": value
                    })
            except Exception as e:
                self.log.error(f"Writer values error: {e}")
            time.sleep(2)

    def _watchdog(self):
        """перевірка heartbeat"""
        while self.running:
            elapsed = time.time() - self.last_heartbeat
            if elapsed > self.timeout_sec:
                self.log.error(
                    f"Redis heartbeat lost ({elapsed:.1f}s) — reconnecting...")
                try:
                    self.r = self._connect()
                    self.r.ping()
                    self.last_heartbeat = time.time()
                    self.log.info("Redis reconnected")
                except Exception as e:
                    self.log.error(f"Reconnect failed: {e}")
            time.sleep(3)

    def start(self):
        self.running = True

        threading.Thread(target=self._listen_clock,  daemon=True).start()
        threading.Thread(target=self._listen_events, daemon=True).start()
        threading.Thread(target=self._listen_values, daemon=True).start()
        threading.Thread(target=self._watchdog,      daemon=True).start()

        self.log.info(
            "Writer started — listening bus:event, bus:data, bus:clock")
