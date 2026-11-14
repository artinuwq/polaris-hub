# Polaris Storage — этап 1

Внутреннее хранилище файлов: метаданные в SQLite, содержимое в Telegram (Bot API) кусками по ≤ 19 MiB.
Модуль `polaris.storage` живёт внутри процесса Polaris: без отдельного сервера, процесса, Docker и без зависимости от Lumica / Polaris Hub.
S3 API (этап 2) описан в [s3-api.md](s3-api.md); Agent API, UI, ACL, биллинг — не реализованы.

## Архитектура

```
Database Layer      database.py + migrations/*.sql + repository.py   (SQLite, WAL, транзакции, весь SQL)
Storage Core        core.py (жизненный цикл) + recovery.py (reconcile)  — не знает про Telegram
Backend Drivers     backends/base.py (интерфейс) + backends/telegram.py
Запуск              config.py (StorageConfig из Settings) + service.py (init/статус/фоновая сверка)
```

Ядро ходит в физическое хранилище только через `BackendDriver` (`upload`, `download`, `delete`, `get_metadata`, `health_check`).
Ссылка на данные (`backend_reference`) для ядра — непрозрачная строка. Новый backend (Local FS и т.п.) добавляется классом-драйвером, ядро не меняется.

### Решения, отличающиеся от буквы ТЗ

* **Чанки принадлежат `blob`, а не объекту** (`storage_chunks.blob_id` вместо `object_id`): ТЗ разрешает отделить логический объект от физического содержимого, а без этого безопасная дедупликация невозможна.
* **SQLite — отдельный файл `storage.db`**, а не `polaris.db`: существующий раннер миграций (`_run_migrations`) перевыполняет все `.sql` при каждом старте и не версионирует схему. У Storage свой версионный раннер (`schema_migrations`, checksum, защита от базы более новой версии при откате кода). Существующий ORM (SQLAlchemy) используется только модулем Athena, остальной код — `sqlite3`; Storage тоже использует `sqlite3` (по соединению на поток).
* **База по умолчанию** — `database/data/storage.db`: каталог в `.gitignore`, переживает `/update` (`git reset --hard`).
* Классы-заглушки под будущие возможности (S3, ACL и т.п.) не создавались.

## Схема SQLite (`migrations/0001_initial.sql`)

| Таблица | Назначение | Ключевое |
|---|---|---|
| `storage_buckets` | логические пространства | `name` UNIQUE, `dedup_scope` — граница дедупликации (по умолчанию = имя bucket) |
| `storage_blobs` | физическое содержимое | статусы `uploading/ready/failed/deleting`; UNIQUE(`dedup_scope`, `checksum_sha256`) WHERE ready |
| `storage_objects` | логические файлы (StorageObject) | статусы `pending/uploading/ready/failed/deleting`; UNIQUE(`bucket_id`,`object_key`) WHERE ready; `blob_id` |
| `storage_chunks` | части blob | UNIQUE(`blob_id`,`chunk_index`); свой `size_bytes` и `checksum_sha256`; `backend_reference`; статусы `pending/stored/delete_failed/delete_rejected` |
| `storage_orphans` | возможные «осиротевшие» данные в backend | `uncertain_upload`, `interrupted_upload` (без ссылки), `db_write_lost` (со ссылкой) |
| `schema_migrations` | версии схемы | version, checksum |

PRAGMA: `journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout` (по умолчанию 10 с), `synchronous=NORMAL`. Записи — `BEGIN IMMEDIATE`. Количество ссылок на blob не хранится счётчиком, а считается `COUNT(*)` по объектам (нечему рассинхронизироваться).

## Жизненный цикл

`ensure_bucket` → `create_object` (pending) → `begin_upload` → `UploadSession.write(...)` (любые порции, чанки уходят в backend по мере заполнения; память ≤ 1 чанк на загрузку) → `complete()` (ready) / `abort()`.
`put_object(bucket, key, bytes|file|iterator)` — обёртка. Чтение: `get_metadata[_by_key]` (только ready), `stream` (проверка размера и SHA-256 каждого чанка *до* выдачи, итогового хеша — в конце), `verify_object(deep=False|True)`, `find_blob`, `find_objects_by_sha256`. Удаление: `delete_object`.
Повторная запись в существующий ключ атомарно заменяет предыдущий готовый объект; параллельные записи в один ключ дают ровно один ready.
Объекты не в статусе `ready` никогда не выдаются (`ObjectNotReady`).

