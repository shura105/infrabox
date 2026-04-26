import json

POINTS_PATH = "/app/config/points.json"


def load_points():
    with open(POINTS_PATH) as f:
        points = json.load(f)

    meta_cache = {}

    for p in points:
        pid = p["id"]

        meta_cache[pid] = {
            "object": p.get("object", ""),
            "drop": p.get("drop", ""),
            "system": p.get("system", p.get("socket", "")),
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
            "hb_service": p.get("hb_service", ""),
            "last_value": None,
            "state": "INIT",
            "last_change_ts": 0,
            "last_update_ts": 0
        }

    return meta_cache
