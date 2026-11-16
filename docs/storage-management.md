# Управление Polaris Storage (этап 3)

Раздел **Storage** в Mini App (боковая панель, непосредственно над Settings) и management API позволяют настраивать,
проверять и использовать Storage без ручного редактирования `.env`. Хранилище остаётся частью Polaris (без отдельных
процессов/контейнеров); S3 API и Core не менялись по сути — добавлены управление ключами и права на операции.

## Разделы интерфейса

| Раздел | Что делает |
|---|---|
| **Overview** | Реальный статус Storage Core / Telegram Backend / S3 API / SQLite: статус, последнее успешное обращение, время проверки, текст ошибки. При открытии запускаются настоящие проверки; пока их не было — «Не проверялось», а не «Online». Точная статистика (buckets, логические объекты, физические blobs, незавершённые multipart, объёмы) и быстрые действия (Create Bucket, Create Access Key, Test Storage, Open Diagnostics). |
| **Configuration** | Telegram Bot Token (маскированный), Chat ID, S3 (вкл., путь endpoint, регион, лимит запроса, допуск часов SigV4), фактический endpoint. Сохранение: проверка → просмотр diff → явное подтверждение → запись в `.env`. |
| **Buckets** | Таблица (имя, дата, число объектов, размер), создание, удаление **только пустых**, страница bucket-а: список объектов, метаданные, Download, Delete, Copy Object Key. Telegram `file_id`/`message_id`/`chat_id` не показываются. |
| **Access Keys** | Список (название, Access Key ID, права, buckets, даты, последнее использование, Active/Revoked), создание, Revoke, Rotate, изменение описания, удаление отозванных. Secret Access Key показывается один раз. |
| **Diagnostics** | Health checks (SQLite, Core, Telegram, S3, CredentialProvider) с временем; Storage Tests (конфигурация, Telegram, запись, чтение, SHA-256, удаление, полный S3-цикл); незавершённые multipart с Abort; Recovery (orphan-записи и ошибки удаления — только просмотр). |

### Применение настроек
Настройки пишутся в `.env` (существующей функцией `write_env_value`) и вступают в силу **только после перезапуска Polaris**:
значения читаются при старте, горячей перезагрузки у проекта нет, а S3-маршруты монтируются при запуске. Интерфейс не перезапускает
сервис скрытно: после сохранения показывается предупреждение и кнопка «Перезапустить Polaris» (существующий `/api/restart`, с подтверждением).
Рядом с параметром видно, что он «ждёт перезапуска» (сохранённое значение ≠ действующему). Кнопка «Проверить Telegram» проверяет
**сохранённую** конфигурацию, даже если она ещё не применена.

**Токен бота.** Поле управляет `STORAGE_TELEGRAM_BOT_TOKEN` — отдельным ботом хранилища; если пусто, используется основной
`TELEGRAM_BOT_TOKEN`. Основной токен из интерфейса менять нельзя: им проверяется вход в Mini App (initData), и ошибка в нём заперла бы
администратора вне Polaris. Сервер никогда не возвращает токен (только `••••••••` + последние 4 символа).

Не настраивается из интерфейса: Host и параметры reverse proxy, addressing style (фиксирован path-style).

## Management API

Все маршруты требуют `require_admin` (Telegram initData админа или `X-Polaris-Token`); S3-ключи (`Authorization: AWS4-HMAC-SHA256 …`)
доступа к ним не дают. Авторизация не cookie-based, поэтому CSRF неприменим (чужой сайт не может приложить `X-Polaris-Token`).
Ответы `{"success": true, "data": …}`; ошибки — HTTP-статус и `detail` (строка или `{message, errors:{поле: текст}}` для 422).

