import json
import os

POINTS_PATH  = "/app/config/points.json"
SOCKETS_PATH = "/app/config/sockets.json"
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
        socket = p.get("socket", "")
        # for diagnostic heartbeat points: derive hb_service from sockets.json
        hb_service = p.get("hb_service") or (
            sock_svc.get(socket, "") if p.get("param") == "heartbeat" else ""
        )

        meta_cache[pid] = {
            "object": p.get("object") or OBJECT_DEFAULT,
            "drop": p.get("drop", ""),
            "system": p.get("system", socket),
            "pointname": p["pointname"],
            "id": p["id"],
            "unit": p.get("unit", ""),

            "limits": {
                "min": p["min"],
                "max": p["max"],
                "warn_min": p["warn_min"],
                "warn_max": p["warn_max"],
                "alarm_min": p["alarm_min"],
                "alarm_max": p["alarm_max"],
            },

            "deadband": p.get("deadband", 0),
            "hb_service": hb_service,
            "last_value": None,
            "state": "INIT",
            "last_change_ts": 0,
            "last_update_ts": 0
        }

    return meta_cache
