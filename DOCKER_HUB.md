# PereHomeLab

Веб-мониторинг кнопок переговорок через Home Assistant с уведомлениями в браузере и Telegram.

- [GitHub и исходники](https://github.com/Bedmah/PereHomeLab)
- [Изменения версии 0.1.3](https://github.com/Bedmah/PereHomeLab/releases/tag/v0.1.3)
- Образы: `bedmah/perehomelab:0.1.3` и `bedmah/perehomelab:latest`.

## Возможности

- Локации по пространствам HA, отдельные страницы `/spb`, `/msk` и общий монитор.
- Всплывающие вызовы, отдельные звуки локаций, история нажатий и заряд батарей.
- Админка `/admin`: локации, пользователи, сотрудники, библиотека звуков, диагностика и логи.
- Отдельный Telegram-бот, группа и тема для каждой локации.
- «Я иду», отмена и комментарии в одном сообщении; ответственный синхронизируется с мониторингом.
- Профиль сотрудника через `/sync`, личное меню через `/start`, административные ограничения и минимальный антиспам.
- Тёмный интерфейс для ТВ и поддержка скачивания Windows kiosk-приложения из отдельного каталога.

## Что нового в 0.1.3

- Новая админка с тремя ролями: главный, общий и администратор назначенных локаций.
- Форма входа, серверные сессии, выход, ограничение попыток входа и журнал изменений с фильтрами.
- Управление сотрудниками и доступом к ботам по локациям; общие профили и запрет их изменения в боте сохранены.
- Настраиваемые автоскрытие уведомлений и жёлтая подсветка истории, отдельные таймеры вызовов и сохранение настроек в браузере.
- Иконки действий и общая ссылка поддержки на главной, в мониторах, админке и на странице входа.
- Улучшена адаптация к узким экранам, переработаны настройки звука и детали батарей.
- Старые ENV и БД совместимы; миграции выполняются автоматически, удалять данные не нужно.

## Быстрый запуск

Используйте отдельную папку развёртывания:

```bash
mkdir -p /docker/perehomelab
cd /docker/perehomelab
mkdir -p data logs sound app
```

Создайте `secret.env`:

```env
HA_URL=http://host.docker.internal:8123
HA_TOKEN=your_home_assistant_long_lived_token
HA_AUTO_DISCOVER=true
HA_ENTITY_DOMAIN=binary_sensor
HA_DEVICE_CLASS=safety
HA_TRIGGER_TO=on
HA_SYNC_HISTORY_HOURS=24

ADMIN_USERNAME=your_admin_login
ADMIN_PASSWORD=replace_with_a_strong_password

BUTTON_SOUND=sound/1.mp3
SOUND_PLAY_SECONDS=25
ACK_BUTTON_TEXT=ФА
HISTORY_DB_PATH=/app/data/press_history.db
HISTORY_DEDUP_SECONDS=3

APP_DOWNLOAD_DIR=/download-app
APP_DOWNLOAD_FILENAME=PereHomeLabKiosk.exe

TELEGRAM_ENABLED=false
TELEGRAM_ENCRYPTION_KEY=
TELEGRAM_EVENT_TTL_SECONDS=300
```

```bash
chmod 600 secret.env
docker pull bedmah/perehomelab:0.1.3
docker run -d --name perehomelab --restart unless-stopped \
  -p 80:8000 --env-file secret.env \
  -e HISTORY_DB_PATH=/app/data/press_history.db \
  -e APP_DOWNLOAD_DIR=/download-app \
  --add-host host.docker.internal:host-gateway \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/logs:/app/logs" \
  -v "$(pwd)/sound:/app/sound" \
  -v "$(pwd)/app:/download-app:ro" \
  bedmah/perehomelab:0.1.3
```

Мониторинг: `http://SERVER_IP/`, админка: `http://SERVER_IP/admin`.

Положите звук в `sound/1.mp3` или загрузите его через админку и выберите для локации. EXE при необходимости положите в `app/PereHomeLabKiosk.exe`. Звуки и EXE не поставляются внутри образа. Для загрузки звуков каталог `sound` должен быть доступен на запись, без `:ro`.

## Home Assistant

- HA на другой машине: `HA_URL=http://192.168.1.50:8123`.
- HA на хосте с опубликованным портом: `HA_URL=http://host.docker.internal:8123`.
- HA в общей пользовательской Docker-сети: `HA_URL=http://homeassistant:8123`; добавьте `--network ИМЯ_СЕТИ`.
- `localhost` внутри контейнера - сам контейнер, не соседний Home Assistant.
- Автообнаружение по умолчанию ищет `binary_sensor` с `device_class: safety`. Локации, звуки, текст подтверждения и глубина истории настраиваются в админке.

## Telegram

Создайте ключ один раз:

```bash
docker run --rm --entrypoint python bedmah/perehomelab:0.1.3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

При первой настройке сохраните результат в `TELEGRAM_ENCRYPTION_KEY`, установите `TELEGRAM_ENABLED=true` и пересоздайте контейнер. Затем в `/admin` → Telegram подключите бота нужной локации, откройте привязку, отправьте `/setup@username_бота` в нужной теме группы и подтвердите её в админке. При обновлении существующего проекта не создавайте новый ключ: сохраните прежний ENV и каталог `data`.

Боту нужен доступ к сообщениям группы; для очистки ответов пользователей - право удаления сообщений. Используется long polling: нужен исходящий HTTPS-доступ к Telegram, публичный webhook не требуется. Один бот должен работать только с одним экземпляром приложения.

Принятие останавливает звук соответствующего вызова. Отмена не запускает звук повторно. Кнопка подтверждения в вебе только отключает оповещение и не назначает сотрудника. Корпоративные данные вводятся самостоятельно, без проверки AD.

## Данные и обновление

- `data/`: SQLite с историей, локациями, пользователями, сотрудниками, комментариями, зашифрованными токенами и очередью Telegram.
- `logs/`: логи приложения и запросов.
- `sound/`: загруженные звуки.
- `app/`: скачиваемый EXE.
- `secret.env`: секреты и ключ шифрования, хранится только на сервере.

Перед обновлением остановите контейнер и сделайте защищённую резервную копию этих данных. Сохраните прежний `TELEGRAM_ENCRYPTION_KEY`: новый ключ не расшифрует уже сохранённые токены ботов. Базу удалять не нужно, миграции выполняются автоматически.

```bash
docker stop perehomelab
docker rm perehomelab
docker pull bedmah/perehomelab:0.1.3
```

Повторите `docker run` с теми же каталогами. При обновлении с 0.1.2 старый ENV подходит; новых обязательных параметров нет. Если указан `APP_VERSION`, измените его на `0.1.3` или удалите строку. Изменения ENV применяются после пересоздания контейнера, не простого restart. Для отката сохраняйте резервную копию данных до миграции.

Вход в админку выполняется через форму. Главный администратор задан в ENV; существующие дополнительные администраторы сохраняют общий доступ. Ограниченные права по локациям назначаются отдельно. Для доступа к админке используйте HTTPS. Пароли и токены ботов не включаются в образ; настройки поддержки, ролей и журнал хранятся в `data`.

Для другого внешнего порта замените `-p 80:8000`, например на `-p 8080:8000`. Громкость сохраняется отдельно в браузере. Обычный браузер может требовать первое взаимодействие для разрешения звука.

## Проверка

```bash
docker logs --tail 100 perehomelab
docker inspect --format='{{.State.Health.Status}}' perehomelab
curl http://localhost/healthz
curl http://localhost/api/status
```

Healthcheck проверяет HTTP-процесс, а не всю связь с HA. После обновления проверьте физическое нажатие и принятие в Telegram. Не публикуйте admin без HTTPS и ограничения доступа; база и логи содержат сведения о сотрудниках.
