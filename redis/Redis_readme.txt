🧠 1. Загальна структура Redis
У тебе зараз використовується 3 механізми Redis:

1️⃣ HASH (основні дані точок)
point:<id>

2️⃣ PUB/SUB (події в реальному часі)
bus:data
bus:event

3️⃣ (потенційно) STREAM — поки не використовується активно

📦 2. Основна структура: point:<id>
🔑 Ключ
point:101
point:102

📊 Поля (hash)
поле.             тип.     опис

value.            float.   поточне значення
ts.               int.     timestamp
quality.          str.     стан (GOOD/WARN/ALARM/UNCERT)
alarm_state.      str.     стан тривоги (опціонально)
object.           str.     об’єкт
system.           str.     система
pointname.        str.     ім’я точки
unit.             str.     одиниці виміру

🧾 Хто це створює?
👉 main.py → on_message()

key = f"point:{point_id}"

r.hset(key, mapping={
    "value": value,
    "ts": ts,
    "quality": meta["state"],
    "object": meta["object"],
    "system": meta["system"],
    "pointname": meta["pointname"],
    "unit": meta.get("unit", "")
})

⚡ Додаткове поле при подіях
r.hset(key, "alarm_state", result["new_state"])
👉 додається тільки якщо є подія


📡 3. PUB/SUB канали

🔵 3.1 Канал: bus:data
📤 Публікація:
r.publish("bus:data", point_id)

📥 Хто слухає:
pubsub.subscribe("bus:data")

📦 Дані:
"101"

🧠 Логіка:
	•повідомляє: “дані точки оновились”
	•далі отримувач сам читає Redis


🔴 3.2 Канал: bus:event
📤 Публікація:
r.publish("bus:event", json.dumps(result))

📦 Дані:
{
  "event": "ALARM",
  "object": "home",
  "system": "heating",
  "point_id": 101,
  "value": 105,
  "old_state": "GOOD",
  "new_state": "ALARM"
}

🧠 Призначення:
	•події (алярми, попередження)
	•реактивна логіка
	•логування

⚙️ 4. Джерело метаданих (НЕ Redis!)

📂 Файл:
core/config/points.json

📥 Використовується тут:
meta_cache = load_points()

👉 файл:
modules/init.py

🧠 Важливо:
Redis НЕ зберігає конфіг
👉 Redis = runtime
👉 JSON = конфігурація


🔁 5. Повний цикл даних
🧩 1. Simulator
MQTT → topic + payload

🧩 2. CORE (main.py)
Отримує:
on_message(topic, payload)

Обробляє:
process_quality(...)

Пише:
r.hset(point:<id>)

Публікує:
bus:data
bus:event

🧩 3. MONITOR
Читає:
r.get / r.hgetall

📊 6. Фактичний стан Redis (як у тебе зараз)

point:101
point:102

Всередині приблизно:
HGETALL point:101

value       → 60.06
ts          → 1774250000
quality     → GOOD
object      → home
system      → heating
pointname   → inputTemperature
unit        → °C
alarm_state → GOOD









































