import json
import math
import os
import re
import subprocess
import threading
import time
from threading import Lock

import redis

from modules.mqtt import start_mqtt
from modules.quality import process_quality
from modules.init import load_points, load_systems
from modules.watchdog import RedisWatchdog
from modules.logger import setup_logger

CONFIG_PATH = "/app/config/sys_params.json"

buffer = {}
buffer_lock = Lock()


def _run_shell(cmd: str, point_id, logger):
    """Run shell command in background thread; log non-zero exit."""
    try:
        r = subprocess.run(cmd, shell=True, timeout=15,
                           capture_output=True, text=True)
        if r.returncode != 0:
            logger.warning(
                f"[CTRL] shell pid={point_id} rc={r.returncode}: "
                f"{(r.stderr or r.stdout).strip()[:200]}")
    except Exception as e:
        logger.warning(f"[CTRL] shell pid={point_id} error: {e}")



def _preprocess_formula(expr: str) -> str:
    """Convert C-style ternary  a ? b : c  →  (b) if (a) else (c)."""
    m = re.match(r'^(.*\S)\s*\?\s*(\S.*?)\s*:\s*(\S.*)$', expr.strip(), re.DOTALL)
    if m:
        cond, true_v, false_v = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        return f"({true_v}) if ({cond}) else ({false_v})"
    return expr

# глобальний логер — ініціалізується в main()
log = None


# --- CONFIG ---
def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


# --- REDIS ---
def get_redis(cfg):
    r_cfg = cfg["bootstrap"]["redis"]

    r = redis.Redis(
        host=r_cfg["host"],
        port=r_cfg["port"],
        db=r_cfg.get("db", 0),
        decode_responses=True
    )

    retries = 10
    for attempt in range(retries):
        try:
            r.ping()
            log.info("Redis connection established")
            return r
        except (redis.ConnectionError, redis.TimeoutError):
            log.warning(f"Redis unavailable, retry {attempt + 1}/{retries}...")
            time.sleep(2)

    log.error("Redis unavailable after all retries — exiting")
    raise SystemExit(1)


# --- REDIS ONE SHOT ---
def try_reconnect_redis(cfg):
    r_cfg = cfg["bootstrap"]["redis"]
    r = redis.Redis(
        host=r_cfg["host"],
        port=r_cfg["port"],
        db=r_cfg.get("db", 0),
        decode_responses=True
    )
    r.ping()
    return r


# --- BUILD MQTT TOPIC ---
def build_topic(meta):
    return f"{meta['object']}/{meta['system']}/{meta['pointname']}/{meta['id']}"


# --- CLOCK ---
def tick_clock(r):
    ts = int(time.time())
    r.set("system:clock", ts)
    r.publish("bus:clock", ts)


# --- MQTT CALLBACK ---
def mqtt_callback(buffer, lock):
    def on_message(topic, payload_raw):
        # ignore command topics published by the control block
        if topic.startswith("cmd/"):
            return

        try:
            payload = json.loads(payload_raw)
        except json.JSONDecodeError as e:
            log.warning(f"Bad JSON on topic {topic}: {e}")
            return

        point_id = payload.get("id")
        value = payload.get("value")
        ts = payload.get("ts", int(time.time()))

        if point_id is None:
            log.warning(f"Missing point_id on topic {topic}")
            return

        if value is None:
            log.warning(f"Missing value for point {point_id} on topic {topic}")
            return

        try:
            value = float(value)
        except (TypeError, ValueError):
            log.warning(f"Invalid value '{value}' for point {point_id}")
            return

        with lock:
            buffer[point_id] = {
                "value": value,
                "ts": ts,
                "topic": topic
            }

    return on_message


# --- RESET TO INIT ---
def reset_meta(meta_cache):
    for meta in meta_cache.values():
        meta["state"] = "INIT"
        meta["last_update_ts"] = 0
        meta["last_value"] = None
    log.info("All points reset to INIT")


# --- CLEAR REDIS STRUCTURE ---
def clear_redis(r, meta_cache):
    keys = [f"point:{pid}" for pid in meta_cache]
    keys += ["system:clock", "system:buffer_size", "system:passed_deadband"]
    r.delete(*keys)
    log.info("Redis structure cleared")