### Дедупликация
* SHA-256 содержимого считается при загрузке; `dedup_scope` задаёт границу: содержимое делится только между bucket-ами с одним scope. Поиск по хешу всегда ограничен scope bucket-а, факт существования чужих файлов не раскрывается.
* Если вызывающий знает хеш заранее (`expected_sha256`) и такое содержимое в scope уже есть — **в Telegram ничего не отправляется**: поток всё равно читается целиком и сверяется (владение содержимым доказывается, заявленного хеша недостаточно), объект привязывается к существующему blob.
* Если хеш заранее неизвестен — содержимое придётся загрузить, затем дубликат сворачивается и удаляется. Без знания хеша избежать этой отправки нельзя, не буферизуя файл целиком.
* Физическое содержимое удаляется только когда на blob не осталось ссылок; незавершённая загрузка-сверка тоже держит ссылку.

## Telegram Driver

`sendDocument` (multipart без копирования чанка) → `file_id` + `message_id` в reference; скачивание `getFile` + `/file/bot<token>/<path>`; удаление `deleteMessage`. Токен берётся из `TELEGRAM_BOT_TOKEN`, прокси/IPv4/адрес API — из существующих `TELEGRAM_PROXY_URL`, `TELEGRAM_FORCE_IPV4`, `TELEGRAM_API_BASE`. Токен не логируется (в сообщениях ошибок вычищается; `repr` скрыт).

* Повторы ограничены (`STORAGE_MAX_RETRIES`, экспоненциальная пауза от `STORAGE_RETRY_DELAY`, максимум 60 с). HTTP 429 → ждём `retry_after`+1 с; если он больше 120 с — не блокируем поток, отдаём `TransientBackendError`. 5xx, таймауты, обрывы, некорректный JSON — повтор. 4xx — без повторов.
* Загрузка: размер документа в ответе сверяется с отправленным; ответ без корректного документа → сообщение удаляется и отправка повторяется.
* Скачивание: чанк читается потоком в буфер с ограничением по размеру, `Content-Length` и ожидаемый размер сверяются, обрыв → повтор чанка; SHA-256 проверяет ядро.
* Удаление: «message to delete not found» = данных уже нет (`already_absent`); любой другой отказ → `DeleteRejected`, успехом не считается.
* **Неопределённый результат отправки**: после read-timeout/обрыва/5xx неизвестно, сохранилось ли сообщение. Драйвер повторяет отправку и возвращает `uncertain_attempts`; ядро записывает по строке `storage_orphans` (`uncertain_upload`) с меткой `polaris-storage:v1 blob=… chunk=… sha256=…`, которая также стоит в подписи сообщения в Telegram. Bot API не умеет перечислять историю чата и не даёт идемпотентной отправки, поэтому такие сообщения **автоматически не удаляются** — только регистрируются для ручной очистки.

## Восстановление (`recovery.py`, `StorageService.reconcile()`)

Запускается в фоне при старте и каждые `STORAGE_MAINTENANCE_INTERVAL` секунд; идемпотентно, безопасно параллельно с работой.
Перед отправкой каждого чанка в БД фиксируется строка `pending`, поэтому сбой в любой точке оставляет след.

| Сценарий | Что происходит |
|---|---|
| Процесс упал во время загрузки | blob `uploading` без прогресса `STORAGE_STALE_UPLOAD_SECONDS` → `failed`, объект → `failed`, сохранённые чанки (ссылки в БД) удаляются; активные в процессе загрузки не трогаются |
| Часть чанков принята, загрузка не завершена | то же; `pending`-чанки без ссылки → `storage_orphans` (`interrupted_upload`), **не** удаляются |
| Запись в БД не удалась после отправки | 3 попытки записи; затем данные удаляются сразу (они точно наши); не удалось — `db_write_lost` со ссылкой, reconcile повторяет удаление; БД недоступна совсем — `CRITICAL`-лог со ссылкой |
| Telegram недоступен | загрузка → `failed` и очистка; удаление → `delete_failed` (повтор при каждой сверке до `delete_max_attempts` = 10) |
| Скачивание прервалось | повтор чанка; иначе ошибка, повреждённые данные потребителю не отдаются |
| Удаление частично | объект логически удалён сразу, чанки в `delete_failed` / `delete_rejected`, blob остаётся `deleting` |
| Конфликт параллельных операций | `BEGIN IMMEDIATE`, partial UNIQUE-индексы, проверки статусов в транзакции (`ObjectConflict`) |

В отчёт (`ReconcileReport`, `needs_attention`) попадают, но **не удаляются**: готовые blob без ссылок, несогласованные метаданные чанков, `delete_rejected`, исчерпавшие попытки, suspected-orphans.

## Конфигурация (`.env`, `Settings`)

