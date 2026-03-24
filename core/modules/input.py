import json


def get_input_mode(config):
    return config["input"]["mode"]


def read_from_stream(r, stream):
    messages = r.xread({stream: "$"}, block=1000, count=10)

    result = []

    for _, msgs in messages:
        for msg_id, data in msgs:
            result.append({
                "point_id": int(data["point_id"]),
                "value": float(data["value"]),
                "ts": int(data["ts"])
            })

    return result