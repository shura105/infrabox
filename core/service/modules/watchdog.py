import threading
import time
import logging
import redis

log = logging.getLogger("core.watchdog")


class RedisWatchdog:
    def __init__(self, r, timeout_sec=5):
        self.r = r
        self.timeout_sec = timeout_sec
        self.last_heartbeat = time.time()
        self.healthy = True
        # self.reconnected = False
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()
        log.info("Watchdog started")

    def _run(self):
        while True:
            try:
                pubsub = self.r.pubsub()
                pubsub.subscribe("bus:clock")
                log.info("Watchdog subscribed to bus:clock")
                # self.reconnected = True

                for msg in pubsub.listen():
                    if msg["type"] == "message":
                        self.last_heartbeat = time.time()

            except Exception as e:
                log.warning(f"Watchdog lost pubsub connection: {e}")

            # пауза перед спробою реконекту
            time.sleep(2)

    def check(self):
        elapsed = time.time() - self.last_heartbeat

        if elapsed > self.timeout_sec:
            if self.healthy:
                log.error(f"Redis heartbeat lost ({elapsed:.1f}s) → UNHEALTHY")
            self.healthy = False
        else:
            if not self.healthy:
                log.info("Redis heartbeat restored → HEALTHY")
            self.healthy = True

        return self.healthy
