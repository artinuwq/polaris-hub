# Polaris S3 API (этап 2)

Ограниченная **S3-compatible** реализация поверх Polaris Storage — не полная реализация AWS S3.
Файлы по-прежнему хранит Storage Core (SQLite-метаданные + Telegram), S3 API — только ещё один интерфейс к нему:
`HTTP → polaris.s3.api (ASGI) → polaris.s3.service → StorageCore → BackendDriver`.
Работает внутри процесса Polaris, без отдельного сервера/контейнера/MinIO. Архитектура описана в [storage.md](storage.md).

## Включение

В `.env` (после `STORAGE_ENABLED=true` и настройки Telegram-backend-а, см. storage.md):

```
S3_ENABLED=true
S3_ACCESS_KEY_ID=polaris-key
S3_SECRET_ACCESS_KEY=<длинная случайная строка>      # openssl rand -base64 32
```

Затем `systemctl restart polaris`. Если S3 включён, но нет ключей или параметры неверны, Polaris стартует без S3 и пишет причину в лог; если Storage недоступен, S3 отвечает `503 ServiceUnavailable`.

* **Endpoint:** `https://<хост Polaris>/s3` (путь настраивается `S3_PATH_PREFIX`; корень `/` занят Mini App). **Только path-style** (`/s3/<bucket>/<key>`), virtual-hosted-style (`bucket.host`) не поддерживается.
* **Регион:** `us-east-1` (`S3_REGION`). Регион в подписи клиента должен совпадать, иначе `AuthorizationHeaderMalformed`.
* **Ключи** двух видов, проверка подписи одна:
  * **Access Keys из интерфейса** (раздел Storage → Access Keys, [storage-management.md](storage-management.md)): свои права (`ListBucket`, `ReadObject`, `WriteObject`, `DeleteObject`, `MultipartUpload`) и список buckets; права проверяются при каждом запросе, в том числе по presigned URL; Revoke и Rotate действуют немедленно. Secret Access Key в базе не хранится.
  * **Ключи из `.env`** (`S3_ACCESS_KEY_ID`/`S3_SECRET_ACCESS_KEY`, `S3_CREDENTIALS=id:secret,…`, отключение — `S3_DISABLED_KEYS=id`): как и раньше имеют **полный доступ**, включая создание и удаление buckets; переход на Access Keys их не ломает. Эти секреты лежат только в `.env` (как токен бота).
  Создавать и удалять buckets ключи Access Management не могут (это действие администратора в интерфейсе или ключа из `.env`).
### За reverse proxy (nginx/Caddy)
SigV4 подписывает путь и заголовок `Host`: прокси **не должен** отрезать префикс `/s3` и менять `Host`. Для больших загрузок отключите буферизацию и лимит тела:
`client_max_body_size 0; proxy_request_buffering off; proxy_buffering off; proxy_read_timeout 3600s; proxy_http_version 1.1;`.

## Подключение: boto3

```python
import boto3
from botocore.config import Config

s3 = boto3.client(
    "s3",
    endpoint_url="https://polaris.example.com/s3",
    aws_access_key_id="polaris-key",
    aws_secret_access_key="…",
    region_name="us-east-1",
    config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),  # path-style и SigV4 обязательны
)

s3.create_bucket(Bucket="backups")
s3.put_object(Bucket="backups", Key="docs/hello.txt", Body=b"hello", ContentType="text/plain", Metadata={"owner": "me"})
print(s3.get_object(Bucket="backups", Key="docs/hello.txt")["Body"].read())
print(s3.get_object(Bucket="backups", Key="docs/hello.txt", Range="bytes=0-1")["Body"].read())
for page in s3.get_paginator("list_objects_v2").paginate(Bucket="backups", Prefix="docs/", Delimiter="/"):
    print([o["Key"] for o in page.get("Contents", [])], [p["Prefix"] for p in page.get("CommonPrefixes", [])])

# большие файлы: multipart выбирается автоматически
from boto3.s3.transfer import TransferConfig
cfg = TransferConfig(multipart_threshold=8 * 1024**2, multipart_chunksize=8 * 1024**2, max_concurrency=2)
s3.upload_file("big.iso", "backups", "iso/big.iso", Config=cfg)
s3.download_file("backups", "iso/big.iso", "big.copy", Config=cfg)

# presigned URL: секрет клиенту не передаётся
url = s3.generate_presigned_url("get_object", Params={"Bucket": "backups", "Key": "docs/hello.txt"}, ExpiresIn=3600)
up = s3.generate_presigned_url("put_object", Params={"Bucket": "backups", "Key": "inbox/new.bin"}, ExpiresIn=600)
```

