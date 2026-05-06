import json
import time
import random
import os
import logging
import threading
from logging.handlers import RotatingFileHandler
import paho.mqtt.client as mqtt
import redis as redis_lib

CONFIG_PATH = os.environ.get("POINTS_PATH", "/app/points.json")
MQTT_HOST   = os.environ.get("MQTT_HOST",   "infrabox-mosquitto-sim")
MQTT_PORT   = int(os.environ.get("MQTT_PORT", 1883))
REDIS_HOST  = os.environ.get("REDIS_HOST",  "infrabox-redis")
REDIS_PORT  = int(os.environ.get("REDIS_PORT", 6379))


def _heartbeat_thread():
    r = None
    while True:
        try:
            if r is None:
                r = redis_lib.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
            r.set("heartbeat:infrabox-simulator", int(time.time()), ex=25)
        except Exception:
            r = None
        time.sleep(5)


# Analog spikes (warn/alarm zones)
SPIKE_INTERVAL = 15
SPIKE_DURATION = 5

# Discrete severity=none — random toggling (motor on/off, pump cycles)
DISCRETE_TOGGLE_MIN = 30
DISCRETE_TOGGLE_MAX = 120

# Discrete severity=warn|alarm — event-driven (door, alarm sensor)
DISCRETE_EVENT_INTERVAL_MIN = 60
DISCRETE_EVENT_INTERVAL_MAX = 180
DISCRETE_EVENT_DURATION_MIN = 5
DISCRETE_EVENT_DURATION_MAX = 30

# Discrete — random invalid value injection (UNCERT) — emulates wire faults,
# protocol errors etc. Glitch lasts one tick, then state returns to normal.
DISCRETE_GLITCH_INTERVAL_MIN = 180   # 3 min
DISCRETE_GLITCH_INTERVAL_MAX = 600   # 10 min
DISCRETE_GLITCH_VALUES = (-1, 2, 3, 9)


