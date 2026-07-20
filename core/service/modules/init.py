import json
import os

POINTS_PATH   = "/app/config/points.json"
SOCKETS_PATH  = "/app/config/sockets.json"
SYSTEMS_PATH  = "/app/config/systems.json"
OBJECT_DEFAULT = os.environ.get("OBJECT", "home")


def _load_sock_service_map():
    """Build {socket_id: service_name} from sockets.json software entries."""
    try:
        with open(SOCKETS_PATH) as f:
            data = json.load(f)
    except Exception:
        return {}
    m = {}
    for node in data:
        for s in (node.get("software") or []):
            if s.get("id") and s.get("name"):
                m[s["id"]] = s["name"]
    return m


def load_points():
    with open(POINTS_PATH) as f:
        points = json.load(f)

    sock_svc = _load_sock_service_map()
    meta_cache = {}

    for p in points:
        pid = p["id"]
        ptype = p.get("type", "analog")
        socket = p.get("socket", "")
        # for diagnostic heartbeat points: derive hb_service from sockets.json
        hb_service = p.get("hb_service") or (
            sock_svc.get(socket, "") if p.get("param") == "heartbeat" else ""
        )

        meta = {
            "type": ptype,
            "object": p.get("object") or OBJECT_DEFAULT,
            "drop": p.get("drop", ""),
            "system": p.get("system", socket),
            "pointname": p["pointname"],
            "id": p["id"],
            "unit": p.get("unit", ""),
            "deadband": p.get("deadband", 0),
            "hb_service": hb_service,
            "last_value": None,
            "state": "INIT",
            "last_change_ts": 0,
            "last_update_ts": 0
        }

        if ptype == "discrete":
            # discrete signals: 0/1 only — synthetic limits so the rest of the
            # pipeline (redis writes, deadband checks) keeps working uniformly
            meta["limits"] = {
                "min": 0, "max": 1,
                "warn_min": 0, "warn_max": 1,
                "alarm_min": 0, "alarm_max": 1,
            }
            meta["normal_value"] = int(p.get("normal_value", 0))
            meta["severity"]     = p.get("severity", "none")
            meta["label_0"]      = p.get("label_0", "")
            meta["label_1"]      = p.get("label_1", "")
        elif ptype == "calculated":
            meta["formula"] = p.get("formula", "")
            meta["limits"] = {
                "min":       p.get("min", 0),
                "max":       p.get("max", 100),
                "warn_min":  p.get("warn_min", 0),
                "warn_max":  p.get("warn_max", 100),
                "alarm_min": p.get("alarm_min", 0),
                "alarm_max": p.get("alarm_max", 100),
            }
        elif ptype == "state_calc":
            # aggregate STATE from other signals' states; value is the severity
            # rank (0=GOOD … 4=ALARM), limits fixed so the pipeline is uniform
            meta["formula"] = p.get("formula", "")
            meta["limits"] = {
                "min": 0, "max": 4,
                "warn_min": 0, "warn_max": 4,
                "alarm_min": 0, "alarm_max": 4,
            }
        elif ptype == "control":
            meta["formula_on"]             = p.get("formula_on", "")
            meta["formula_off"]            = p.get("formula_off", "")
            meta["transport"]              = p.get("transport", "mqtt")
            meta["target"]                 = p.get("target", "")
            meta["feedback_id"]            = p.get("feedback_id")
            meta["feedback_timeout_ticks"] = p.get("feedback_timeout_ticks", 3)
            meta["limits"] = {
                "min": 0.0, "max": 1.0,
                "warn_min": 0.0, "warn_max": 1.0,
                "alarm_min": 0.0, "alarm_max": 1.0,
            }
            # runtime state (not from config)
            meta["ctrl_status"]         = "INIT"
            meta["cmd_value"]           = None
            meta["feedback_ticks_left"] = 0
        else:
            meta["limits"] = {
                "min":       p.get("min", 0),
                "max":       p.get("max", 100),
                "warn_min":  p.get("warn_min", 0),
                "warn_max":  p.get("warn_max", 100),
                "alarm_min": p.get("alarm_min", 0),
                "alarm_max": p.get("alarm_max", 100),
            }

        meta_cache[pid] = meta

    return meta_cache


def load_systems():
    """Load systems.json → {sys_id: system_dict}"""
    try:
        with open(SYSTEMS_PATH) as f:
            systems = json.load(f)
        return {s["id"]: s for s in systems}
    except Exception:
        return {}