| Переменная | По умолчанию | |
|---|---|---|
| `STORAGE_ENABLED` | `false` | выключено — ничего не создаётся и не запускается |
| `STORAGE_DB_PATH` | `database/data/storage.db` | относительный путь — от корня `polaris/` |
| `TELEGRAM_BOT_TOKEN` | — | уже существующая |
| `CLOUD_STORAGE_CHAT_ID` | — | чат/канал; бот — админ, желательно с правом удалять сообщения |
| `STORAGE_CHUNK_SIZE_MIB` | 15 | 1…19 |
| `STORAGE_CONNECT_TIMEOUT` / `STORAGE_READ_TIMEOUT` | 10 / 120 с | |
| `STORAGE_MAX_RETRIES` / `STORAGE_RETRY_DELAY` | 3 / 2 с | |
| `STORAGE_MAX_CONCURRENT_UPLOADS` / `…_DOWNLOADS` | 2 / 4 | сверх лимита — `StorageBusy`, без очередей |
| `STORAGE_STALE_UPLOAD_SECONDS` | 1800 | ≥ 60 |
| `STORAGE_MAINTENANCE_INTERVAL` | 600 | 0 — только при старте |
| `TELEGRAM_PROXY_URL`, `TELEGRAM_FORCE_IPV4`, `TELEGRAM_API_BASE` | существующие | |

Валидация при старте: неверные значения или ненастроенный Telegram **не роняют Polaris** — Storage остаётся в состоянии `unavailable` (причина в логе и `get_storage_service().status()`, `get_core()` бросает `StorageUnavailable`). Нечисловые значения в `.env` заменяются значениями по умолчанию с предупреждением.

## Тесты

`PYTHONPATH=src python -m unittest tests.test_storage_database tests.test_storage_core tests.test_storage_telegram tests.test_storage_recovery tests.test_storage_service`
(или `pytest tests/test_storage_*.py`). Реальный токен не нужен: Telegram эмулируется локальным HTTP-сервером (`tests/storage_helpers.py`) с внедрением сбоев (429, 5xx, таймауты, обрывы, битый JSON, отказ удаления…).

## Известные ограничения и технический долг

* Дубликат без заранее известного хеша сначала уходит в Telegram и затем удаляется.
* Orphan-сообщения после неопределённой отправки не находятся автоматически (ограничение Bot API); для ручной очистки есть метка в подписи и таблица `storage_orphans`.
* Telegram может отклонять удаление (права бота, возраст сообщения) — тогда данные физически остаются в чате (`delete_rejected`), хотя логически объект удалён.
* `file_id` теоретически может перестать работать (смена бота/токена); хранится также `message_id`, перевыпуск ссылки автоматически не реализован.
* Брошенная чужим процессом загрузка определяется только по таймеру `STALE_UPLOAD_SECONDS`.
* Загрузка буферизует до одного чанка (≤ 19 MiB) на сеанс; скачивание — один чанк на поток. При лимитах 2/4 это ≈ 40–75 MiB пиково.
* Диапазонные чтения, листинг и multipart добавлены на этапе 2 (см. ниже и s3-api.md).
* Тесты написаны на `unittest` (запускаются и pytest-ом): в среде разработки не было `pytest`/`fastapi`, поэтому существующие тесты проекта и импорт `app.py` с FastAPI не прогонялись — только `py_compile`.

## Расширения этапа 2 (для S3 API)

Только необходимое для S3; существующая логика этапа 1 не переписывалась.

* `StorageCore`: `list_buckets / create_bucket / delete_bucket`, `list_objects(prefix, from_key, limit)`, `stream(object_id, start, end)` (читает только чанки, пересекающиеся с диапазоном; каждый проверяется целиком), `compose_object`, `begin_upload(dedup=False)`, `UploadSession.complete(etag=…)`, `core.multipart`.
* Миграция `0002_s3_support.sql`: `storage_blobs.checksum_kind` (`content` | `composite`) и таблица `storage_multipart_uploads`. Обновление применяется автоматически при старте; откат кода на этап 1 после этого запрещён защитой от «более новой схемы».
* **Составной blob.** Complete multipart не читает и не отправляет данные: чанки частей переносятся в новый blob одной транзакцией. SHA-256 всего содержимого при этом неизвестен (хеш не складывается из хешей частей), поэтому у такого blob `checksum_kind='composite'`: `checksum_sha256` — хеш от перечня SHA-256 чанков, дедупликации по содержимому для него нет, целостность проверяется по SHA-256 каждого чанка. Чанки составного объекта могут быть разного размера (этап 1 это допускал).
* **Части multipart** — обычные объекты служебного bucket-а `polaris-internal.multipart` (скрыт из `list_buckets`, имя зарезервировано) с ключом `<upload_id>/<номер>` и `dedup=False` (чанки части принадлежат только ей). Поэтому перезаливка части, Abort, сбои Telegram и восстановление работают теми же механизмами этапа 1; `reconcile` дополнительно доводит прерванный Abort и удаляет части без загрузки.
* `S3_MULTIPART_EXPIRY_HOURS` (по умолчанию 0 = выключено) отменяет давно забытые multipart-загрузки при фоновой сверке.
