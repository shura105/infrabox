# Infrabox

Система домашньої автоматизації на базі Banana Pi M2 Berry (ARMv7, 1 GB RAM, 30 GB SSD).

## Архітектура

Монорепо з трьох незалежних пакетів:

```
core/   — збір та обробка даних (infrabox-core)
arch/   — архівування та перегляд архіву (infrabox-arch)
ui/     — головний UI + адмін-панель (infrabox-ui)
adm/    — сервіс керування (infrabox-adm)
```

### Структура репо

```
infrabox/
├── core/
│   ├── service/         core-сервіс (обробка точок, MQTT, Redis)
│   │   └── config/      points.json, objects.json, drops.json,
│   │                    systems.json, serv_subsystems.json, sys_params.json
│   ├── tools/           simulator, selfdiagnostic, agent (Telegram-бот)
│   ├── mosquitto/
│   └── redis/
├── arch/
│   ├── archivator/      archivator-сервіс
│   ├── backend/         arch_backend API
│   └── ui/              arch_ui (nginx + Alpine.js + Chart.js)
├── ui/
│   ├── adm/             adm-сервіс (FastAPI, керування контейнерами)
│   ├── backend/         головний backend
│   └── frontend/        головний UI + адмін-панель
└── adm/                 docker-compose для infrabox-adm
```

## Порти

| Сервіс               | Порт  |
|----------------------|-------|
| Redis                | 6379  |
| MQTT real            | 1883  |
| MQTT sim             | 1884  |
| Archivator API       | 8100  |
| Arch Backend API     | 8101  |
| Arch UI (nginx)      | 8102  |
| ADM API (внутрішній) | 8099  |

## Конфігураційні файли (`core/service/config/`)

### `objects.json` — Об'єкти автоматизації

| id      | Назва            |
|---------|------------------|
| home    | Квартира         |
| garage  | Гараж            |
| boiler  | Котельня         |
| solar   | Сонячна станція  |

### `drops.json` — Вузли (сервери)

| id           | Назва                  | Об'єкт  |
|--------------|------------------------|---------|
| homeServ-1   | Сервер на балконі      | home    |
| garageServ-1 | Сервер гаражу          | garage  |
| boilerServ-1 | Сервер котельні        | boiler  |
| solarServ-1  | Сервер сонячної станції| solar   |

### `systems.json` — Технологічні системи

| id           | Назва                        | Вузол        |
|--------------|------------------------------|--------------|
| reserv_power | Резервне електропостачання   | homeServ-1   |
| res_battary1 | Батареї резервного живлення  | homeServ-1   |
| water        | Водопостачання               | garageServ-1 |
| gas          | Газ                          | boilerServ-1 |
| power        | Електроживлення              | solarServ-1  |

### `serv_subsystems.json` — Серверні підсистеми

Контейнери на `homeServ-1` з хартбіт-елементами:

| id                      | Назва           | Елемент                  |
|-------------------------|-----------------|--------------------------|
| infrabox-core           | Core            | core-heartbeat           |
| infrabox-auth           | Auth            | auth-heartbeat           |
| infrabox-backend        | Backend         | backend-heartbeat        |
| infrabox-adm            | ADM             | adm-heartbeat            |
| infrabox-simulator      | Simulator       | simulator-heartbeat      |
| selfdiagnostic          | Selfdiagnostic  | selfDiag                 |
| infrabox-arch           | Archivator      | arch-heartbeat           |
| infrabox-arch-backend   | Arch Backend    | arch-backend-heartbeat   |
| infrabox-arch-ui        | Arch UI         | arch-ui-heartbeat        |
| infrabox-redis          | Redis           | redis-heartbeat          |
| infrabox-mosquitto-real | MQTT Real       | mqtt-real-heartbeat      |
| infrabox-mosquitto-sim  | MQTT Sim        | mqtt-sim-heartbeat       |
| infrabox-web            | Web             | web-heartbeat            |
| portainer               | Portainer       | portainer-heartbeat      |

### `points.json` — Параметри

Поля: `id, type, object, drop, system, hb_service, pointname, unit,`  
`min, max, warn_min, warn_max, alarm_min, alarm_max,`  
`deadband, interval, onArchive, archive_interval, archive_on_change`

Типи: `analog`, `discrete`, `calculated`, `external`, `system`

## Потік даних

```
MQTT → Core (буфер) → Quality Engine → Redis (point:{id}) → bus:data / bus:event
```

## Redis-структура

```
point:{id}       hash: value, ts, quality, object, system, pointname, unit
heartbeat:{name} TTL=25s (наявність = контейнер живий)
bus:data         pub/sub: point_id при оновленні
bus:event        pub/sub: JSON-події
bus:clock        pub/sub: системний тік
```

## Стани якості точки

`INIT → GOOD → WARN → ALARM → UNCERT → NODATA`

## Архів (`arch/`)

Формат: JSON Lines у `/app/data/{YYYY-MM-DD_HH-MM-SS}/`:  
`values.json`, `events.json`, `selfdiag.json`, `meta.json`, `config_snap.json`  
Стискаються в `.json.gz` при закритті тому. Ротація: 100k записів або 24 год.

### Arch Backend API (порт 8101)

```
GET  /points
GET  /points/{id}/range?from_ts&to_ts
GET  /points/{id}/state_range
GET  /status, /sessions, /volumes/*
POST /control/{action}
```

## ADM-сервіс (`adm/`)

FastAPI-сервіс для керування контейнерами та метаданими.  
Монтує конфіги з `core/service/config/` та `infrabox.json`.

### ADM API (внутрішній порт 8099, зовні через nginx `/adm/api/`)

```
GET  /config, /containers, /host/status
GET  /objects, /drops, /systems, /config/subsystems, /sys-params
POST/PUT/DELETE  /objects/{id}
POST/PUT/DELETE  /drops/{id}
POST/PUT/DELETE  /systems/{id}
POST/PUT/DELETE  /points/{id}
POST /subsystems/{id}/start|stop|restart|down|build/stream
POST /host/reboot|shutdown
```

## Деплой

```bash
# Core
docker compose -f core/docker-compose.yml up -d [--build]

# Arch
docker compose -f arch/docker-compose.yml up -d [--build]

# UI
docker compose -f ui/docker-compose.yml up -d [--build]

# ADM (rebuild)
cd adm && docker compose build --no-cache && docker compose up -d
```

## Конвенції комітів

`fix(scope):`, `feat(scope):`, `refactor(scope):`, `chore:`  
Scopes: `core`, `quality`, `arch`, `arch_ui`, `arch_backend`, `adm`, `ui`
