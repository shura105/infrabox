╔══════════════════════════════════════════════════════════════════════════════╗
║                     INFRABOX — INSTALLER                                     ║
║                     Інструкція з використання скриптів                       ║
╚══════════════════════════════════════════════════════════════════════════════╝

Всі скрипти знаходяться в папці infrabox_install/.
Запускаються з admin-машини (або безпосередньо на хості — де зазначено).
Вимоги на admin-машині: bash, ssh, python3.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  СКРИПТИ — КОРОТКИЙ ОГЛЯД
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  probe.sh       Збирає звіт про хост → host-report.json
                 Запуск НА ЦІЛЬОВОМУ ХОСТІ, нічого не змінює
                 Підтримує Linux (arm/x86) і macOS

  wizard.sh      Інтерактивний майстер налаштування
                 Читає host-report.json → генерує topology.yml
                 Запуск на admin-машині

  host-prep.sh   Підготовка хоста до розгортання
                 Запуск НА ЦІЛЬОВОМУ ХОСТІ (від sudo-користувача)
                 Встановлює Docker, створює мережу, SSL, logrotate

  deploy.sh      Розгортання підсистем
                 Читає topology.yml → git pull → docker compose up
                 Запуск на admin-машині (по SSH)

  status.sh      Дашборд стану системи
                 Контейнери, RAM, диск, load, git-стан, томи, порти
                 Запуск на admin-машині (по SSH)

  uninstall.sh   Видалення з хоста
                 Три режими: зупинка / інтерактивне / повне видалення
                 Запуск на admin-машині (по SSH)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ПЕРША УСТАНОВКА — ПОРЯДОК ДІЙ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Крок 1. Зібрати звіт про хост
  -- виконати НА ЦІЛЬОВОМУ ХОСТІ --
  bash probe.sh

  → Створюється host-report.json у поточній директорії.
  → Скопіювати файл на admin-машину (в папку infrabox_install/):
    scp user@host:/path/host-report.json infrabox_install/

Крок 2. Запустити майстер налаштування
  -- виконати на ADMIN-МАШИНІ --
  cd infrabox_install
  bash wizard.sh

  → Майстер прочитає host-report.json і поставить питання.
  → Більшість відповідей заповнені автоматично із звіту.
  → Обов'язково вказати: SSH-ключ, git-репозиторій, JWT_SECRET.
  → Результат: topology.yml у папці infrabox_install/.

Крок 3. Підготувати хост
  -- виконати НА ЦІЛЬОВОМУ ХОСТІ --
  bash host-prep.sh

  Або з параметрами:
  bash host-prep.sh --deploy-dir /home/user/infrabox
  bash host-prep.sh --hostname myserver.local
  bash host-prep.sh --skip-ssl       (якщо SSL не потрібен)

  → Встановить Docker (якщо відсутній).
  → Додасть поточного користувача до групи docker.
  → Створить директорію розгортання та Docker-мережу infrabox-net.
  → Згенерує SSL-сертифікати (mkcert якщо доступний, інакше self-signed).
  → Налаштує logrotate.
  → УВАГА: після виконання може знадобитись newgrp docker або re-login.

Крок 4. Клонувати репозиторій на хост (якщо ще не зроблено)
  -- виконати НА ЦІЛЬОВОМУ ХОСТІ --
  git clone https://github.com/shura105/infrabox.git ~/infrabox
  cd ~/infrabox && git checkout feat/infrabox-ui

Крок 5. Розгорнути систему
  -- виконати на ADMIN-МАШИНІ --
  cd infrabox_install
  bash deploy.sh

  Або тільки окремі підсистеми:
  bash deploy.sh core
  bash deploy.sh core ui
  bash deploy.sh --dry-run        (показати план без дій)

Крок 6. Перевірити стан
  bash status.sh

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ПОТОЧНА РОБОТА — ДОСТУПНІ ДІЇ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Перевірити стан системи:
  bash status.sh
  bash status.sh --no-ports       (без перевірки TCP-портів, швидше)
  bash status.sh --topology /path/other.yml

  Повертає код виходу: 0 = все OK, 1 = є проблеми.
  Зручно для моніторингу: bash status.sh || alert.sh

Оновити код і перезапустити:
  bash deploy.sh                  (git pull + compose up всіх підсистем)
  bash deploy.sh ui               (тільки ui)
  bash deploy.sh arch ui          (arch і ui, в порядку topology)
  bash deploy.sh --dry-run        (показати що буде зроблено, без змін)

