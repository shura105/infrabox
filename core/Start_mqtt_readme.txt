ЗАПУСК MQTT (детально)

📍 Де це відбувається
У main():
start_mqtt(config, on_message)
Це точка входу в MQTT-підсистему.

🔹 1. Призначення
Запуск MQTT забезпечує:
	•	підключення до брокера
	•	підписку на топіки
	•	прийом повідомлень
	•	передачу їх у ядро через callback

👉 Тобто це вхідний канал даних у систему

🔹 2. Вхідні параметри

config
{
  "bootstrap": {
    "data_source": "sim",
    "mqtt": {
      "host_sim": "infrabox-mosquitto-sim",
      "host_real": "infrabox-mosquitto-real",
      "port": 1883
    }
  }
}

on_message_cb

Функція-обробник (твоя логіка ядра):
def on_message(topic, payload):
    ...
    
🔹 3. Що робить start_mqtt()

Ось розклад по кроках:
3.1 Вибір брокера
data_source = config["bootstrap"]["data_source"]

if data_source == "sim":
    host = mqtt_cfg["host_sim"]
else:
    host = mqtt_cfg["host_real"]
    
👉 Це критично:
	•sim → підключення до симулятора
	•real → до реального обладнання

3.2 Створення клієнта
client = mqtt.Client()
👉 Це об’єкт з бібліотеки paho-mqtt, який:
	•відкриває TCP-з’єднання
	•обробляє MQTT протокол
	•керує підписками

3.3 Обробник підключення
def on_connect(client, userdata, flags, rc):
    print(f"[MQTT] Connected to {host}:{port} rc={rc}")
    client.subscribe("#")
Що тут важливо:
	•rc=0 → успішне підключення
	•client.subscribe("#") → підписка на ВСІ топіки

👉 Це означає:

CORE отримує ВСІ MQTT повідомлення і сам фільтрує їх

3.4 Обробник повідомлень
def on_message(client, userdata, msg):
    on_message_cb(msg.topic, msg.payload.decode())

Тут відбувається:
	•отримання MQTT пакета
	•витяг topic
	•декодування payload
	•передача в ядро

👉 Це міст між MQTT і твоєю бізнес-логікою

3.5 Прив’язка обробників
client.on_connect = on_connect
client.on_message = on_message

3.6 Підключення
client.connect(host, port, 60)

Що це робить:
	•відкриває TCP сокет
	•виконує MQTT handshake
	•ініціює сесію

3.7 Запуск циклу
client.loop_start()

👉 Найважливіший момент.

Це:
	•запускає окремий thread
	•який постійно:
	•слухає сокет
	•читає пакети
	•викликає callbacks

🔹 4. Архітектурна схема
SIMULATOR / DEVICE
        ↓
   MQTT Broker
        ↓
   paho client (thread)
        ↓
   on_message()
        ↓
   on_message_cb()  ← main.py
   
🔹 5. Потік виконання
    1. main() викликає start_mqtt
	2. створюється клієнт
	3. підключення до брокера
	4. підписка на #
	5. запускається фоновий thread
	6. приходить MQTT повідомлення
	7. викликається: on_message → on_message_cb
    8. далі працює CORE

🔹 6. Вихідні дані
Функція повертає:
return client
👉 Але фактично:
	•клієнт працює у фоні
	•основна логіка йде через callbacks


🔹 7. Важливі нюанси (дуже)
⚠️ 1. Асинхронність

MQTT працює в окремому потоці:

👉 main() НЕ блокується

⸻

⚠️ 2. Немає контролю порядку

Повідомлення можуть приходити:
	•швидко
	•паралельно

⸻

⚠️ 3. Підписка на “#”

Це:
	•просто і гнучко ✅
	•але не оптимально ❗

👉 Потім можна зробити:
client.subscribe("home/#")

⚠️ 4. CORE фільтрує сам
if topic != expected_topic:
    return

🔚 Висновок

Запуск MQTT = запуск асинхронного каналу даних

Він:
	•підключає систему до брокера
	•слухає всі повідомлення
	•передає їх у CORE
	•працює у фоні через thread
	
---------------------------------
	
🔹 Що таке return client у start_mqtt()
Функція:
def start_mqtt(config, on_message_cb):
    ...
    client = mqtt.Client()
    ...
    client.connect(host, port, 60)
    client.loop_start()

    return client

повертає об’єкт MQTT-клієнта (client), який є центральним елементом роботи з MQTT.

🔹 Що всередині цього client

Це екземпляр класу:
paho.mqtt.client.Client

Він містить:

1. 📡 Стан з’єднання
	•підключений / не підключений
	•адреса брокера (host, port)
	•keepalive
	•статус reconnect

2. 🔁 Callback-и (обробники подій)

Ти їх задаєш тут:
client.on_connect = on_connect
client.on_message = on_message

Тобто client містить:
	•on_connect() → викликається при підключенні
	•on_message() → викликається при отриманні повідомлення


3. 📬 Підписки (subscriptions)

Після connect:
client.subscribe("#")

Всередині клієнта зберігається:
	•список топіків
	•QoS
	•статус підписок

⸻

4. 📥 Буфери повідомлень

Клієнт:
	•читає сокет
	•розбирає MQTT пакети
	•викликає callback

Це все відбувається в окремому потоці:
client.loop_start()

🔹 ЯКІ дані реально “приходять” через client?

Не через return, а через callback:
def on_message(client, userdata, msg):

Ось що ти отримуєш:

📦 msg — головне

msg.topic     # str
msg.payload   # bytes
msg.qos       # int
msg.retain    # bool

👉 У твоєму випадку:
msg.topic = "home/heating/inputTemperature/101"

msg.payload = b'{"id": 101, "value": 61.87, "ts": 1774211956}'

Ти робиш:
msg.payload.decode()
→ отримуєш:
{"id": 101, "value": 61.87, "ts": 1774211956}

🔹 Потік даних (дуже важливо зрозуміти)
Mosquitto (broker)
        ↓
TCP socket
        ↓
paho mqtt client (client.loop_start thread)
        ↓
on_message(msg)
        ↓
on_message_cb(topic, payload)
        ↓
твій main.py

🔹 Чому ми повертаємо client

Навіть якщо ти зараз його не використовуєш — це важливо для майбутнього:

Можеш:
•🔁 перепідключити:
	client.reconnect()
•📤 публікувати:
	client.publish("topic", "data")
•❌ зупинити:
    client.loop_stop()
    client.disconnect()
•🔍 перевірити стан:
	client.is_connected()
	
🔹 Висновок (суть)

return client → це:

👉 керуючий об’єкт MQTT-з’єднання, який:
	•тримає сокет
	•слухає топіки
	•викликає callback-и
	•дозволяє керувати MQTT з коду

А самі “дані” приходять не через return, а через:
	on_message(...)
	
🔹 Якщо спростити до 1 фрази
👉 client — це “двигун MQTT”,
а on_message — це “місце, де ти бачиш дані”.