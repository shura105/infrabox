🔷 4.1 INIT (ініціалізація ядра)

Фрагмент коду:
config = load_config()
meta_cache = load_points()

print(f"INIT: loaded {len(meta_cache)} points")

r = get_redis()

📌 Загальна ідея

INIT — це етап, де система:
	1.	Завантажує конфігурацію
	2.	Завантажує всі точки (points)
	3.	Формує оперативну модель стану
	4.	Підключається до Redis

👉 Після цього система готова приймати дані (MQTT)

🔹 1. load_config()
config = load_config()

📥 Вхід:
	•	файл: /app/config/system.json

📤 Вихід:
dict

📌 Що містить:
	•режими роботи (sim / real)
	•MQTT налаштування
	•параметри quality (наприклад recovery_time)

📌 Роль:
👉 Глобальні правила системи

🔹 2. load_points()
meta_cache = load_points()
📥 Вхід:
	•/app/config/points.json
📤 Вихід:
dict[int, dict]

Структура:
meta_cache = {
    101: {
        "id": 101,
        "object": "home",
        "drop": "homeServ-1",
        "system": "heating",
        "pointname": "inputTemperature",
        "unit": "°C",

        "limits": {...},

        "state": "INIT",
        "last_change_ts": 0
    },
    ...
}
📌 Що тут важливо
👉 1. Денормалізація
JSON → перетворюється у швидкий доступ по ID
meta_cache[101]
⚡ замість пошуку по списку

👉 2. Додається runtime-стан
"state": "INIT",
"last_change_ts": 0

⚠️ Це НЕ з файлу — це стан системи в пам’яті

👉 3. limits групуються
"limits": {
    "min": ...
}
👉 щоб не тягати весь JSON у quality

📌 Роль meta_cache

👉 Це ядро системи станів

Він використовується для:
	•перевірки MQTT повідомлень
	•визначення topic
	•обчислення quality
	•збереження стану

🔹 3. Лог INIT
print(f"INIT: loaded {len(meta_cache)} points")

🔹 4. get_redis()
r = get_redis()

📥 Вхід:
	•host: infrabox-redis
	•port: 6379

📤 Вихід:
redis.Redis

📌 Що створюється:
👉 TCP-з’єднання до Redis

📌 Для чого використовується:

Далі в коді:
r.hset(...)
r.publish(...)


⚙️ Вся логіка INIT разом

INIT робить 3 ключові речі:

⸻

🧠 1. Формує модель системи
points.json → meta_cache
👉 це “цифровий двійник” всіх точок

🔌 2. Підключає інфраструктуру
Redis → для збереження стану

⚙️ 3. Завантажує правила
system.json → config

🔥 Ключова ідея INIT
👉 INIT = розділення на 3 рівні

Рівень.     Джерело.    Призначення
Конфіг.     system.json правила
Метадані.   points.json структура
Runtime.    meta_cache. стан





