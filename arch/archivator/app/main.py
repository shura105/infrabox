import json
import logging
import os
import time
import threading
from logging.handlers import RotatingFileHandler

import redis as redis_lib
import uvicorn

REDIS_HOST = os.environ.get("REDIS_HOST", "infrabox-redis")
REDIS_PORT = int(os.environ.get("REDIS_PORT", 6379))


def _heartbeat_thread():
    r = None
    while True:
        try:
            if r is None:
                r = redis_lib.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
            r.set("heartbeat:infrabox-arch", int(time.time()), ex=5)
        except Exception:
            r = None
        time.sleep(1)

from modules.volume import Volume
from modules.writer import Writer
from modules.api import app, set_volume

CONFIG_PATH = "/app/config/archive_config.json"


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def setup_logger():
    logger = logging.getLogger("arch")
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    os.makedirs("/app/log", exist_ok=True)
    file_handler = RotatingFileHandler(
        "/app/log/arch.log",
        maxBytes=1048576,
        backupCount=3
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


def main():
    log = setup_logger()
    log.info("Archivator started")
    threading.Thread(target=_heartbeat_thread, daemon=True).start()

    config = load_config()

    # --- VOLUME ---
    volume = Volume(config, data_dir="/app/data")
    log.info(f"Volume opened: {volume.get_current_meta()['volume']}")

    # --- WRITER ---
    writer = Writer(config, volume, log)
    writer.start()

    # --- API ---
    set_volume(volume)

    port = config["api"]["port"]
    log.info(f"API starting on port {port}")

    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
