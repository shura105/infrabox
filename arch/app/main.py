import json
import logging
import os
import threading
from logging.handlers import RotatingFileHandler

import uvicorn

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