`aws` CLI: `aws --endpoint-url https://polaris.example.com/s3 s3 cp file s3://backups/` (в `~/.aws/config`: `s3 = addressing_style = path`). Подойдут и rclone (`provider = Other`, `force_path_style = true`), s3cmd и т.п. — но совместимость проверялась только указанным ниже.

## Поддерживаемые операции

| Группа | Операции |
|---|---|
| Buckets | ListBuckets, CreateBucket (409 `BucketAlreadyOwnedByYou`, если уже есть), HeadBucket, DeleteBucket (только пустой; открытые multipart-загрузки тоже блокируют), GetBucketLocation |
| Объекты | PutObject, GetObject, HeadObject, DeleteObject (204 даже для отсутствующего ключа), DeleteObjects |
| Листинг | ListObjectsV2 (`prefix`, `delimiter`, `max-keys` ≤ 1000, `continuation-token` — непрозрачный, `start-after`, `encoding-type=url`) и ListObjects v1 (`marker`) |
| Заголовки | `Content-Type`, `Content-Length`, `Last-Modified`, `ETag` (MD5; у multipart — `md5-N`), `Cache-Control`, `Content-Disposition`, `Content-Encoding`, `Content-Language`, `Expires`, `x-amz-meta-*` (≤ 2 KiB), `response-*` в query, условные `If-Match / If-None-Match / If-Modified-Since / If-Unmodified-Since` для GET/HEAD |
| Range | `bytes=a-b`, `a-`, `-n` → `206` + `Content-Range`; `416 InvalidRange`; некорректный/множественный Range игнорируется (как в S3) |
| Multipart | CreateMultipartUpload, UploadPart, CompleteMultipartUpload, AbortMultipartUpload, ListParts, ListMultipartUploads |
| Аутентификация | SigV4 в заголовке `Authorization` и presigned URL (GET/PUT и любые методы); `UNSIGNED-PAYLOAD`, подписанный SHA-256 тела, `STREAMING-AWS4-HMAC-SHA256-PAYLOAD[-TRAILER]` и `STREAMING-UNSIGNED-PAYLOAD-TRAILER` (aws-chunked); `Content-MD5` и `x-amz-checksum-crc32/sha1/sha256` проверяются |

Ключи — произвольный UTF-8 до 1024 байт, без управляющих символов (`\x00–\x1f` отклоняются: иначе нельзя построить корректный XML). Листинг отдаёт ключи в порядке UTF-8 binary. Если клиент отправляет хеш тела, а такое содержимое уже есть в том же `dedup_scope`, данные в Telegram не уходят (тело всё равно читается и сверяется).

## Не поддерживается (честные ошибки `501 NotImplemented`, а не имитация)

CopyObject / UploadPartCopy; версионирование; ACL и policy (запись `x-amz-acl` не `private` отклоняется); тегирование; шифрование (SSE) и object lock; lifecycle, CORS, website, replication, notifications; Select; Torrent; условная запись `If-Match/If-None-Match` на PUT/Complete; SigV2 и SigV4a; virtual-hosted-style; классы хранения кроме `STANDARD`; проверка `x-amz-checksum-crc32c/crc64nvme` (принимаются без сверки — целостность всё равно обеспечивают SHA-256 чанков). Нет шардирования по пользователям: все ключи равноправны.

## Что получается из-за Telegram Backend Driver

