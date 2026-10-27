# Docker: PereHomeLab 0.2.0

Образы: `bedmah/perehomelab:0.2.0` и `bedmah/perehomelab:latest`. Команды выполняются в отдельной папке развёртывания с вашим `secret.env`. При обновлении сначала остановите прежний контейнер и сделайте защищённую резервную копию данных.

```bash
docker pull bedmah/perehomelab:0.2.0
mkdir -p data logs sound downloads
chmod 600 secret.env
docker run --rm --network none --user 0 --entrypoint chown \
  -v "$(pwd)/data:/app/data" -v "$(pwd)/logs:/app/logs" -v "$(pwd)/sound:/app/sound" \
  bedmah/perehomelab:0.2.0 -R --no-dereference 10001:10001 /app/data /app/logs /app/sound
docker run -d --name perehomelab --restart unless-stopped \
  -p 8000:8000 --env-file secret.env \
  -e HISTORY_DB_PATH=/app/data/press_history.db \
  -e APP_DOWNLOAD_DIR=/download-app \
  --add-host host.docker.internal:host-gateway \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/logs:/app/logs" \
  -v "$(pwd)/sound:/app/sound" \
  -v "$(pwd)/downloads:/download-app:ro" \
  bedmah/perehomelab:0.2.0
```

Приложение запускается от UID/GID `10001:10001`, без root. Команда `chown` выше используется только для подготовки каталогов и само приложение не запускает. При обновлении со старого root-образа сначала остановите старый контейнер и сделайте резервную копию, затем выполните её для прежних `data`, `logs`, `sound`. Файлы, история, настройки и ключи не удаляются. `app`/`downloads` остаётся доступным только на чтение и должен разрешать чтение EXE пользователю 10001. Новых обязательных параметров ENV нет; формат БД и Fernet не меняется. Не обходите эту подготовку постоянным запуском сервиса через `--user 0`.

Внутри образа версия `tzdata` закреплена через `TZDATA_VERSION`. При плановом обновлении пакета измените ARG и повторите сборку/проверки; для локальной сборки можно передать `--build-arg TZDATA_VERSION=ПРОВЕРЕННАЯ_ВЕРСИЯ`.

Откройте `http://SERVER_IP:8000`. Для внешнего порта 80 замените `-p 8000:8000` на `-p 80:8000`.

В `secret.env` заполните параметры из `secret.env.example`, в том числе `HA_URL`, `HA_TOKEN`, пароль администратора и `TELEGRAM_ENCRYPTION_KEY`. Ключ Fernet создаётся один раз:

```bash
docker run --rm --entrypoint python bedmah/perehomelab:0.2.0 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Сохраните результат в `TELEGRAM_ENCRYPTION_KEY`. При обновлении или переносе используйте **тот же ключ**: иначе сохранённые токены ботов не расшифруются. Не меняйте его при каждом запуске.

## Что сохраняется снаружи

| Путь на сервере | Содержимое |
|---|---|
| `data/` | SQLite: история, локации, настройки, пользователи админки, сотрудники, комментарии, зашифрованные токены ботов, очередь Telegram и шаги регистрации |
| `logs/` | Логи приложения и HTTP-запросов |
| `sound/` | Загруженные звуки; доступ на запись обязателен для загрузки и удаления через сайт |
| `downloads/` | EXE для скачивания; имя задаётся `APP_DOWNLOAD_FILENAME` |
| `secret.env` | Подключение HA, ключ шифрования и начальные настройки; не включается в образ |

`APP_DOWNLOAD_DIR` указан явно, чтобы значение из старого env (`static/downloads`) не перенаправило скачивание внутрь контейнера. В отдельной папке развёртывания можно назвать `downloads` как `app`, изменив левую часть `-v`; в исходниках папка `app` уже содержит Python-код.

Громкость и другие браузерные настройки хранятся в браузере, не в Docker. Изменения файлов `secret.env`, переданных через `--env-file`, применяются после **пересоздания** контейнера, а не простого restart.

`HA_URL=http://localhost:8123` внутри контейнера указывает на сам контейнер. Используйте адрес HA в сети, `http://host.docker.internal:8123` для доступного порта хоста или имя контейнера HA в общей Docker-сети. Не запускайте два экземпляра с одними Telegram-ботами. SQLite рассчитана на один экземпляр приложения; запускать Uvicorn с несколькими workers не нужно.

## Обновление и резервная копия

Остановите контейнер, сохраните `data`, `sound`, `downloads` и `secret.env` в защищённое место, затем удалите только контейнер и повторите запуск с теми же каталогами. Базу удалять не нужно. При копировании работающей SQLite используйте SQLite backup API, а не копирование одного файла без WAL.

Проверка: `docker logs --tail 100 perehomelab`, `docker inspect --format='{{.State.Health.Status}}' perehomelab`. Healthcheck подтверждает работу HTTP-процесса; состояние подключения HA проверяется отдельно на странице мониторинга.

Логи и база содержат служебные сведения и данные сотрудников. Не размещайте каталоги данных в публичном веб-каталоге и не добавляйте их в Git/образ. Тестовый прокси WSL не входит в образ.

Загрузка одного звука ограничена 20 МиБ, чтобы случайно большой файл не занял всю память процесса.

## Проверки перед публикацией

```bash
docker run --rm --network none --read-only --tmpfs /tmp \
  -v "$(pwd)/tests:/app/tests:ro" --entrypoint python \
  bedmah/perehomelab:0.2.0 -B -m unittest discover -s tests -q
python3 tests/docker_persistence.py --image bedmah/perehomelab:0.2.0
```

Вторая проверка создаёт временные каталоги и два последовательных контейнера без сети: проверяет сохранность БД, загруженного звука, логов и скачивания EXE, а также отказ в загрузке слишком большого файла. Рабочие каталоги и секреты не используются.
