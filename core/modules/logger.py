import logging
import os
from logging.handlers import RotatingFileHandler


def setup_logger(name, config):
    log_level_str = config["system"].get("log_level", "INFO")
    log_level = getattr(logging, log_level_str.upper(), logging.INFO)

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    # --- КОНСОЛЬ ---
    console = logging.StreamHandler()
    console.setLevel(log_level)
    console.setFormatter(formatter)
    logger.addHandler(console)

    # --- ФАЙЛ (тільки INFO і вище) ---
    if log_level_str.upper() == "INFO":
        log_cfg = config["system"].get("log", {})
        max_bytes = log_cfg.get("max_bytes", 1048576)
        backup_count = log_cfg.get("backup_count", 3)

        log_dir = "/app/log"
        os.makedirs(log_dir, exist_ok=True)

        log_file = os.path.join(log_dir, f"{name}.log")

        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger
