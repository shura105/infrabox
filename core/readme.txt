main.py — опис функцій

⸻

1. load_config()

📌 Призначення

Завантажує глобальну конфігурацію системи з файлу system.json.

📥 Вхідні параметри
	•❌ відсутні

📤 Вихід
	•dict — повний конфіг системи
(включає bootstrap, system, quality тощо)

📎 Приклад структури
{
  "bootstrap": {
    "data_source": "sim",
    "mqtt": {...}
  },
  "system": {
    "quality": {
      "recovery_time_ms": 5000
    }
  }
}

2. get_redis()

📌 Призначення

Створює клієнт для підключення до Redis.

📥 Вхідні параметри
	•❌ відсутні

📤 Вихід
	•redis.Redis — готовий клієнт

⚙️ Особливості
	•host: infrabox-redis (Docker DNS)
	•decode_responses=True → повертає str, а не bytes

⸻

3. build_topic(meta)

📌 Призначення

Формує MQTT topic на основі метаданих точки.

📥 Вхідні параметри
	•meta: dict

📎 Очікувана структура meta
{
  "object": "home",
  "system": "heating",
  "pointname": "inputTemperature",
  "id": 101
}
📤 Вихід
	•str — MQTT topic

📎 Приклад
home/heating/inputTemperature/101

4. main()

📌 Призначення
Головна функція ядра:
	•ініціалізація
	•запуск MQTT
	•обробка даних
	•запис у Redis
	•генерація подій

⸻

🔹 Етапи роботи

4.1 INIT
config = load_config()
meta_cache = load_points()
📤 Результат
	•config — системний конфіг
	•meta_cache: dict[int → meta]
	
4.2 Redis підключення
r = get_redis()

4.3 MQTT callback
def on_message(topic, payload_raw):

5. on_message(topic, payload_raw)

📌 Призначення

Обробляє кожне MQTT повідомлення.
📥 Вхідні параметри
Параметр.    Тип.   Опис
topic.       str.   MQTT topic
payload_raw  str.   JSON рядок

📎 Приклад payload
{
  "id": 101,
  "value": 61.5,
  "ts": 1774250568
}

🔹 Кроки обробки

5.1 Парсинг JSON
payload = json.loads(payload_raw)

❗ Помилка

→ якщо JSON невалідний → лог + return

5.2 Витяг полів

point_id = payload.get("id")
value = payload.get("value")
ts = payload.get("ts", now)

5.3 Перевірка точки

if point_id not in meta_cache:
    return
    
5.4 Отримання метаданих

meta = meta_cache[point_id]

5.5 Валідація topic

expected_topic = build_topic(meta)

if topic != expected_topic:
    return
    
📌 Це захист від сміття в MQTT


5.6 Обробка якості

result = process_quality(...)

📤 Вихід
	•dict або None
	
5.7 Запис у Redis
key = f"point:{point_id}"

📥 Дані
{
  "value": value,
  "ts": ts,
  "quality": meta["state"],
  "object": meta["object"],
  "system": meta["system"],
  "pointname": meta["pointname"],
  "unit": meta.get("unit", "")
}

📤 Результат у Redis
point:101
  ├── value
  ├── ts
  ├── quality
  ├── object
  ├── system
  ├── pointname
  └── unit
  
  5.8 Pub/Sub (дані)
  r.publish("bus:data", point_id)
  
  📌 Для:
	•monitor
	•інших сервісів

5.9 Обробка подій (events)
if result:

📥 result структура
{
  "event": "ALARM",
  "object": "home",
  "system": "heating",
  "point_id": 101,
  "value": 105,
  "old_state": "GOOD",
  "new_state": "ALARM"
}

5.10 Запис alarm_state

r.hset(key, "alarm_state", result["new_state"])

5.11 Pub/Sub (події)

r.publish("bus:event", json.dumps(result))

📌 Це вже:
	•алерти
	•автоматика
	•логування

6. start_mqtt(config, on_message)
(з іншого модуля, але важливо)

📌 Призначення

Запускає MQTT клієнт
📥 Вхід
	•config — конфіг системи
	•on_message — callback

📤 Вихід
	•mqtt.Client

⚙️ Поведінка
	•підключається до:
	•host_sim або host_real
	•підписується на:
	
#

🧠 Загальна схема потоку
SIMULATOR
   ↓ MQTT
MOSQUITTO
   ↓
CORE (on_message)
   ↓
process_quality()
   ↓
Redis (point:ID)
   ↓
pub/sub (bus:data)
   ↓
MONITOR

⚠️ Важливі залежності

meta_cache повинен містити:
{
  "id": ...,
  "pointname": ...,
  "unit": ...
}

👉 інакше:
	•build_topic() падає
	•Redis пише неповні дані

