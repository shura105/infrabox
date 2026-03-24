Обробка даних (Data Processing)

Обробка даних — це центральна логіка ядра, яка виконується при кожному отриманні MQTT-повідомлення.

Вона реалізована всередині callback-функції:
def on_message(topic, payload_raw):
📥 1. Вхідні дані

Обробка стартує з двох параметрів:

✔ topic (str)

MQTT-топік, наприклад:
home/heating/inputTemperature/101

✔ payload_raw (str)
JSON-рядок:
{"id": 101, "value": 72.94, "ts": 1774250568}

🔄 2. Етапи обробки

2.1 Парсинг payload
payload = json.loads(payload_raw)
👉 Результат:
{
    "id": 101,
    "value": 72.94,
    "ts": 1774250568
}

📌 Якщо JSON некоректний:
print("[ERROR] Bad JSON")
return

2.2 Витяг ключових полів
point_id = payload.get("id")
value = payload.get("value")
ts = payload.get("ts", int(time.time()))

📌 Вихід:
	•point_id → ідентифікатор точки
	•value → значення
	•ts → timestamp

⸻

2.3 Валідація точки

if point_id not in meta_cache:
    return
📌 Значення відкидається, якщо:
	•точка не описана в points.json

2.4 Отримання метаданих
meta = meta_cache[point_id]

📌 meta містить:
{
    "object": "home",
    "system": "heating",
    "pointname": "inputTemperature",
    "limits": {...},
    "state": "GOOD",
    "last_change_ts": ...
}

2.5 Перевірка топіка (анти-фільтр)
expected_topic = build_topic(meta)

if topic != expected_topic:
    return

📌 Це критично:
	•захист від “чужих” повідомлень
	•гарантія відповідності конфігу

🧠 3. Основна обробка — QUALITY ENGINE

result = process_quality(...)

Вхід:
process_quality(
    point_id=point_id,
    value=value,
    meta=meta,
    config=config
)

Що відбувається:

1. Класифікація значення
new_state = classify(value, limits)

Можливі стани:
	•GOOD
	•WARN
	•ALARM
	•UNCERT

2. Порівняння зі старим станом
old_state = meta["state"]

3. Перевірка переходів станів
Логіка:
Було.     Стало.         Дія
INIT → X. FIRST_VALID_X. GOOD → WARN
WARN.     WARN → GOOD.   CLEAR_WARN (з таймером)
GOOD → ALARM.   ALARM.   ALARM → GOOD
CLEAR_ALARM (з таймером)   * → UNCERT.   UNCERT

4. Урахування часу відновлення
if now - meta["last_change_ts"] >= recovery_time:
📌 Це захист від “дьоргання” значення

Вихід:
result = {
    "event": "ALARM",
    "object": ...,
    "system": ...,
    "point_id": ...,
    "value": ...,
    "old_state": ...,
    "new_state": ...
}

або: None

















