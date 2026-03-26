import json
import redis
import os
import time
import curses
import threading

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
POINTS_PATH = os.path.abspath(os.path.join(
    BASE_DIR, "../../core/config/points.json"))


def load_points():
    with open(POINTS_PATH) as f:
        return json.load(f)


# --- CLEAN DATA ---
def clean(s):
    if s is None:
        return ""
    return str(s).replace("\n", "").replace("\r", "").replace("\x00", "").strip()


# --- COLOR MAP ---
def get_color(state):
    if state == "GOOD":
        return 1
    elif state == "WARN":
        return 2
    elif state == "ALARM":
        return 3
    elif state == "NODATA":
        return 5    # ← сірий
    else:
        return 4


# --- REDIS LISTENER ---
def redis_listener(r, cache, lock):
    pubsub = r.pubsub()
    pubsub.subscribe("bus:data")

    for msg in pubsub.listen():
        if msg["type"] != "message":
            continue

        try:
            pid = int(msg["data"])
        except:
            continue

        data = r.hgetall(f"point:{pid}")
        if not data:
            continue

        with lock:
            cache[pid] = data


# --- MAIN UI ---
def main(stdscr):
    curses.curs_set(0)
    stdscr.nodelay(True)

    curses.start_color()
    curses.init_pair(1, curses.COLOR_GREEN, curses.COLOR_BLACK)   # GOOD
    curses.init_pair(2, curses.COLOR_YELLOW, curses.COLOR_BLACK)  # WARN
    curses.init_pair(3, curses.COLOR_RED, curses.COLOR_BLACK)     # ALARM
    curses.init_pair(4, curses.COLOR_WHITE, curses.COLOR_BLACK)   # NONE
    curses.init_pair(5, curses.COLOR_CYAN, curses.COLOR_BLACK)    # NODATA

    points = load_points()

    # --- REDIS З РЕТРАЯМИ ---
    r = None
    while r is None:
        key = stdscr.getch()      # ← додати
        if key == 27:             # ← додати
            return                # ← додати

        try:
            r_test = redis.Redis(
                host="localhost", port=6379, decode_responses=True)
            r_test.ping()
            r = r_test
        except redis.ConnectionError:
            stdscr.erase()
            stdscr.addstr(0, 0, "Waiting for Redis... (ESC to exit)")
            stdscr.refresh()
            time.sleep(2)

    cache = {}
    lock = threading.Lock()

    # --- старт listener ---
    t = threading.Thread(target=redis_listener,
                         args=(r, cache, lock), daemon=True)
    t.start()

    # --- preload (щоб одразу щось було) ---
    for p in points:
        pid = p["id"]
        data = r.hgetall(f"point:{pid}")
        if data:
            cache[pid] = data

    # --- UI LOOP ---
    while True:
        stdscr.erase()

        h, w = stdscr.getmaxyx()

        header = "=== INFRABOX MONITOR (ESC to exit) ==="
        stdscr.addstr(0, 0, header[:w-1])

        # заголовки колонок
        columns = f"{'POINT':40} | {'VALUE':>7} | {'UNIT':5} | STATE"
        stdscr.addstr(1, 0, columns[:w-1], curses.A_BOLD)

        with lock:
            for i, p in enumerate(points):
                row = i + 3
                if row >= h:
                    break

                pid = p["id"]
                data = cache.get(pid, {})

                value = clean(data.get("value", "None"))
                unit = clean(data.get("unit", ""))
                state = clean(data.get("quality", "none"))

                color = curses.color_pair(get_color(state))

                # name = f"{p['object']}/{p['system']}/{p['pointname']}"
                name = f"{p['object']}/{p['system']}/{p['pointname']} ({p['id']})"

                text = f"{name:40} | {value:>7} | {unit:<5} | {state}"

                # --- ЗАХИСТ ВІД АРТЕФАКТІВ ---
                text = text[:w-1]

                stdscr.move(row, 0)
                stdscr.clrtoeol()
                stdscr.addstr(row, 0, text, color)

        stdscr.refresh()

        key = stdscr.getch()
        if key == 27:  # ESC
            break


if __name__ == "__main__":
    curses.wrapper(main)