def setup_logger():
    logger = logging.getLogger("simulator")
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s"
    )

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    os.makedirs("/app/log", exist_ok=True)
    file_handler = RotatingFileHandler(
        "/app/log/simulator.log",
        maxBytes=1048576,
        backupCount=3
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


def load_points():
    with open(CONFIG_PATH) as f:
        points = json.load(f)
    return [p for p in points if p["id"] >= 100 and p.get("type") != "diagnostic"]


def build_topic(p):
    return f"{p['object']}/{p['system']}/{p['pointname']}/{p['id']}"


# ── Analog helpers ───────────────────────────────────────────────────────────

def normal_center(p):
    return (p["warn_min"] + p["warn_max"]) / 2


def normal_drift(p):
    return (p["warn_max"] - p["warn_min"]) * 0.02


def spike_value(p, level):
    if level == "warn":
        if random.random() > 0.5:
            return random.uniform(p["warn_max"], p["alarm_max"])
        else:
            return random.uniform(p["alarm_min"], p["warn_min"])
    else:
        if random.random() > 0.5:
            return random.uniform(p["alarm_max"], p["max"])
        else:
            return random.uniform(p["min"], p["alarm_min"])


# ── Discrete helpers ─────────────────────────────────────────────────────────

def _init_discrete_state(p, now):
    """Build per-point discrete state. Two flavours:
        kind=toggle : severity=none, both states are operational, random toggles
        kind=event  : severity=warn|alarm, mostly stays at normal_value with
                      occasional 'event' bursts to the abnormal state.

    Both flavours also schedule a 'glitch' — a one-tick injection of an
    out-of-range value to test UNCERT handling.
    """
    severity = p.get("severity", "none")
    normal   = int(p.get("normal_value", 0))
    base = {
        "next_glitch": now + random.uniform(DISCRETE_GLITCH_INTERVAL_MIN, DISCRETE_GLITCH_INTERVAL_MAX),
        "glitching":   False,
        "abnormal_until": 0,
    }
    if severity == "none":
        return {**base,
            "kind": "toggle",
            "value": random.choice([0, 1]),
            "next_change": now + random.uniform(DISCRETE_TOGGLE_MIN, DISCRETE_TOGGLE_MAX),
        }
    return {**base,
        "kind": "event",
        "value": normal,
        "next_change": now + random.uniform(DISCRETE_EVENT_INTERVAL_MIN, DISCRETE_EVENT_INTERVAL_MAX),
    }


def _step_discrete(p, s, now):
    """Advance discrete state for one tick; mutates s in place. Returns value."""
    # Glitch: out-of-range value for one tick, then back to the regular flow
    if s["glitching"]:
        # we already published the glitch last tick — restore normal value now
        s["glitching"] = False
        s["next_glitch"] = now + random.uniform(DISCRETE_GLITCH_INTERVAL_MIN, DISCRETE_GLITCH_INTERVAL_MAX)
        # fall through to regular logic to compute the recovery value

    elif now >= s["next_glitch"]:
        s["glitching"] = True
        s["value"] = random.choice(DISCRETE_GLITCH_VALUES)
        return s["value"]

    normal = int(p.get("normal_value", 0))
    if s["kind"] == "toggle":
        if now >= s["next_change"]:
            # value may currently be a glitch — derive next from binary 0/1
            cur = 0 if s["value"] not in (0, 1) else s["value"]
            s["value"] = 1 - cur
            s["next_change"] = now + random.uniform(DISCRETE_TOGGLE_MIN, DISCRETE_TOGGLE_MAX)
        elif s["value"] not in (0, 1):
            # recovering from glitch — settle at last known binary
            s["value"] = 0
    else:  # event
        if now < s["abnormal_until"]:
            s["value"] = 1 - normal
        elif now >= s["next_change"]:
            s["value"] = 1 - normal
            s["abnormal_until"] = now + random.uniform(DISCRETE_EVENT_DURATION_MIN, DISCRETE_EVENT_DURATION_MAX)
            s["next_change"] = now + random.uniform(DISCRETE_EVENT_INTERVAL_MIN, DISCRETE_EVENT_INTERVAL_MAX)
        else:
            s["value"] = normal
    return s["value"]


# ── Publish ──────────────────────────────────────────────────────────────────

def _publish(client, p, value, retain=False):
    payload = {"id": p["id"], "value": value, "ts": int(time.time())}
    client.publish(build_topic(p), json.dumps(payload), retain=retain)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    log = setup_logger()
    log.info(f"Simulator started → {MQTT_HOST}:{MQTT_PORT}")
    threading.Thread(target=_heartbeat_thread, daemon=True).start()

    points = load_points()
    n_disc = sum(1 for p in points if p.get("type") == "discrete")
    n_calc = sum(1 for p in points if p.get("type") == "calculated")
    n_an   = len(points) - n_disc - n_calc
    log.info(f"Loaded {len(points)} points ({n_an} analog, {n_disc} discrete, {n_calc} calculated — skipped)")

    points = [p for p in points if p.get("type") != "calculated"]

    client = mqtt.Client()
    client.connect(MQTT_HOST, MQTT_PORT, 60)
    client.loop_start()

    now = time.time()
    state = {}
    for p in points:
        pid = p["id"]
        if p.get("type") == "discrete":
            state[pid] = _init_discrete_state(p, now)
        else:
            state[pid] = {
                "value": normal_center(p),
                "spike_until": 0,
                "next_spike": now + random.uniform(5, SPIKE_INTERVAL),
            }

    # Initial broadcast for discrete points (retain so late subscribers see state)
    for p in points:
        if p.get("type") == "discrete":
            _publish(client, p, state[p["id"]]["value"], retain=True)

    while True:
        now = time.time()

        for p in points:
            pid = p["id"]
            s = state[pid]

            # ── DISCRETE: edge-triggered ────────────────────────────────────
            if p.get("type") == "discrete":
                old = s["value"]
                new = _step_discrete(p, s, now)
                if new != old:
                    _publish(client, p, new, retain=True)
                    log.debug(f"[DISCRETE] {p['pointname']} ({pid}) → {new}")
                continue

            # ── ANALOG / CALC: continuous ───────────────────────────────────
            if now < s["spike_until"]:
                value = s["value"]

            elif now >= s["next_spike"]:
                level = "alarm" if random.random() > 0.6 else "warn"
                value = spike_value(p, level)
                s["value"] = value
                s["spike_until"] = now + SPIKE_DURATION
                s["next_spike"] = now + SPIKE_INTERVAL + random.uniform(-3, 3)
                log.debug(
                    f"[SPIKE {level.upper()}] {p['pointname']} ({pid}) → {value:.2f}")

            else:
                drift = random.uniform(-normal_drift(p), normal_drift(p))
                value = s["value"] + drift
                value = max(p["warn_min"] * 1.05,
                            min(p["warn_max"] * 0.95, value))
                s["value"] = value

            _publish(client, p, round(value, 2))

        time.sleep(1)


if __name__ == "__main__":
    main()
