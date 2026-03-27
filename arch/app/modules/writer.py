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

        r_cfg = config["redis"]
        self.r = redis.Redis(
            host=r_cfg["host"],
            port=r_cfg["port"],
            db=r_cfg.get("db", 0),
            decode_responses=True
        )

    def _listen_events(self):
        """bus:event → events.json та selfdiag.json"""
        pubsub = self.r.pubsub()
        pubsub.subscribe("bus:event")

        for msg in pubsub.listen():
            if not self.running:
                break
            if msg["type"] != "message":
                continue

            try:
                event = json.loads(msg["data"])
                stream = "selfdiag" if event.get(
                    "system") == "selfDiag" else "events"
                self.volume.write(stream, event)
            except Exception as e:
                self.log.error(f"Writer events error: {e}")

    def _listen_values(self):
        """bus:data → values.json (тільки при зміні deadband)"""
        pubsub = self.r.pubsub()
        pubsub.subscribe("bus:data")

        # завантажуємо deadband з Redis
        prev_values = {}

        for msg in pubsub.listen():
            if not self.running:
                break
            if msg["type"] != "message":
                continue

            try:
                point_id = int(msg["data"])
                data = self.r.hgetall(f"point:{point_id}")

                if not data:
                    continue

                value = float(data.get("value", 0))
                ts = int(data.get("ts", time.time()))

                # deadband перевірка
                prev = prev_values.get(point_id)
                if prev is not None:
                    deadband = float(data.get("deadband", 0))
                    if deadband > 0 and abs(value - prev) < deadband:
                        continue

                prev_values[point_id] = value

                record = {
                    "ts": ts,
                    "point_id": point_id,
                    "value": value
                }
                self.volume.write("values", record)

            except Exception as e:
                self.log.error(f"Writer values error: {e}")

    def start(self):
        self.running = True

        t1 = threading.Thread(target=self._listen_events, daemon=True)
        t2 = threading.Thread(target=self._listen_values, daemon=True)

        t1.start()
        t2.start()

        self.log.info("Writer started — listening bus:event and bus:data")
