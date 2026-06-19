# PereHomeLab

Веб-мониторинг кнопок через Home Assistant.

## Ссылки

- Docker Hub: https://hub.docker.com/r/bedmah/perehomelab
- GitHub: https://github.com/Bedmah/PereHomeLab

## Что делает

- Подключается к Home Assistant по REST и WebSocket API.
- Автоматически находит `binary_sensor` с `device_class: safety`.
- Реагирует на переход состояния в `on`.
- Показывает всплывающее уведомление в веб-морде.
- Проигрывает звук из папки `sound`.
- Сохраняет историю нажатий в SQLite.

## Настройка

Секреты лежат только в `secret.env`.

Минимально нужно заполнить:

```env
HA_URL=http://homeassistant:8123
HA_TOKEN=your_long_lived_access_token
```

Варианты `HA_URL`:

- Home Assistant в той же Docker-сети: `http://homeassistant:8123`
- Home Assistant на другой машине: `http://192.168.1.50:8123`
- Home Assistant на Docker-хосте Linux: `http://host.docker.internal:8123` и запуск с `--add-host host.docker.internal:host-gateway`

`http://172.17.0.1:8123` подходит только для конкретного Docker-хоста. На другой ноде это уже другой адрес, поэтому чаще всего работать не будет.

## Запуск локально

```bash
cd /home/bedmah/PereHomeLab
./run.sh
```

Открыть страницу:

```text
http://localhost:8000
```

## Запуск в Docker

```bash
mkdir -p data logs sound

docker run -d \
  --name perehomelab \
  --restart unless-stopped \
  -p 80:8000 \
  --env-file secret.env \
  --add-host host.docker.internal:host-gateway \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/logs:/app/logs" \
  -v "$(pwd)/sound:/app/sound:ro" \
  bedmah/perehomelab:0.0.3
```

После изменения `secret.env` контейнер нужно пересоздать, потому что `docker restart` не перечитывает `--env-file`.

## Проверка Home Assistant

```bash
cd /home/bedmah/PereHomeLab
. .venv/bin/activate
python check_ha.py
```

Команда должна показать количество найденных кнопок.
Если команда зависает или падает по timeout, сервер Home Assistant недоступен по адресу `HA_URL`.

В контейнере можно проверить диагностику так:

```bash
curl http://localhost/api/diagnostics
```

## Логи

```text
logs/app.log
logs/access.log
```