Використати інший topology-файл:
  bash deploy.sh --topology staging.yml
  bash status.sh --topology staging.yml

Зупинити систему (з збереженням даних):
  bash uninstall.sh --keep-data --force
  bash uninstall.sh --keep-data --force core    (тільки core)

Видалити підсистему:
  bash uninstall.sh               (інтерактивно — питає що видаляти)
  bash uninstall.sh --force       (без підтверджень, видаляє образи і volumes)
  bash uninstall.sh --full        (повне видалення: контейнери + образи + volumes)
  bash uninstall.sh ui adm        (тільки ui і adm)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ФАЙЛ ЗВІТУ: host-report.json
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Генерується командою: bash probe.sh (на цільовому хості)
Альтернатива тільки JSON: bash probe.sh --json > host-report.json

Що містить:
  hw.*          Апаратура: arch, CPU, RAM, диск, модель плати
  os.*          ОС: ID, дистрибутив, версія, hostname
  net.*         Мережа: IP-адреси, hostname, mDNS
  docker.*      Docker: встановлений/ні, версія, compose
  ports.*       Зайняті порти (перевіряє 80, 443, 1883, 1884, 6379, 8099-8102)
  ssl.*         Наявність mkcert
  git.*         Git: встановлений/ні

Звіт не містить паролів чи ключів — безпечно передавати.
Можна зберігати поруч із topology.yml для документування хоста.

Повторний запуск: завжди перезаписує host-report.json.
Актуалізувати перед wizard.sh якщо хост змінився (нові порти, оновлена ОС).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ФАЙЛ КОНФІГУРАЦІЇ: topology.yml
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Центральний файл конфігурації. Генерується wizard.sh, читається всіма іншими
скриптами (deploy, status, uninstall).

Секції:
  system:         Назва, репозиторій, гілка, timezone
  nodes:          Хости: адреса, SSH-користувач, ключ, deploy_dir, arch
  subsystems:     Підсистеми: workdir, containers, ports_exposed
  deploy_order:   Порядок розгортання (core першим — він запускає Redis)
  undeploy_order: Порядок зупинки (core останнім)
  data:           Volumes і директорії (для backup і uninstall)

Де зберігати:
  - В папці infrabox_install/ поруч із скриптами (там і шукають за замовчуванням)
  - Або вказувати через --topology /path/topology.yml
  - УВАГА: містить шляхи SSH-ключів і JWT_SECRET — не комітити в публічний репо

Кілька середовищ (dev/staging/prod):
  bash deploy.sh --topology prod.yml
  bash status.sh --topology staging.yml

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ПІДТРИМУВАНІ ПЛАТФОРМИ (ЦІЛЬОВИЙ ХОСТ)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Перевірено:    Armbian (Debian 12) / ARM v7 (Banana Pi M2 Berry)
  Сумісно:       Debian 11/12, Ubuntu 22/24, DietPi, Armbian / amd64, arm64, armhf
  Мінімум RAM:   256 MB (512 MB для arch-підсистеми)
  Мінімум диск:  3 GB (рекомендовано 6 GB+)

  host-prep.sh — тільки Linux. probe.sh і wizard.sh — Linux і macOS.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ЗМІННІ СЕРЕДОВИЩА
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  TOPOLOGY_FILE=/path/topology.yml   Використовувати альтернативний topology
                                     Діє для deploy.sh, status.sh, uninstall.sh

  INFRABOX_DEPLOY_DIR=/path          Перевизначити deploy_dir для host-prep.sh

  Приклад:
  TOPOLOGY_FILE=~/prod.yml bash deploy.sh --dry-run

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ТИПОВІ СЦЕНАРІЇ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Деплой нової версії коду:
  bash deploy.sh

Перевірити стан після деплою:
  bash status.sh

Щось не працює — перевірити конкретну підсистему:
  bash status.sh          → знайти проблемний контейнер
  bash deploy.sh core     → перерозгорнути тільки core

Переїзд на новий хост:
  1. bash probe.sh         (на новому хості)
  2. bash wizard.sh        (на admin-машині, з новим host-report.json)
  3. bash host-prep.sh     (на новому хості)
  4. git clone ...         (на новому хості)
  5. bash deploy.sh

Тимчасово зупинити систему:
  bash uninstall.sh --keep-data --force
  ... обслуговування ...
  bash deploy.sh

Повне видалення (звільнити місце):
  bash uninstall.sh --full --force