# --- MAIN ---
def main():
    global log

    config = load_config()
    log = setup_logger("core", config)
    log.info("Core started")

    meta_cache = load_points()
    systems_cache = load_systems()
    log.info(f"Loaded {len(systems_cache)} systems")

    def _watch_systems():
        path = "/app/config/systems.json"
        try:
            last_mtime = os.path.getmtime(path)
        except Exception:
            last_mtime = 0
        while True:
            time.sleep(60)
            try:
                mtime = os.path.getmtime(path)
                if mtime != last_mtime:
                    last_mtime = mtime
                    new = load_systems()
                    systems_cache.clear()
                    systems_cache.update(new)
                    log.info(f"systems.json reloaded — {len(systems_cache)} systems")
            except Exception as e:
                log.error(f"systems.json watch error: {e}")

    threading.Thread(target=_watch_systems, daemon=True).start()

    r = get_redis(config)

    log.info(f"Loaded {len(meta_cache)} points")

    # --- INIT operation_mode points in Redis (not MQTT-driven, never written otherwise) ---
    for pid, meta in meta_cache.items():
        if meta.get("type") != "operation_mode":
            continue
        key = f"point:{pid}"
        if not r.exists(key):
            r.hset(key, mapping={
                "value": "0",
                "quality": "INIT",
                "type": "operation_mode",
                "object": meta["object"],
                "system": meta["system"],
                "pointname": meta["pointname"],
            })
            r.publish("bus:data", str(pid))

    # start_mqtt(config, mqtt_callback(buffer, buffer_lock), log)
    mqtt_client = start_mqtt(config, mqtt_callback(buffer, buffer_lock))

    # --- WATCHDOG ---
    watchdog = RedisWatchdog(r, timeout_sec=5)
    watchdog.start()

    # --- SYSTEM TICK ---
    tick = config["system"]["system_tick_ms"] / 1000

    log.info(f"System tick: {tick}s")

    _hb_tick = 0
    _HB_INTERVAL = 25  # update heartbeat points every N ticks
    # built dynamically from points.json — system points with hb_service field
    _HB_POINTS = {
        meta["hb_service"]: pid
        for pid, meta in meta_cache.items()
        if meta.get("hb_service")
    }

    # --- SCHEDULER STATE (multi_timer server-side) ---
    sched_cache      = {}   # {el_id: {"point_id": int, "schedule": [{"on":..,"off":..}]}}
    sched_state      = {}   # {el_id: bool | None}  — last known ON/OFF per element
    sched_reload_ctr = 0.0  # countdown seconds until next Redis reload
    SCHED_RELOAD_S   = 60.0

    # --- MAIN LOOP ---
    while True:

        # --- WATCHDOG CHECK ---
        if not watchdog.check():
            reset_meta(meta_cache)

            log.error("Redis unhealthy — attempting reconnect...")
            try:
                r_new = try_reconnect_redis(config)
                clear_redis(r_new, meta_cache)

                r = r_new
                watchdog.r = r_new
                watchdog.last_heartbeat = time.time()

                log.info("Redis reconnected — ready")

            except Exception as e:
                log.error(f"Reconnect failed: {e}")
                time.sleep(5)

            continue

        try:
            # --- CLOCK ---
            tick_clock(r)

            # --- READ BUFFER ---
            with buffer_lock:
                updates = list(buffer.items())
                buffer.clear()

            # --- PROCESS BATCH ---
            passed = 0
            batch_pipe = r.pipeline()
            batch_has = False

            for point_id, data in updates:

                if point_id not in meta_cache:
                    continue

                meta = meta_cache[point_id]

                # --- topic validation ---
                expected_topic = build_topic(meta)

                if data["topic"] != expected_topic:
                    continue

                value = data["value"]
                ts = data["ts"]

                meta["last_update_ts"] = int(time.time() * 1000)

                # --- DEADBAND / discrete change-only ---
                deadband = meta.get("deadband", 0)
                last_value = meta.get("last_value")

                if meta.get("type") == "discrete":
                    # discrete: write only on transitions (no deadband concept)
                    if last_value is not None and value == last_value:
                        continue
                elif last_value is not None and deadband > 0:
                    if abs(value - last_value) < deadband:
                        continue

                meta["last_value"] = value
                passed += 1

                # --- QUALITY ---
                result = process_quality(
                    point_id=point_id,
                    value=value,
                    meta=meta,
                    config=config
                )

                if result:
                    meta["state"] = result["new_state"]
                    meta["last_change_ts"] = result["ts"]
                elif meta.get("type") in ("discrete", "operation_mode"):
                    # value changed but quality didn't (e.g. severity=none: 0→1 stays GOOD)
                    # still need to reset last_change_ts so elapsed timer counts correctly
                    meta["last_change_ts"] = int(time.time() * 1000)

                # --- REDIS WRITE + PUB DATA (queued; flushed once after loop) ---
                key = f"point:{point_id}"
                lim = meta["limits"]
                mapping = {
                    "value": value,
                    "ts": ts,
                    "quality": meta["state"],
                    "type": meta.get("type", "analog"),
                    "object": meta["object"],
                    "system": meta["system"],
                    "pointname": meta["pointname"],
                    "unit": meta.get("unit", ""),
                    "min":       lim["min"],
                    "max":       lim["max"],
                    "warn_min":  lim["warn_min"],
                    "warn_max":  lim["warn_max"],
                    "alarm_min": lim["alarm_min"],
                    "alarm_max": lim["alarm_max"],
                }
                if meta.get("type") == "discrete":
                    mapping["label_0"]      = meta.get("label_0", "")
                    mapping["label_1"]      = meta.get("label_1", "")
                    mapping["normal_value"] = meta.get("normal_value", 0)
                    mapping["severity"]     = meta.get("severity", "none")
                # always write last_change_ts for timer-capable types
                if meta.get("type") in ("discrete", "operation_mode") or result:
                    mapping["last_change_ts"] = meta["last_change_ts"]
                batch_pipe.hset(key, mapping=mapping)
                batch_pipe.publish("bus:data", point_id)

                if result:
                    batch_pipe.publish("bus:event", json.dumps(result))

                batch_has = True

                # if result:
                #    log.info(f"[EVENT] {result}")

            if batch_has:
                batch_pipe.execute()

            # --- CALCULATED POINTS ---
            _CALC_NS = {
                "__builtins__": {},
                "sin": math.sin,  "cos": math.cos,  "tan": math.tan,
                "asin": math.asin,"acos": math.acos,"atan": math.atan,"atan2": math.atan2,
                "sqrt": math.sqrt,"log": math.log,  "log10": math.log10,"log2": math.log2,
                "exp": math.exp,  "pow": math.pow,  "abs": abs,
                "round": round,   "floor": math.floor,"ceil": math.ceil,
                "min": min,       "max": max,
                "pi": math.pi,    "e": math.e,
                "True": True,     "False": False,
            }
            _QRANK = {"GOOD": 0, "INIT": 1, "WARN": 2, "ALARM": 3}
            now_ms_c = int(time.time() * 1000)
            _CALC_NS["now"] = now_ms_c // 1000
            def _age_fn(pid, _cache=meta_cache, _now=now_ms_c):
                m = _cache.get(int(pid))
                if not m or not m.get("last_change_ts"):
                    return 0.0
                return max(0.0, (_now - m["last_change_ts"]) / 1000.0)
            _CALC_NS["age"] = _age_fn
            calc_pipe = r.pipeline()
            calc_has  = False

            for c_id, c_meta in meta_cache.items():
                if c_meta.get("type") != "calculated":
                    continue
                formula = c_meta.get("formula", "").strip()
                if not formula:
                    continue

                refs    = [int(m) for m in re.findall(r'\$(\d+)', formula)]
                worst_q = "GOOD"
                valid   = True
                for ref_id in set(refs):
                    rm = meta_cache.get(ref_id)
                    rq = rm.get("state", "NODATA") if rm else "NODATA"
                    rv = rm.get("last_value")      if rm else None
                    if rv is None or rq in ("UNCERT", "NODATA"):
                        valid = False; break
                    if _QRANK.get(rq, 3) > _QRANK.get(worst_q, 0):
                        worst_q = rq

                if not valid:
                    new_val = None
                    new_q   = "UNCERT"
                else:
                    try:
                        expr = re.sub(
                            r'\$(\d+)',
                            lambda m: str(float(meta_cache[int(m.group(1))]["last_value"])),
                            formula
                        )
                        expr = _preprocess_formula(expr)
                        if "__" in expr:
                            continue
                        result = eval(expr, _CALC_NS, {})
                        new_val = round(float(result), 6)
                        new_q   = worst_q
                    except Exception:
                        new_val = None
                        new_q   = "UNCERT"

                if new_val == c_meta.get("last_value") and new_q == c_meta.get("state"):
                    continue

                c_meta["last_value"]     = new_val
                c_meta["state"]          = new_q
                c_meta["last_update_ts"] = now_ms_c

                lim = c_meta["limits"]
                calc_pipe.hset(f"point:{c_id}", mapping={
                    "value":     str(new_val) if new_val is not None else "",
                    "ts":        str(now_ms_c),
                    "quality":   new_q,
                    "type":      "calculated",
                    "object":    c_meta["object"],
                    "system":    c_meta["system"],
                    "pointname": c_meta["pointname"],
                    "unit":      c_meta.get("unit", ""),
                    "min":       lim["min"],  "max":       lim["max"],
                    "warn_min":  lim["warn_min"], "warn_max":  lim["warn_max"],
                    "alarm_min": lim["alarm_min"], "alarm_max": lim["alarm_max"],
                })
                calc_pipe.publish("bus:data", c_id)
                calc_has = True

            if calc_has:
                calc_pipe.execute()

            # --- SYNC operation_mode points (Redis → systems_cache + systems.json) ---
            # set_point_value() writes directly to Redis (bypasses MQTT/buffer),
            # so we poll here once per tick to catch UI-driven mode changes.
            _sys_path    = "/app/config/systems.json"
            _sys_changed = False
            for _om_pid, _om_meta in meta_cache.items():
                if _om_meta.get("type") != "operation_mode":
                    continue
                _sys_id = _om_meta.get("system")
                if not _sys_id:
                    continue
                _raw  = r.hgetall(f"point:{_om_pid}")
                _val  = int(_raw.get("value", 0)) if _raw else 0
                _mode = "auto" if _val == 1 else "manual"
                _om_meta["last_value"] = _val
                sys_entry = systems_cache.setdefault(_sys_id, {})
                if sys_entry.get("operation_mode") != _mode:
                    sys_entry["operation_mode"] = _mode
                    _sys_changed = True
                    log.info(f"[OPMODE] system={_sys_id} → {_mode}")
            if _sys_changed:
                try:
                    _tmp = _sys_path + ".tmp"
                    with open(_tmp, "w") as _sf:
                        json.dump(list(systems_cache.values()), _sf,
                                  indent=2, ensure_ascii=False)
                    os.replace(_tmp, _sys_path)
                except Exception as _e:
                    log.error(f"systems.json persist error: {_e}")

            # --- CONTROL POINTS ---
            now_ms_ctrl = int(time.time() * 1000)
            ctrl_pipe = r.pipeline()
            ctrl_has  = False
            # keep formula 'now' and 'age()' current for control conditions
            _CALC_NS["now"] = now_ms_ctrl // 1000
            def _age_fn_ctrl(pid, _cache=meta_cache, _now=now_ms_ctrl):
                m = _cache.get(int(pid))
                if not m or not m.get("last_change_ts"):
                    return 0.0
                return max(0.0, (_now - m["last_change_ts"]) / 1000.0)
            _CALC_NS["age"] = _age_fn_ctrl

            def _eval_ctrl_cond(fml, pid):
                """Evaluate a boolean control condition; return True/False or None on error."""
                refs = [int(x) for x in re.findall(r'\$(\d+)', fml)]
                for ref_id in set(refs):
                    rm = meta_cache.get(ref_id)
                    if (not rm or rm.get("last_value") is None or
                            rm.get("state") in ("INIT", "UNCERT", "NODATA")):
                        return None  # no data yet or unreliable → hold
                try:
                    expr = re.sub(
                        r'\$(\d+)',
                        lambda m: str(float(meta_cache[int(m.group(1))]["last_value"])),
                        fml
                    )
                    if "__" in expr:
                        return None
                    return bool(eval(expr, _CALC_NS, {}))
                except Exception as _fe:
                    log.warning(f"[CTRL] cond eval error pid={pid}: {_fe}")
                    return None

            for c_id, c_meta in meta_cache.items():
                if c_meta.get("type") != "control":
                    continue

                # helper: write status point to Redis
                def _ctrl_write(pid=c_id, meta=c_meta):
                    _Q = {
                        "INIT": "INIT", "GOOD": "GOOD", "WARN": "WARN",
                        "ALARM": "ALARM", "NODATA": "NODATA",
                    }
                    q = _Q.get(meta["ctrl_status"], "NODATA")
                    lim = meta["limits"]
                    meta["last_change_ts"] = now_ms_ctrl
                    ctrl_pipe.hset(f"point:{pid}", mapping={
                        "value":          str(meta["cmd_value"]) if meta["cmd_value"] is not None else "",
                        "ts":             str(now_ms_ctrl // 1000),
                        "quality":        q,
                        "type":           "control",
                        "object":         meta["object"],
                        "system":         meta["system"],
                        "pointname":      meta["pointname"],
                        "unit":           "",
                        "last_change_ts": now_ms_ctrl,
                        "min":       lim["min"],  "max":       lim["max"],
                        "warn_min":  lim["warn_min"], "warn_max":  lim["warn_max"],
                        "alarm_min": lim["alarm_min"], "alarm_max": lim["alarm_max"],
                    })
                    ctrl_pipe.publish("bus:data", pid)

                # --- PENDING: check feedback ---
                if c_meta["ctrl_status"] == "WARN":
                    fb_id  = c_meta.get("feedback_id")
                    fb_meta = meta_cache.get(fb_id) if fb_id else None
                    if fb_meta and fb_meta.get("last_value") is not None:
                        if int(fb_meta["last_value"]) == c_meta["cmd_value"]:
                            c_meta["ctrl_status"] = "GOOD"
                            _ctrl_write()
                            ctrl_has = True
                            continue

                    ticks = c_meta["feedback_ticks_left"] - 1
                    c_meta["feedback_ticks_left"] = ticks
                    if ticks <= 0:
                        c_meta["ctrl_status"] = "ALARM"
                        _ctrl_write()
                        ctrl_pipe.publish("bus:event", json.dumps({
                            "event": "CTRL_TIMEOUT", "point_id": c_id,
                            "object": c_meta["object"], "system": c_meta["system"],
                            "ts": now_ms_ctrl,
                        }))
                        ctrl_has = True
                    continue  # don't re-evaluate while pending

                # --- GATE 1: system operation mode ---
                sys_id = c_meta.get("system")
                sys_cfg = systems_cache.get(sys_id, {}) if sys_id else {}
                if sys_cfg.get("operation_mode", "auto") != "auto":
                    # check for pending manual command from UI
                    cmd_raw = r.get(f"manual_cmd:{c_id}")
                    if cmd_raw is not None:
                        r.delete(f"manual_cmd:{c_id}")
                        try:
                            user_val = int(cmd_raw)
                        except (ValueError, TypeError):
                            user_val = None
                        if user_val is not None:
                            target    = c_meta.get("target", "")
                            transport = c_meta.get("transport", "mqtt")
                            sent = False
                            if target and transport == "mqtt":
                                fb_id = c_meta.get("feedback_id")
                                payload = json.dumps({
                                    "value": user_val,
                                    "feedback_id": fb_id,
                                    "ts": now_ms_ctrl,
                                })
                                mqtt_client.publish(target, payload)
                                log.info(f"[CTRL MANUAL] {c_id} → mqtt:{target} val={user_val}")
                                sent = True
                            if sent:
                                c_meta["cmd_value"]           = user_val
                                c_meta["ctrl_status"]         = "WARN"
                                c_meta["feedback_ticks_left"] = c_meta["feedback_timeout_ticks"]
                                _ctrl_write()
                                ctrl_has = True
                                continue  # PENDING will handle feedback on next ticks
                    # check feedback quality in manual mode too
                    fb_id_m  = c_meta.get("feedback_id")
                    fb_meta_m = meta_cache.get(fb_id_m) if fb_id_m else None
                    if fb_meta_m:
                        fb_q_m = fb_meta_m.get("state", "NODATA")
                        if fb_q_m in ("ALARM", "WARN", "NODATA", "UNCERT", "INIT"):
                            if c_meta["ctrl_status"] != "ALARM":
                                c_meta["ctrl_status"] = "ALARM"
                                _ctrl_write()
                                ctrl_has = True
                            continue
                    if c_meta["ctrl_status"] not in ("WARN", "ALARM", "GOOD"):
                        if c_meta["ctrl_status"] != "NODATA":
                            c_meta["ctrl_status"] = "NODATA"
                            _ctrl_write()
                            ctrl_has = True
                    continue

                # --- GATE 2: feedback quality ---
                fb_id   = c_meta.get("feedback_id")
                fb_meta = meta_cache.get(fb_id) if fb_id else None
                if fb_meta:
                    fb_q = fb_meta.get("state", "NODATA")
                    if fb_q in ("ALARM", "WARN", "NODATA", "UNCERT", "INIT"):
                        if c_meta["ctrl_status"] != "ALARM":
                            c_meta["ctrl_status"] = "ALARM"
                            _ctrl_write()
                            ctrl_has = True
                        continue

                # --- ALARM RECOVERY: feedback confirmed last command ---
                # If in ALARM and feedback has since confirmed cmd_value, clear to GOOD.
                # This handles the case where formulas are in "hold" zone so GATE 3
                # is never reached, yet the device eventually responded.
                if c_meta["ctrl_status"] == "ALARM":
                    cv = c_meta.get("cmd_value")
                    if (cv is not None and fb_meta and
                            fb_meta.get("last_value") is not None and
                            int(fb_meta["last_value"]) == cv):
                        c_meta["ctrl_status"] = "GOOD"
                        _ctrl_write()
                        ctrl_has = True
                        continue

                # --- GATE RUN_LIMIT: auto-shutoff after max runtime ---
                run_limit = c_meta.get("run_limit_s")
                if run_limit and c_meta["ctrl_status"] == "GOOD" and fb_meta:
                    fb_lcts = fb_meta.get("last_change_ts", 0)
                    if fb_lcts and (now_ms_ctrl - fb_lcts) / 1000 >= run_limit:
                        target    = c_meta.get("target", "")
                        transport = c_meta.get("transport", "mqtt")
                        if target and transport == "mqtt":
                            payload = json.dumps({
                                "value":       0,
                                "feedback_id": c_meta.get("feedback_id"),
                                "ts":          now_ms_ctrl,
                            })
                            mqtt_client.publish(target, payload)
                            log.info(f"[CTRL RUN_LIMIT] {c_id} → OFF after {run_limit}s")
                            c_meta["cmd_value"]           = 0
                            c_meta["ctrl_status"]         = "WARN"
                            c_meta["feedback_ticks_left"] = c_meta["feedback_timeout_ticks"]
                            _ctrl_write()
                            ctrl_has = True
                        continue

                # --- SR-LATCH FORMULAS ---
                formula_on  = c_meta.get("formula_on",  "").strip()
                formula_off = c_meta.get("formula_off", "").strip()
                if not formula_on and not formula_off:
                    continue

                on_v  = _eval_ctrl_cond(formula_on,  c_id) if formula_on  else False
                off_v = _eval_ctrl_cond(formula_off, c_id) if formula_off else False

                if on_v is None or off_v is None:
                    continue  # unresolved ref or eval error → hold

                # SR-latch: both true → safety off, ON→1, OFF→0, neither→hold
                if on_v and off_v:
                    new_val = 0
                elif on_v:
                    new_val = 1
                elif off_v:
                    new_val = 0
                else:
                    continue  # neither condition met → hold

                # --- GATE 3: already at expected state ---
                if fb_meta and fb_meta.get("last_value") is not None:
                    if int(fb_meta["last_value"]) == new_val:
                        if c_meta["ctrl_status"] != "GOOD":
                            c_meta["ctrl_status"] = "GOOD"
                            c_meta["cmd_value"]   = new_val
                            _ctrl_write()
                            ctrl_has = True
                        continue

                # --- SEND ---
                target    = c_meta.get("target", "")
                transport = c_meta.get("transport", "mqtt")

                sent = False
                if target and transport == "mqtt":
                    payload = json.dumps({
                        "value":       new_val,
                        "feedback_id": fb_id,
                        "ts":          now_ms_ctrl,
                    })
                    mqtt_client.publish(target, payload)
                    log.info(f"[CTRL] {c_id} → mqtt:{target} val={new_val}")
                    sent = True

                elif target and transport == "shell":
                    cmd = target.replace("{value}", str(new_val))
                    threading.Thread(
                        target=_run_shell, args=(cmd, c_id, log), daemon=True
                    ).start()
                    log.info(f"[CTRL] {c_id} → shell: {cmd}")
                    sent = True

                if sent:
                    c_meta["cmd_value"]           = new_val
                    c_meta["ctrl_status"]         = "WARN"
                    c_meta["feedback_ticks_left"] = c_meta["feedback_timeout_ticks"]
                    _ctrl_write()
                    ctrl_has = True

            if ctrl_has:
                ctrl_pipe.execute()

            # --- SCHEDULER: multi_timer server-side ---
            sched_reload_ctr -= tick

            if sched_reload_ctr <= 0:
                sched_reload_ctr = SCHED_RELOAD_S
                _new_cache = {}
                for _sk in r.keys("scheduler:*"):
                    _sk_str = _sk.decode() if isinstance(_sk, bytes) else _sk
                    _el_id  = _sk_str.split(":", 1)[1]
                    _sd = r.hgetall(_sk)
                    if not _sd:
                        continue
                    _dec = {
                        (k.decode() if isinstance(k, bytes) else k):
                        (v.decode() if isinstance(v, bytes) else v)
                        for k, v in _sd.items()
                    }
                    try:
                        _schedule = json.loads(_dec.get("schedule", "[]"))
                    except Exception:
                        _schedule = []
                    _pid = int(_dec.get("point_id", 0))
                    if _pid > 0 and _schedule:
                        _new_cache[_el_id] = {"point_id": _pid, "schedule": _schedule}
                # drop states for removed elements
                for _gone in set(sched_state) - set(_new_cache):
                    del sched_state[_gone]
                sched_cache = _new_cache
                if sched_cache:
                    log.debug(f"[SCHED] loaded {len(sched_cache)} schedule(s)")

            if sched_cache:
                _now_t   = time.localtime()
                _now_hms = f"{_now_t.tm_hour:02d}:{_now_t.tm_min:02d}:{_now_t.tm_sec:02d}"
                _sc_pipe  = r.pipeline()
                _sc_has   = False

                for _el_id, _sc in sched_cache.items():
                    _is_on = any(
                        (
                            (s["on"] < s["off"] and _now_hms >= s["on"] and _now_hms < s["off"]) or
                            (s["on"] > s["off"] and (_now_hms >= s["on"] or _now_hms < s["off"]))
                        )
                        for s in _sc["schedule"]
                        if s.get("on") and s.get("off")
                    )

                    _prev = sched_state.get(_el_id)
                    if _prev is None:
                        sched_state[_el_id] = _is_on   # first tick — record without firing
                        continue
                    if _is_on == _prev:
                        continue

                    # state transition → fire command
                    sched_state[_el_id] = _is_on
                    _target_val  = 1 if _is_on else 0
                    _sc_ctrl_id  = _sc["point_id"]
                    _sc_meta     = meta_cache.get(_sc_ctrl_id)

                    if not _sc_meta or _sc_meta.get("type") != "control":
                        log.warning(f"[SCHED] {_el_id}: point {_sc_ctrl_id} not found or not type=control")
                        continue

                    # respect system operation mode — only fire in auto mode
                    _sc_sys   = _sc_meta.get("system")
                    _sc_syscfg = systems_cache.get(_sc_sys, {}) if _sc_sys else {}
                    if _sc_syscfg.get("operation_mode", "auto") != "auto":
                        log.info(f"[SCHED] skip {_el_id} → {_sc_ctrl_id}: system in manual mode")
                        continue

                    _sc_target = _sc_meta.get("target", "")
                    _sc_trans  = _sc_meta.get("transport", "mqtt")
                    _sc_sent   = False
                    _sc_now_ms = int(time.time() * 1000)

                    if _sc_target and _sc_trans == "mqtt":
                        mqtt_client.publish(_sc_target, json.dumps({
                            "value":       _target_val,
                            "feedback_id": _sc_meta.get("feedback_id"),
                            "ts":          _sc_now_ms,
                        }))
                        log.info(f"[SCHED] {_el_id} → ctrl:{_sc_ctrl_id} ({_sc_target}) = {_target_val}")
                        _sc_sent = True

                    elif _sc_target and _sc_trans == "shell":
                        threading.Thread(
                            target=_run_shell,
                            args=(_sc_target.replace("{value}", str(_target_val)), _sc_ctrl_id, log),
                            daemon=True
                        ).start()
                        log.info(f"[SCHED] {_el_id} → shell:{_sc_target} = {_target_val}")
                        _sc_sent = True

                    if _sc_sent:
                        _sc_meta["cmd_value"]           = _target_val
                        _sc_meta["ctrl_status"]         = "WARN"
                        _sc_meta["feedback_ticks_left"] = _sc_meta["feedback_timeout_ticks"]
                        _sc_meta["last_change_ts"]      = _sc_now_ms
                        _sc_lim = _sc_meta["limits"]
                        _sc_pipe.hset(f"point:{_sc_ctrl_id}", mapping={
                            "value":          str(_target_val),
                            "ts":             str(_sc_now_ms // 1000),
                            "quality":        "WARN",
                            "type":           "control",
                            "object":         _sc_meta["object"],
                            "system":         _sc_meta["system"],
                            "pointname":      _sc_meta["pointname"],
                            "unit":           "",
                            "last_change_ts": str(_sc_now_ms),
                            "min":       _sc_lim["min"],  "max":       _sc_lim["max"],
                            "warn_min":  _sc_lim["warn_min"], "warn_max":  _sc_lim["warn_max"],
                            "alarm_min": _sc_lim["alarm_min"], "alarm_max": _sc_lim["alarm_max"],
                        })
                        _sc_pipe.publish("bus:data", str(_sc_ctrl_id))
                        _sc_has = True

                if _sc_has:
                    _sc_pipe.execute()

            # --- DESYNC GUARD ---
            if config["system"]["desync_guard"]:
                now_ms = int(time.time() * 1000)
                timeout = config["system"]["desync_timeout_ms"]

                for point_id, meta in meta_cache.items():

                    if meta["last_update_ts"] == 0:
                        continue

                    if meta["state"] == "NODATA":
                        continue

                    # binary/discrete types send only on state change —
                    # stable silence is normal; skip desync for them
                    if meta.get("type") in ("calculated", "discrete",
                                            "operation_mode", "control"):
                        continue

                    if now_ms - meta["last_update_ts"] > timeout:
                        old_state = meta["state"]
                        meta["state"] = "NODATA"
                        meta["last_change_ts"] = now_ms

                        key = f"point:{point_id}"

                        event = {
                            "event": "DESYNC",
                            "object": meta["object"],
                            "drop": meta["drop"],
                            "system": meta["system"],
                            "point_id": point_id,
                            "value": None,
                            "old_state": old_state,
                            "new_state": "NODATA",
                            "ts": now_ms
                        }

                        pipe = r.pipeline()
                        pipe.hset(key, "quality", "NODATA")
                        pipe.publish("bus:event", json.dumps(event))
                        pipe.publish("bus:data", point_id)
                        pipe.execute()

                        # log.warning(f"[DESYNC] point {point_id} → NODATA")

            # --- ELAPSED TIMERS ---
            # For discrete/control/operation_mode points: write elapsed_s + timer_state
            # once per second (only when value changes). Used by timer UI elements.
            elapsed_pipe = r.pipeline()
            elapsed_has  = False
            now_elapsed  = int(time.time() * 1000)
            for _et_pid, _et_meta in meta_cache.items():
                _et_type = _et_meta.get("type")
                if _et_type not in ("discrete", "control", "operation_mode"):
                    continue
                lcts = _et_meta.get("last_change_ts", 0)
                if not lcts:
                    continue
                elapsed_s = int((now_elapsed - lcts) / 1000)
                if elapsed_s == _et_meta.get("_last_elapsed_s", -1):
                    continue  # no change this second
                _et_meta["_last_elapsed_s"] = elapsed_s
                run_lim   = _et_meta.get("run_limit_s")
                warn_aft  = _et_meta.get("warn_after_s")
                if run_lim and elapsed_s >= run_lim:
                    tstate = "ALARM"
                elif warn_aft and elapsed_s >= warn_aft:
                    tstate = "WARN"
                else:
                    tstate = "GOOD"
                elapsed_pipe.hset(f"point:{_et_pid}", mapping={
                    "elapsed_s":   elapsed_s,
                    "timer_state": tstate,
                })
                elapsed_pipe.publish("bus:data", _et_pid)
                elapsed_has = True
            if elapsed_has:
                elapsed_pipe.execute()

            # --- STATS ---
            r.set("system:buffer_size", len(updates))
            r.set("system:passed_deadband", passed)
            r.set("heartbeat:infrabox-core", int(time.time()), ex=25)

            # --- HEARTBEAT POINTS (every _HB_INTERVAL ticks) ---
            _hb_tick += 1
            if _hb_tick >= _HB_INTERVAL:
                _hb_tick = 0
                now_ms = int(time.time() * 1000)
                hb_get_pipe = r.pipeline()
                for svc in _HB_POINTS:
                    hb_get_pipe.get(f"heartbeat:{svc}")
                hb_statuses = hb_get_pipe.execute()

                hb_pipe = r.pipeline()
                for (svc, pid), raw in zip(_HB_POINTS.items(), hb_statuses):
                    alive   = raw is not None
                    value   = "1" if alive else "0"
                    quality = "GOOD" if alive else "ALARM"
                    meta    = meta_cache[pid]
                    lim     = meta["limits"]
                    hb_pipe.hset(f"point:{pid}", mapping={
                        "value":    value,
                        "ts":       now_ms,
                        "quality":  quality,
                        "object":   meta["object"],
                        "system":   meta["system"],
                        "pointname": meta["pointname"],
                        "unit":     meta.get("unit", ""),
                        "min":      lim["min"],    "max":      lim["max"],
                        "warn_min": lim["warn_min"],"warn_max": lim["warn_max"],
                        "alarm_min":lim["alarm_min"],"alarm_max":lim["alarm_max"],
                    })
                    hb_pipe.publish("bus:data", pid)
                hb_pipe.execute()

        except Exception as e:
            log.error(f"Unexpected error in main loop: {e}")
            time.sleep(1)

        # --- MQTT WATCHDOG ---
        if config["system"].get("mqtt_watchdog"):
            timeout = config["system"]["mqtt_timeout_ms"]
            if not mqtt_client.check(timeout):
                log.error("MQTT heartbeat lost — reconnecting...")
                mqtt_client.reconnect()

        # --- SLEEP ---
        time.sleep(tick)


if __name__ == "__main__":
    main()