| Метод и путь | Назначение |
|---|---|
| `GET /api/storage/status` | компоненты (последние результаты проверок), точная статистика, состояние S3, `restart_required` |
| `GET` / `PUT /api/storage/config` | настройки (секреты маскированы) / `{changes, confirm}`: без `confirm: true` — только проверка и diff, с ним — запись в `.env` |
| `POST /api/storage/test` | `{steps?, use_saved?}`: config, telegram, write, read, hash, delete, s3 (зависимости добавляются автоматически) |
| `GET` / `POST /api/storage/buckets` | список со статистикой / создание |
| `GET` / `DELETE /api/storage/buckets/{bucket}` | сведения / удаление пустого (409, если нет) |
| `GET /api/storage/buckets/{bucket}/objects` | `?prefix=&after=&limit=` |
| `GET` / `DELETE /api/storage/objects/{id}`, `GET …/download` | метаданные (без внутренних ссылок) / удаление / потоковое скачивание с проверкой SHA-256 чанков |
| `GET` / `POST /api/storage/credentials` | список (без секретов) / создание: ответ содержит `secret_access_key` один раз, `Cache-Control: no-store` |
| `POST …/credentials/{id}/revoke`, `…/rotate`, `PATCH …/{id}`, `DELETE …/{id}` | отзыв / ротация (новый секрет один раз) / название и описание / удаление отозванного |
| `GET /api/storage/diagnostics`, `POST …/diagnostics/run` | сводка (checks, multipart, recovery, последний тест) / запуск проверок `{checks?}` |
| `GET /api/storage/multipart`, `POST …/multipart/{upload_id}/abort` | незавершённые загрузки / отмена (только по явному действию администратора) |

Ключи из `.env` отображаются в списке только для чтения (`managed_in_env`); их нельзя отозвать из интерфейса.

## Access Keys: модель и хранение секретов

**Расхождение с ТЗ.** Хранить Secret Access Key «солёным хешем» (как пароль) для SigV4 невозможно: подпись проверяется
пересчётом HMAC от самого секрета, а необратимый хеш секрета этого не позволяет. Выбрано минимальное безопасное решение:

* в БД лежит только случайное **семя** (32 байта), версия секрета и отпечаток мастер-ключа — ни секрета, ни чего-либо, из чего его
  можно восстановить без мастер-ключа;
* секрет = `base64url(HMAC-SHA256(мастер-ключ, access_key_id ‖ семя ‖ версия)[:30])` (40 символов); он показывается один раз при создании/ротации
  и при каждой проверке подписи выводится заново в памяти (в логи, API-ответы и БД не попадает);
* **мастер-ключ** — 32 случайных байта вне БД: файл `database/data/storage_master.key` (создаётся с правами 0600, переживает `/update`)
  или `STORAGE_MASTER_KEY`; сравнение подписей — `hmac.compare_digest`.

Утечка одной БД секретов не раскрывает. Утечка БД **и** мастер-ключа раскрывает секреты (как и любая схема, где сервер должен знать
секрет для проверки подписи). **Сделайте резервную копию мастер-ключа:** если он потерян, ключи из интерфейса перестанут проходить
проверку (`InvalidAccessKeyId`), их нужно перевыпустить (Rotate работает с новым мастер-ключом); в Diagnostics это видно как «нужна ротация».

### Права
Права выбираются при создании и дальше не расширяются (изменить можно только название и описание; для других прав — новый ключ).
`MultipartUpload` требует `WriteObject` (multipart создаёт объекты). Доступ — к выбранным buckets или ко всем.

| Операция S3 | Нужные права ключа |
|---|---|
| HeadBucket, GetBucketLocation, ListObjects(V2) | `ListBucket` |
| GetObject, HeadObject | `ReadObject` |
| PutObject (в т.ч. перезапись) | `WriteObject` |
| DeleteObject, DeleteObjects | `DeleteObject` |
| CreateMultipartUpload, UploadPart, CompleteMultipartUpload | `WriteObject` **и** `MultipartUpload` |
| AbortMultipartUpload, ListParts, ListMultipartUploads | `MultipartUpload` |
| ListBuckets | любой ключ; в ответе только доступные ему buckets |
| CreateBucket, DeleteBucket | ключи из `.env` и администратор в интерфейсе (ключам Access Management недоступно) |

Проверка идёт на каждом запросе до чтения тела и до обращения к bucket-у: чужой и несуществующий bucket неотличимы (`AccessDenied`).
Загрузки привязаны к bucket-у и ключу: чужой `uploadId` через путь «своего» bucket-а не найти (`NoSuchUpload`). Presigned URL
наследует права ключа, которым подписан, и проверяется в момент использования: после Revoke выданные ссылки перестают работать.
Revoke/Rotate вступают в силу немедленно (каждая проверка читает БД, кеша нет). «Последнее использование» записывается после успешной
проверки подписи и обновляется не чаще раза в минуту.

