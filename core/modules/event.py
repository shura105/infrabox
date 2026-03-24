import json

def publish_event(r, event):
    print("[EVENT]", event)
    r.publish("infrabox/events", json.dumps(event))