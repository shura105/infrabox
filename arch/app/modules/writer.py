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

        self._ts_lock = threading.Lock()
        self.last_archive_ts = {}  # ініціалізується один раз

        self.points_meta = self._load_points_meta()

        # r ініціалізується останнім — після всіх інших полів
        self.r = self._connect()

    def _connect(self):
        r_cfg = self.config["redis"]
        return redis.Redis(
            host=r_cfg["host"],
            port=r_cfg["port"],
            db=r_cfg.get("db", 0),
            decode_responses=True
        )

    def _load_points_meta(self):
        try:
            with open("/app/config/points.json") as f:
                points = json.load(f)
            return {p["id"]: p for p in points}
        except Exception as e:
            self.log.error(f"Failed to load points.json: {e}")
            return {}

    def _should_archive(self, point_id, value, prev_value):
        meta = self.points_meta.get(point_id, {})

        if meta.get("onArchive", 1) == 0:
            return False

        archive_on_change = meta.get("archive_on_change", 1)
        archive_interval = meta.get("archive_interval", 0)
        deadband = meta.get("deadband", 0)

        now = time.time()

        # читаємо last_archive_ts під локом — запобігаємо dirty read
        with self._ts_lock:
            last_ts = self.last_archive_ts.get(point_id, 0)

        if archive_on_change == 0:
            return True

        if prev_value is None:
            changed = True
        elif deadband > 0:
            changed = abs(value - prev_value) >= deadband
        else:
            changed = value != prev_value

        interval_fired = archive_interval > 0 and (now - last_ts) >= archive_interval

        return changed or interval_fired

    def _listen_clock(self):
        while self.running:
            try:
                r = self.r  # локальна копія — захист від watchdog reconnect
                pubsub = r.pubsub()
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
        while self.running:
            try:
                r = self.r
                pubsub = r.pubsub()
                pubsub.subscribe("bus:event")
                for msg in pubsub.listen():
                    if not self.running:
                        break
                    if msg["type"] != "message":
                        continue
                    event = json.loads(msg["data"])

                    point_id = event.get("point_id")
                    meta = self.points_meta.get(point_id, {})
                    if meta.get("onArchive", 1) == 0:
                        continue

                    stream = "selfdiag" if event.get("system") == "selfDiag" else "events"
                    self.volume.write(stream, event)
            except Exception as e:
                self.log.error(f"Writer events error: {e}")
            time.sleep(2)

    def _listen_values(self):
        prev_values = {}
        while self.running:
            try:
                r = self.r
                pubsub = r.pubsub()
                pubsub.subscribe("bus:data")
                for msg in pubsub.listen():
                    if not self.running:
                        break
                    if msg["type"] != "message":
                        continue

                    point_id = int(msg["data"])
                    data = r.hgetall(f"point:{point_id}")
                    if not data:
                        continue

                    value = float(data.get("value", 0))
                    ts = int(data.get("ts", time.time()))
                    prev = prev_values.get(point_id)

                    if self._should_archive(point_id, value, prev):
                        prev_values[point_id] = value
                        with self._ts_lock:
                            self.last_archive_ts[point_id] = time.time()
                        self.volume.write("values", {
                            "ts": ts,
                            "point_id": point_id,
                            "value": value
                        })

            except Exception as e:
                self.log.error(f"Writer values error: {e}")
            time.sleep(2)

    def _watchdog(self):
        while self.running:
            elapsed = time.time() - self.last_heartbeat
            if elapsed > self.timeout_sec:
                self.log.error(f"Redis heartbeat lost ({elapsed:.1f}s) — reconnecting...")
                try:
                    new_r = self._connect()
                    new_r.ping()
                    # атомарна заміна — listener треди беруть локальну копію self.r
                    self.r = new_r
                    self.last_heartbeat = time.time()
                    self.log.info("Redis reconnected")
                except Exception as e:
                    self.log.error(f"Reconnect failed: {e}")
            time.sleep(3)

    def _interval_archiver(self):
        while self.running:
            now = time.time()
            for point_id, meta in self.points_meta.items():
                if meta.get("onArchive", 1) == 0:
                    continue
                archive_interval = meta.get("archive_interval", 0)
                if archive_interval == 0:
                    continue

                with self._ts_lock:
                    last_ts = self.last_archive_ts.get(point_id, 0)
                    if now - last_ts < archive_interval:
                        continue
                    self.last_archive_ts[point_id] = now

                try:
                    r = self.r
                    data = r.hgetall(f"point:{point_id}")
                    if not data:
                        continue
                    value = float(data.get("value", 0))
                    # використовуємо поточний час — Redis ts може бути застарілим
                    # якщо значення рідко проходить дедбенд (напр. disk_space)
                    self.volume.write("values", {
                        "ts": int(now),
                        "point_id": point_id,
                        "value": value
                    })
                except Exception as e:
                    self.log.error(f"Interval archiver error: {e}")

            time.sleep(10)

    def start(self):
        self.running = True
        threading.Thread(target=self._interval_archiver, daemon=True).start()
        threading.Thread(target=self._listen_clock,      daemon=True).start()
        threading.Thread(target=self._listen_events,     daemon=True).start()
        threading.Thread(target=self._listen_values,     daemon=True).start()
        threading.Thread(target=self._watchdog,          daemon=True).start()

        self.log.info(f"Writer started — {len(self.points_meta)} points loaded")