import time


def tick_clock(r):

    ts = int(time.time())

    r.set("system:clock", ts)

    r.publish("bus:clock", ts)