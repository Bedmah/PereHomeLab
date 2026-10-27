# Docker: версия 0.1.3

Этот вариант собирает текущие исходники локально, без публикации образа.

```bash
docker build -t perehomelab:local .
mkdir -p data logs sound downloads
chmod 600 secret.env
docker run -d --name perehomelab --restart unless-stopped \
  -p 8000:8000 --env-file secret.env \
  -e HISTORY_DB_PATH=/app/data/press_history.db \
  -e APP_DOWNLOAD_DIR=/download-app \
  --add-host host.docker.internal:host-gateway \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/logs:/app/logs" \
  -v "$(pwd)/sound:/app/sound" \
  -v "$(pwd)/downloads:/download-app:ro" \
  perehomelab:local
```

Откройте `http://SERVER_IP:8000`. Для внешнего порта 80 замените `-p 8000:8000` на `-p 80:8000`.

В `secret.env` заполните параметры из `secret.env.example`, в том числе `HA_URL`, `HA_TOKEN`, пароль администратора и `TELEGRAM_ENCRYPTION_KEY`. Ключ Fernet создаётся один раз:

```bash
docker run --rm --entrypoint python perehomelab:local -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
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
  perehomelab:local -B -m unittest discover -s tests -q
python3 tests/docker_persistence.py --image perehomelab:local
```

Вторая проверка создаёт временные каталоги и два последовательных контейнера без сети: проверяет сохранность БД, загруженного звука, логов и скачивания EXE, а также отказ в загрузке слишком большого файла. Рабочие каталоги и секреты не используются.
