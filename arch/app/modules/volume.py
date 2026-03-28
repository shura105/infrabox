import json
import os
import gzip
import shutil
import logging
import threading
from datetime import datetime

STATE_PATH = "/app/config/state.json"


class Volume:
    def __init__(self, config, data_dir="/app/data"):
        self.config = config
        self.data_dir = data_dir
        self.max_records = config["volume"]["max_records"]
        self.max_duration_hours = config["volume"]["max_duration_hours"]
        self.compression = config["compression"]["enabled"]

        self.current_dir = None
        self.opened_at = None
        self.record_count = 0
        self.log = logging.getLogger("arch.volume")

        self._open()
        self._lock = threading.Lock()
        self.recording = self._load_state()

    def _volume_name(self):
        return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    def _open(self):
        name = self._volume_name()
        self.current_dir = os.path.join(self.data_dir, name)
        os.makedirs(self.current_dir, exist_ok=True)

        self.opened_at = datetime.now()
        self.record_count = 0

        # --- знімок конфігурації ---
        self._write_config_snap()

        # --- мета тому ---
        self._write_meta("open")

        self._write_session("open")

        self.log.info(f"Volume opened: {name}")

    def _close(self, reason="manual"):
        self._write_meta("close")
        self._write_session("close", reason=reason)
        if self.compression:
            self._compress()
        self.log.info(
            f"Volume closed: {os.path.basename(self.current_dir)} reason={reason}")

    def _write_meta(self, status):
        meta = {
            "status": status,
            "opened_at": self.opened_at.isoformat(),
            "closed_at": datetime.now().isoformat() if status == "close" else None,
            "record_count": self.record_count
        }
        self._write_file("meta.json", meta, append=False)

    def _write_config_snap(self):
        config_path = "/app/config/points.json"
        if os.path.exists(config_path):
            with open(config_path) as f:
                points = json.load(f)
            self._write_file("config_snap.json", points, append=False)

    def _write_session(self, event, reason=None):
        record = {
            "event": event,
            "volume": os.path.basename(self.current_dir),
            "ts": datetime.now().isoformat(),
            "records": self.record_count
        }
        if reason:
            record["reason"] = reason
        sessions_path = os.path.join(self.data_dir, "sessions.json")
        with open(sessions_path, "a") as f:
            f.write(json.dumps(record) + "\n")

    def _compress(self):
        for fname in os.listdir(self.current_dir):
            if fname.endswith(".json"):
                fpath = os.path.join(self.current_dir, fname)
                gz_path = fpath + ".gz"
                with open(fpath, "rb") as f_in:
                    with gzip.open(gz_path, "wb") as f_out:
                        shutil.copyfileobj(f_in, f_out)
                os.remove(fpath)
                self.log.info(f"Compressed: {fname}")

    def _write_file(self, filename, data, append=True):
        path = os.path.join(self.current_dir, filename)
        if append:
            with open(path, "a") as f:
                f.write(json.dumps(data) + "\n")
        else:
            with open(path, "w") as f:
                json.dump(data, f, indent=2)

    def should_rotate(self):
        elapsed_hours = (datetime.now() -
                         self.opened_at).total_seconds() / 3600
        return (
            self.record_count >= self.max_records or
            elapsed_hours >= self.max_duration_hours
        )

    def rotate(self):
        elapsed_hours = (datetime.now() -
                         self.opened_at).total_seconds() / 3600

        if self.record_count >= self.max_records:
            reason = "max_records"
        elif elapsed_hours >= self.max_duration_hours:
            reason = "max_duration"
        else:
            reason = "manual"

        self._close(reason=reason)
        self._open()

    def write(self, stream, record):
        if not self.recording:
            return
        with self._lock:
            if self.should_rotate():
                self.rotate()
            self._write_file(f"{stream}.json", record)
            self.record_count += 1

    def get_current_meta(self):
        return {
            "volume": os.path.basename(self.current_dir),
            "opened_at": self.opened_at.isoformat(),
            "record_count": self.record_count,
            "max_records": self.max_records,
            "max_duration_hours": self.max_duration_hours
        }

    def _load_state(self):
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH) as f:
                return json.load(f).get("recording", True)
        return True

    def _save_state(self, recording):
        with open(STATE_PATH, "w") as f:
            json.dump({"recording": recording}, f)
