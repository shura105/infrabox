Запис у Redis

Виконується завжди (навіть без події):

r.hset(key, mapping={...})

Структура:
point:101

{
  "value": 72.94,
  "ts": 1774250568,
  "quality": "GOOD",
  "object": "home",
  "system": "heating",
  "pointname": "inputTemperature",
  "unit": "°C"
}

📌 Тут:
	•quality = актуальний стан з meta
	•це головне джерело для монітора

📡 5. Публікація в шину (pub/sub)

Дані:
r.publish("bus:data", point_id)
📌 Це:
	•сигнал “дані оновились”
	•використовується монітором / listener’ами

🚨 6. Генерація подій

Якщо є подія:
if result:

6.1 Лог
print("[EVENT]", result)

6.2 Запис alarm_state
r.hset(key, "alarm_state", result["new_state"])

6.3 Публікація події
r.publish("bus:event", json.dumps(result))

📌 Це вже:
	•реактивна система
	•тригер для інших сервісів

🔁 7. Повний цикл обробки

MQTT →
  parse →
    validate →
      match topic →
        quality →
          redis write →
            pub/sub →
              events
              
              
🔚 Висновок

Обробка даних — це:

👉 перетворення “сирого значення”
→ у “стан системи”
→ з фіксацією
→ і генерацією подій










