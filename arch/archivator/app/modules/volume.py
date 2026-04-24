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
        self.max_days = config["volume"].get("max_days", 30)
        self.compression = config["compression"]["enabled"]

        self.current_dir = None
        self.opened_at = None
        self.record_count = 0
        self.log = logging.getLogger("arch.volume")

        # lock ініціалізується ДО _open() щоб write() не падав при race
        self._lock = threading.Lock()
        self.recording = self._load_state()
        self._close_orphans()
        self._open()

    def _close_orphans(self):
        """При старті закриває томи, що залишились відкритими після падіння/перезапуску."""
        if not os.path.exists(self.data_dir):
            return
        for name in os.listdir(self.data_dir):
            vol_path = os.path.join(self.data_dir, name)
            if not os.path.isdir(vol_path):
                continue
            meta_path = os.path.join(vol_path, "meta.json")
            gz_path   = meta_path + ".gz"
            try:
                if os.path.exists(meta_path):
                    with open(meta_path) as f:
                        meta = json.load(f)
                elif os.path.exists(gz_path):
                    import gzip as _gz
                    with _gz.open(gz_path, "rt") as f:
                        meta = json.load(f)
                else:
                    continue
                if meta.get("status") == "open":
                    # закриваємо: встановлюємо current_dir тимчасово
                    self.current_dir = vol_path
                    self.opened_at   = datetime.fromisoformat(meta["opened_at"])
                    self.record_count = meta.get("record_count", 0)
                    self._write_meta("close")
                    self._write_session("close", reason="orphan_close")
                    self.log.info(f"Orphan volume closed: {name}")
            except Exception as e:
                self.log.warning(f"Could not close orphan {name}: {e}")

    def _volume_name(self):
        return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    def _open(self):
        name = self._volume_name()
        self.current_dir = os.path.join(self.data_dir, name)
        os.makedirs(self.current_dir, exist_ok=True)

        self.opened_at = datetime.now()
        self.record_count = 0

        self._write_config_snap()
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

    def prune_old_volumes(self):
        """Видаляє томи старші за max_days днів. Повертає кількість видалених."""
        if self.max_days <= 0:
            return 0
        cutoff = datetime.now().timestamp() - self.max_days * 86400
        current = os.path.basename(self.current_dir)
        try:
            all_dirs = [
                d for d in os.listdir(self.data_dir)
                if os.path.isdir(os.path.join(self.data_dir, d)) and d != current
            ]
        except Exception as e:
            self.log.error(f"prune_old_volumes list error: {e}")
            return 0
        to_delete = []
        for d in all_dirs:
            try:
                vol_ts = datetime.strptime(d, "%Y-%m-%d_%H-%M-%S").timestamp()
                if vol_ts < cutoff:
                    to_delete.append(d)
            except ValueError:
                pass  # не наш формат — пропускаємо
        deleted = set()
        for v in to_delete:
            try:
                shutil.rmtree(os.path.join(self.data_dir, v))
                deleted.add(v)
                self.log.info(f"Pruned old volume: {v}")
            except Exception as e:
                self.log.error(f"Failed to prune volume {v}: {e}")
        if deleted:
            self._prune_sessions(deleted)
        return len(deleted)

    def _prune_sessions(self, deleted_volumes):
        sessions_path = os.path.join(self.data_dir, "sessions.json")
        if not os.path.exists(sessions_path):
            return
        try:
            with open(sessions_path) as f:
                lines = f.readlines()
            kept = []
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get("volume") not in deleted_volumes:
                        kept.append(line + "\n")
                except json.JSONDecodeError:
                    pass
            with open(sessions_path, "w") as f:
                f.writelines(kept)
        except Exception as e:
            self.log.error(f"_prune_sessions error: {e}")

    def rotate(self):
        if self.record_count >= self.max_records:
            reason = "max_records"
        elif (datetime.now() - self.opened_at).total_seconds() / 3600 >= self.max_duration_hours:
            reason = "max_duration"
        else:
            reason = "manual"

        self._close(reason=reason)
        self._open()
        self.prune_old_volumes()

    def write(self, stream, record):
        if not self.recording:
            return
        with self._lock:
            if self.should_rotate():
                self.rotate()
            if stream == "values":
                # Зберігаємо per-point для швидкого читання: values_{point_id}.json
                # замість одного великого values.json (~100× менший файл на точку)
                pid = record.get("point_id")
                if pid is not None:
                    self._write_file(f"values_{pid}.json", record)
            else:
                self._write_file(f"{stream}.json", record)
            self.record_count += 1

    def stop(self):
        """Зупинка запису через API — атомарна операція."""
        with self._lock:
            self._close(reason="manual_stop")
            self._save_state(False)
            self.recording = False

    def start(self):
        """Старт запису через API — атомарна операція."""
        with self._lock:
            self._open()
            self._save_state(True)
            self.recording = True

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
