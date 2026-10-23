# PereHomeLab

Веб-мониторинг кнопок переговорок через Home Assistant с уведомлениями в браузере и Telegram.

- [GitHub и исходники](https://github.com/Bedmah/PereHomeLab)
- [Изменения версии 0.1.2](https://github.com/Bedmah/PereHomeLab/releases/tag/v0.1.2)
- Образы: `bedmah/perehomelab:0.1.2` и `bedmah/perehomelab:latest`.

## Возможности

- Регионы по пространствам HA, отдельные страницы `/spb`, `/msk` и общий монитор.
- Всплывающие вызовы, региональные звуки, история нажатий и заряд батарей.
- Админка `/admin`: регионы, пользователи, сотрудники, библиотека звуков, диагностика и логи.
- Отдельный Telegram-бот, группа и тема для каждого региона.
- «Я иду», отмена и комментарии в одном сообщении; ответственный синхронизируется с мониторингом.
- Профиль сотрудника через `/sync`, личное меню через `/start`, административные ограничения и минимальный антиспам.
- Тёмный интерфейс для ТВ и поддержка скачивания Windows kiosk-приложения из отдельного каталога.

## Что нового в 0.1.2

- Обновлён интерфейс в стиле Liquid Glass и адаптация к разным формам экрана.
- Исправлены ложные нажатия при загрузке начального состояния истории HA.
- Карточки истории выравниваются по рядам; назначение не растягивает остальные ряды.
- Старые env и БД совместимы; ранее записанные ошибочные события автоматически не удаляются.

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

ADMIN_USERNAME=admin
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
docker pull bedmah/perehomelab:0.1.2
docker run -d --name perehomelab --restart unless-stopped \
  -p 80:8000 --env-file secret.env \
  -e HISTORY_DB_PATH=/app/data/press_history.db \
  -e APP_DOWNLOAD_DIR=/download-app \
  --add-host host.docker.internal:host-gateway \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/logs:/app/logs" \
  -v "$(pwd)/sound:/app/sound" \
  -v "$(pwd)/app:/download-app:ro" \
  bedmah/perehomelab:0.1.2
```

Мониторинг: `http://SERVER_IP/`, админка: `http://SERVER_IP/admin`.

Положите звук в `sound/1.mp3` или загрузите его через админку и выберите для региона. EXE при необходимости положите в `app/PereHomeLabKiosk.exe`. Звуки и EXE не поставляются внутри образа. Для загрузки звуков каталог `sound` должен быть доступен на запись, без `:ro`.

## Home Assistant

- HA на другой машине: `HA_URL=http://192.168.1.50:8123`.
- HA на хосте с опубликованным портом: `HA_URL=http://host.docker.internal:8123`.
- HA в общей пользовательской Docker-сети: `HA_URL=http://homeassistant:8123`; добавьте `--network ИМЯ_СЕТИ`.
- `localhost` внутри контейнера - сам контейнер, не соседний Home Assistant.
- Автообнаружение по умолчанию ищет `binary_sensor` с `device_class: safety`. Регионы, звуки, текст подтверждения и глубина истории настраиваются в админке.

## Telegram

Создайте ключ один раз:

```bash
docker run --rm --entrypoint python bedmah/perehomelab:0.1.2 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Сохраните результат в `TELEGRAM_ENCRYPTION_KEY`, установите `TELEGRAM_ENABLED=true` и пересоздайте контейнер. Затем в `/admin` → Telegram подключите бота нужного региона, откройте привязку, отправьте `/setup@username_бота` в нужной теме группы и подтвердите её в админке.

Боту нужен доступ к сообщениям группы; для очистки ответов пользователей - право удаления сообщений. Используется long polling: нужен исходящий HTTPS-доступ к Telegram, публичный webhook не требуется. Один бот должен работать только с одним экземпляром приложения.

Принятие останавливает звук соответствующего вызова. Отмена не запускает звук повторно. Кнопка подтверждения в вебе только отключает оповещение и не назначает сотрудника. Корпоративные данные вводятся самостоятельно, без проверки AD.

## Данные и обновление

- `data/`: SQLite с историей, регионами, пользователями, сотрудниками, комментариями, зашифрованными токенами и очередью Telegram.
- `logs/`: логи приложения и запросов.
- `sound/`: загруженные звуки.
- `app/`: скачиваемый EXE.
- `secret.env`: секреты и ключ шифрования, хранится только на сервере.

Перед обновлением остановите контейнер и сделайте защищённую резервную копию этих данных. Сохраните прежний `TELEGRAM_ENCRYPTION_KEY`: новый ключ не расшифрует уже сохранённые токены ботов. Базу удалять не нужно, миграции выполняются автоматически.

```bash
docker stop perehomelab
docker rm perehomelab
docker pull bedmah/perehomelab:0.1.2
```

Повторите `docker run` с теми же каталогами. Изменения env применяются после пересоздания контейнера, не простого restart. При обновлении с 0.1.0 старые настройки HA сохраняются; новые параметры нужны только для Telegram.

Для другого внешнего порта замените `-p 80:8000`, например на `-p 8080:8000`. Громкость сохраняется отдельно в браузере. Обычный браузер может требовать первое взаимодействие для разрешения звука.

## Проверка

```bash
docker logs --tail 100 perehomelab
docker inspect --format='{{.State.Health.Status}}' perehomelab
curl http://localhost/healthz
curl http://localhost/api/status
```

Healthcheck проверяет HTTP-процесс, а не всю связь с HA. После обновления проверьте физическое нажатие и принятие в Telegram. Не публикуйте admin без HTTPS и ограничения доступа; база и логи содержат сведения о сотрудниках.