## Диагностика и тесты

Проверки (`database`, `core`, `telegram`, `s3`, `credentials`) выполняются по-настоящему и возвращают результат, время выполнения, время проверки и
последнее успешное обращение. Telegram проверяется вызовами `getMe`/`getChat`/`getChatMember` (не расходует сообщения чата).

Storage Tests: запись 64 КиБ случайных данных в служебный bucket `polaris-internal.selftest`, чтение, сверка SHA-256, удаление (с проверкой
физического удаления), и **полный S3-цикл внутри процесса** (PUT/GET/HEAD/List/Range/DELETE, отклонение неверной подписи, отказ вне разрешённых buckets,
отказ после Revoke) через настоящее S3-приложение с временным служебным ключом и временным bucket-ом `polaris-selftest-*`. Пользовательские данные не
используются. Очистка выполняется в `finally`; остатки прерванных тестов удаляются в начале следующего запуска (имена с этими префиксами
зарезервированы и скрыты). Одновременно выполняется один тест (иначе 409). S3-цикл идёт без сети, поэтому reverse proxy он не проверяет — для этого
`scripts/s3_sdk_check.py` и `scripts/s3_key_check.py` против живого адреса.

## Изменения SQLite (`0003_access_keys.sql`)
`storage_access_keys` (id, название, описание, `access_key_id`, `secret_seed`, `secret_version`, `master_fp`, права JSON, `all_buckets`, статус, `system`, даты,
`last_used_at`) и `storage_access_key_buckets` (ключ ↔ имена buckets; привязка по имени, как политика). Ключи самотеста (`system=1`) в списках скрыты.

## Тестирование

```bash
cd polaris
PYTHONPATH=src python -m unittest tests.test_storage_access_keys tests.test_s3_permissions tests.test_storage_admin \
    tests.test_storage_routes tests.test_storage_ui        # или: pytest tests/
python scripts/s3_key_check.py check   --endpoint https://HOST/s3 --access-key ID --secret-key SECRET --bucket my-bucket
python scripts/s3_key_check.py revoked --endpoint https://HOST/s3 --access-key ID --secret-key SECRET --bucket my-bucket   # после Revoke
```
* `test_storage_access_keys`, `test_s3_permissions` — хранение секретов (в файлах БД секрета нет), права по каждой операции, buckets, multipart, presigned,
  отзыв и ротация через HTTP; `test_storage_admin` — management-слой (маскирование, diff, диагностика, самотесты с очисткой при сбоях);
  `test_storage_routes` — защита маршрутов (S3-ключи не проходят) и отображение ошибок.
* `test_storage_ui` — **настоящий Chromium (Playwright)** на реальных `index.html`/JS: навигация, пустые состояния, ошибки API, создание bucket-а и ключа,
  секрет только один раз, Revoke/Rotate и проверка живым S3-клиентом (в т.ч. curl `--aws-sigv4`), конфигурация с diff и подтверждением, диагностика, мобильная
  вёрстка, экранирование HTML. Пропускается без Playwright/Chromium. Маршруты в этих тестах работают поверх заглушки FastAPI (`tests/fastapi_stub.py`) —
  настоящий FastAPI не требуется и не проверяется.
* `Boto3KeyFlowTests` и `scripts/s3_key_check.py` — ключ из интерфейса с настоящим boto3, затем Revoke (пропускается без boto3).

## Известные ограничения
* Конфигурация применяется только перезапуском; нет аудит-журнала действий администратора (только записи в лог без секретов).
* Права и список buckets ключа после создания не редактируются; привязка к bucket-у — по имени (пересозданный bucket с тем же именем наследует доступ).
* Скачивание из интерфейса идёт через `fetch` в память браузера — для больших файлов используйте presigned URL.
* Ротация мгновенная (без периода, когда действуют оба секрета); Access Key ID при ротации сохраняется.
* Ключи из `.env` из интерфейса не меняются.
* Существующая проверка `X-Polaris-Token` в Polaris сравнивает токен обычным `==` (не константным временем) — это код проекта до этапа 3, он не менялся; желательно перейти на `hmac.compare_digest`.
