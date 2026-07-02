# PereHomeLab

Веб-мониторинг кнопок переговорок через Home Assistant.

- Docker Hub: https://hub.docker.com/r/bedmah/perehomelab
- GitHub: https://github.com/Bedmah/PereHomeLab

PereHomeLab подключается к Home Assistant, отслеживает кнопки переговорок, показывает нажатия в веб-интерфейсе, проигрывает звуковые уведомления, сохраняет историю, отображает заряд батарей и позволяет управлять регионами через админку.

## Возможности

- Отслеживание кнопок через Home Assistant WebSocket API.
- Регионы по пространствам Home Assistant и коротким ссылкам `/spb`, `/msk`.
- Общая вкладка `Все` и отдельные страницы регионов.
- Всплывающие уведомления с названием кнопки, регионом и временем нажатия.
- Отдельный звук, текст подтверждения и настройки истории для каждого региона.
- Загрузка, поиск, пагинация и удаление звуков через админку.
- Регулировка громкости в браузере с сохранением значения.
- История нажатий в SQLite и ручная синхронизация истории из Home Assistant.
- Отображение заряда батарей и поиск по батареям.
- Админка с пользователями, сменой пароля, блокировкой и логом входов.
- Веб-логи ошибок backend / Home Assistant.
- Скачивание Windows kiosk-приложения из веб-интерфейса.
- Тёмный ТВ-интерфейс и Docker-ready запуск.

## Быстрый запуск в Docker

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
HA_ENTITY_IDS=
HA_ENTITY_DOMAIN=binary_sensor
HA_DEVICE_CLASS=safety
HA_ENTITY_ID_SUFFIX=
HA_TRIGGER_FROM=
HA_TRIGGER_TO=on
HA_RECONNECT_SECONDS=5
HA_REFRESH_ENTITIES_ON_RECONNECT=false
HA_SYNC_HISTORY_HOURS=24

BUTTON_SOUND=sound/1.mp3
SOUND_PLAY_SECONDS=25
ACK_BUTTON_TEXT=ФА
HISTORY_DB_PATH=data/press_history.db
HISTORY_DEDUP_SECONDS=3

ADMIN_USERNAME=admin
ADMIN_PASSWORD=change_me

APP_DOWNLOAD_DIR=/download-app
APP_DOWNLOAD_FILENAME=PereHomeLabKiosk.exe
```

Запустите контейнер:

```bash
docker run -d \
  --name perehomelab \
  --restart unless-stopped \
  -p 80:8000 \
  --env-file secret.env \
  --add-host host.docker.internal:host-gateway \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/logs:/app/logs" \
  -v "$(pwd)/app:/download-app" \
  -v "$(pwd)/sound:/app/sound" \
  bedmah/perehomelab:0.0.9
```

Откройте веб-интерфейс:

```text
http://SERVER_IP/
```

Админка:

```text
http://SERVER_IP/admin
```

## Home Assistant

`HA_URL` - адрес Home Assistant.

Примеры:

```env
HA_URL=http://host.docker.internal:8123
HA_URL=http://192.168.1.50:8123
HA_URL=http://homeassistant:8123
```

`HA_TOKEN` - долгоживущий токен Home Assistant.

После изменения `secret.env` контейнер нужно пересоздать, потому что `docker restart` не перечитывает `--env-file`.

## Регионы

Регионы настраиваются в `/admin`.

Для каждого региона можно указать:

- название региона;
- slug для короткой ссылки;
- пространство Home Assistant;
- звук уведомления;
- текст кнопки подтверждения;
- длительность или количество повторов звука;
- сколько часов истории подтягивать.

## Звуки и приложение

Звуки хранятся в volume:

```text
sound/
```

Windows kiosk-приложение для скачивания хранится в volume:

```text
app/PereHomeLabKiosk.exe
```

Ссылка скачивания:

```text
http://SERVER_IP/download-app
```

## Данные

История и настройки хранятся в SQLite:

```env
HISTORY_DB_PATH=data/press_history.db
```

Логи приложения:

```text
logs/app.log
logs/access.log
```

При пересоздании контейнера сохраняйте volumes:

```text
data/
logs/
sound/
app/
```

## Проверка

```bash
curl http://localhost/api/status
curl http://localhost/api/diagnostics
curl http://localhost/api/batteries
curl http://localhost/api/history
```

## Важно

- Папку `sound` подключайте без `:ro`, если хотите загружать звуки через админку.
- Папку `app` подключайте как `/download-app`, если хотите отдавать kiosk-приложение из веб-интерфейса.