* **Размер чанка 15 MiB** (максимум 19 MiB — `getFile` отдаёт ≤ 20 MB). Один S3-объект = N сообщений-документов в чате `CLOUD_STORAGE_CHAT_ID`. Скорость и лимиты определяет Telegram (≈ 20 сообщений/мин в группе; 429 учитывается через `retry_after`, но очень большие загрузки идут медленно). Клиентам с короткими таймаутами нужны увеличенные `read_timeout`.
* **Range без временных файлов:** читаются только чанки, пересекающиеся с диапазоном, каждый целиком (до 19 MiB) и с проверкой SHA-256; память ≤ один чанк на запрос. Запрос на 1 байт стоит скачивания чанка.
* **GetObject** проверяет каждый чанк до отправки клиенту; при сбое **до** первого байта клиент получает `5xx`, при сбое **посреди** ответа соединение обрывается (HTTP-статус уже отправлен) — клиент увидит неполный ответ, а не «успешный» повреждённый файл.
* **Multipart — отдельный уровень** (части S3 любого размера ≥ 5 MiB, кроме последней; ≤ 10 000 частей): каждая часть записывается чанками как обычный объект служебного bucket-а, а `Complete` переносит чанки частей в итоговый объект **одной транзакцией без обращения к Telegram** (быстро, не зависит от размера). Следствия: чанки составного объекта могут быть меньше 15 MiB (при частях по 5–8 MiB сообщений больше); у такого объекта нет SHA-256 всего содержимого (только по чанкам), он не участвует в дедупликации. Незавершённые загрузки хранятся в `storage.db` и переживают перезапуск; Abort и просроченные загрузки удаляют сообщения Telegram (если Telegram отказал — состояние записано и повторяется при сверке).
* **Удаление** логически мгновенное; физическое зависит от прав бота. Отклонённое Telegram-ом удаление не маскируется: данные остаются в чате (`delete_rejected` в отчёте reconcile), а в S3 объект уже не виден.
* Внутренние `file_id`, `chat_id`, ответы Telegram, SQL и traceback клиенту не отдаются: ошибки backend-а → `503 ServiceUnavailable` / `500 InternalError` с нейтральным текстом, подробности только в логе.
* Один процесс веб-приложения: тяжёлые операции выполняются в отдельном пуле потоков и ограничены `STORAGE_MAX_CONCURRENT_UPLOADS/DOWNLOADS` (сверх лимита — `503 SlowDown`, SDK повторяет запрос), поэтому основной Polaris не блокируется. Пиковая память ≈ (загрузки + скачивания) × размер чанка.

## Модель безопасности

SigV4 как у AWS: подпись покрывает метод, путь, query, подписанные заголовки (в т.ч. `host`), хеш тела и дату; допустимое расхождение часов `S3_CLOCK_SKEW_SECONDS` (900 с); presigned URL — до 7 суток и только в пределах `X-Amz-Expires`. Сравнение подписей — `hmac.compare_digest`. Повторное воспроизведение перехваченного запроса внутри окна времени возможно так же, как у самого S3 (nonce-кеша нет); TLS на уровне reverse proxy обязателен для внешнего доступа. Анонимный доступ запрещён полностью. Запросы без `x-amz-content-sha256` с телом отклоняются. Лимит размера запроса `S3_MAX_REQUEST_SIZE_MIB` проверяется заранее по заголовкам и по факту чтения; простой при чтении тела — `S3_BODY_TIMEOUT`; XML-тела ограничены 4 MiB, DTD/сущности в XML запрещены. В логах нет query-строк, заголовков подписи и секретов.

## Конфигурация

| Переменная | По умолчанию | |
|---|---|---|
| `S3_ENABLED` | `false` | |
| `S3_PATH_PREFIX` | `/s3` | `/seg[/seg…]`, без завершающего `/` |
| `S3_REGION` | `us-east-1` | |
| `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` | — | основной ключ |
| `S3_CREDENTIALS` / `S3_DISABLED_KEYS` | — | `id:secret,id2:secret2` / `id,id2` |
| `S3_MAX_REQUEST_SIZE_MIB` | 5120 | |
| `S3_CLOCK_SKEW_SECONDS` | 900 | |
| `S3_MIN_PART_SIZE_MIB` | 5 | |
| `S3_MULTIPART_EXPIRY_HOURS` | 0 | 0 — не истекают |
| `S3_BODY_TIMEOUT` | 60 | сек простоя при чтении тела |

## Тестирование

```bash
cd polaris
PYTHONPATH=src python -m unittest tests.test_s3_sigv4 tests.test_s3_unit tests.test_s3_api tests.test_s3_api_extra \
    tests.test_storage_phase2 tests.test_s3_boto3      # или: pytest tests/
pip install boto3 requests && python scripts/s3_sdk_check.py --endpoint http://127.0.0.1:8000/s3 \
    --access-key … --secret-key …          # те же проверки против живого Polaris (добавьте --legacy-checksums для старого поведения SDK)
```

* `test_s3_sigv4` — официальные тест-векторы AWS (GET с Range, PUT со спецсимволами, `?lifecycle`, листинг, presigned, цепочка подписей aws-chunked).
* `test_s3_api*` — полный путь через настоящие HTTP-сокеты (тестовый ASGI-сервер `tests/asgi_server.py`), mock-backend, независимый клиент **curl `--aws-sigv4`**.
* `test_s3_boto3` и `scripts/s3_sdk_check.py` — настоящий boto3/botocore (14 сценариев: bucket, put/get/head, list, Range, Unicode, multipart `upload_file`, `download_file`, presigned GET/PUT, неверные credentials, delete_objects, ручной multipart). Тесты пропускаются, если boto3 не установлен.
