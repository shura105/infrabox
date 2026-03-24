import json

def load_rules(path):
    with open(path) as f:
        return json.load(f)

def evaluate_rules(r, rules, updates, meta_cache):
    events = []
    for obj, drop_id, sys, pid, value in updates:
        # Перевірка правил
        for rule in rules:
            if rule["pid"] == pid:
                cond = rule["condition"]
                ref = rule["threshold"]
                if cond == ">" and value > ref:
                    events.append({
                        "object": obj,
                        "drop": drop_id,
                        "system": sys,
                        "point": pid,
                        "value": value,
                        "rule": rule["name"]
                    })
                elif cond == "<" and value < ref:
                    events.append({
                        "object": obj,
                        "drop": drop_id,
                        "system": sys,
                        "point": pid,
                        "value": value,
                        "rule": rule["name"]
                    })
    return events