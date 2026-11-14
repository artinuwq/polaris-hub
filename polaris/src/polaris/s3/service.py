"""S3 Service: преобразование S3-операций в вызовы Storage Core.

Синхронный и не знает про HTTP/ASGI: все блокирующие вызовы (SQLite, Telegram) выполняются в потоках
верхним уровнем. К таблицам Storage и Telegram слой не обращается — только к публичному API StorageCore.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import zlib
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable, Iterator

from polaris.s3 import errors, util
from polaris.storage import core as storage_core
from polaris.storage.backends.base import BackendError, PermanentBackendError
from polaris.storage.errors import (
    BucketExists, BucketNotEmpty, BucketNotFound, ChecksumMismatch, IntegrityError, InvalidName, InvalidRange,
    MultipartError, ObjectConflict, ObjectNotFound, ObjectNotReady, StorageBusy, StorageError, StorageUnavailable,
)
from polaris.storage.models import StorageObject

logger = logging.getLogger(__name__)

MAX_KEYS_LIMIT = 1000
MAX_USER_METADATA = 2048
OWNER_ID = "polaris"
_STANDARD_HEADERS = ("cache-control", "content-disposition", "content-encoding", "content-language", "expires")
_REJECTED_HEADERS = {  # заголовки, обещающие то, чего хранилище не умеет: честная ошибка вместо имитации
    "x-amz-website-redirect-location": "website redirect",
    "x-amz-server-side-encryption": "server-side encryption",
    "x-amz-server-side-encryption-customer-algorithm": "server-side encryption",
    "x-amz-tagging": "object tagging",
    "x-amz-object-lock-mode": "object lock",
    "x-amz-object-lock-retain-until-date": "object lock",
    "x-amz-object-lock-legal-hold": "object lock",
    "x-amz-grant-read": "ACL grants",
    "x-amz-grant-write": "ACL grants",
    "x-amz-grant-full-control": "ACL grants",
}


def map_exception(exc: BaseException) -> errors.S3Error:
    """Любое внутреннее исключение -> безопасная S3-ошибка (без деталей Telegram/SQL/traceback)."""
    if isinstance(exc, errors.S3Error):
        return exc
    if isinstance(exc, BucketNotFound):
        return errors.NoSuchBucket()
    if isinstance(exc, (ObjectNotFound, ObjectNotReady)):
        return errors.NoSuchKey()
    if isinstance(exc, BucketExists):
        return errors.BucketAlreadyOwnedByYou()
    if isinstance(exc, BucketNotEmpty):
        return errors.BucketNotEmpty()
    if isinstance(exc, ChecksumMismatch):
        return errors.XAmzContentSHA256Mismatch()
    if isinstance(exc, InvalidRange):
        return errors.InvalidRange()
    if isinstance(exc, InvalidName):
        return errors.InvalidArgument("недопустимое имя bucket или ключ объекта")
    if isinstance(exc, MultipartError):
        factory = getattr(errors, exc.code, None) or errors.InvalidRequest
        return factory(str(exc))
    if isinstance(exc, ObjectConflict):
        return errors.S3Error("OperationAborted", "A conflicting operation is currently in progress against this "
                              "resource. Please try again.", 409)
    if isinstance(exc, StorageBusy):
        return errors.SlowDown()
    if isinstance(exc, StorageUnavailable):
        return errors.ServiceUnavailable()
    if isinstance(exc, PermanentBackendError):
        logger.error("S3: постоянная ошибка backend: %s", type(exc).__name__)
        return errors.InternalError()
    if isinstance(exc, BackendError):
        logger.warning("S3: временная ошибка backend: %s", type(exc).__name__)
        return errors.ServiceUnavailable()
    if isinstance(exc, IntegrityError):
        logger.error("S3: нарушена целостность данных: %s", exc)
        return errors.InternalError()
    if isinstance(exc, StorageError):
        logger.error("S3: ошибка Storage: %s", exc)
        return errors.InternalError()
    logger.error("S3: непредвиденная ошибка", exc_info=exc)
    return errors.InternalError()


# ─── метаданные объекта ───

@dataclass
class ObjectHeaders:
    content_type: str
    metadata: dict[str, Any]  # то, что сохраняется в StorageObject.metadata


def parse_object_headers(headers: dict[str, str]) -> ObjectHeaders:
    for name, feature in _REJECTED_HEADERS.items():
        if name in headers:
            raise errors.NotImplementedS3(f"{feature} не поддерживается")
    acl = headers.get("x-amz-acl")
    if acl is not None and acl != "private":
        raise errors.NotImplementedS3("ACL не поддерживаются: все объекты приватны")
    storage_class = headers.get("x-amz-storage-class")
    if storage_class is not None and storage_class != "STANDARD":
        raise errors.InvalidArgument("поддерживается только класс хранения STANDARD")
    meta = {k[len("x-amz-meta-"):]: v for k, v in headers.items() if k.startswith("x-amz-meta-")}
    if sum(len(k) + len(v.encode("utf-8")) for k, v in meta.items()) > MAX_USER_METADATA:
        raise errors.MetadataTooLarge()
    std = {k: v for k in _STANDARD_HEADERS if (v := headers.get(k)) is not None}
    if "content-encoding" in std:  # aws-chunked — транспортное кодирование SDK, оно не часть объекта
        tokens = [t.strip() for t in std["content-encoding"].split(",") if t.strip().lower() != "aws-chunked"]
        if tokens:
            std["content-encoding"] = ", ".join(tokens)
        else:
            del std["content-encoding"]
    return ObjectHeaders(headers.get("content-type") or "binary/octet-stream", {"s3": {"meta": meta, "headers": std}})


def _s3_meta(obj: StorageObject) -> tuple[dict[str, str], dict[str, str]]:
    s3 = obj.metadata.get("s3", {}) if isinstance(obj.metadata, dict) else {}
    return dict(s3.get("meta", {})), dict(s3.get("headers", {}))


# ─── проверка целостности входящего тела ───

class BodyIntegrity:
    """Считает контрольные суммы тела параллельно с записью и сверяет их до завершения загрузки."""

    _TRAILER_ALGOS = ("crc32", "sha1", "sha256")

    def __init__(self, headers: dict[str, str]) -> None:
        self.md5 = hashlib.md5(usedforsecurity=False)
        self.sha1 = hashlib.sha1(usedforsecurity=False)
        self.sha256 = hashlib.sha256()
        self.crc32 = 0
        self.size = 0
        self.expected: dict[str, str] = {}
        if (content_md5 := headers.get("content-md5")) is not None:
            try:
                raw = base64.b64decode(content_md5, validate=True)
            except (binascii.Error, ValueError):
                raise errors.InvalidDigest() from None
            if len(raw) != 16:
                raise errors.InvalidDigest()
            self.expected["md5"] = content_md5
        for algo in self._TRAILER_ALGOS:
            if (value := headers.get(f"x-amz-checksum-{algo}")) is not None:
                self.expected[algo] = value

    def update(self, data: bytes) -> None:
        self.md5.update(data)
        self.sha1.update(data)
        self.sha256.update(data)
        self.crc32 = zlib.crc32(data, self.crc32)
        self.size += len(data)

    def verify(self, trailers: dict[str, str] | None = None) -> None:
        expected = dict(self.expected)
        for name, value in (trailers or {}).items():
            if name.startswith("x-amz-checksum-") and name[15:] in self._TRAILER_ALGOS:
                expected[name[15:]] = value
        actual = {
            "md5": base64.b64encode(self.md5.digest()).decode(),
            "sha1": base64.b64encode(self.sha1.digest()).decode(),
            "sha256": base64.b64encode(self.sha256.digest()).decode(),
            "crc32": base64.b64encode(self.crc32.to_bytes(4, "big")).decode(),
        }
        for algo, value in expected.items():
            if value != actual[algo]:
                raise errors.BadDigest()
        # crc32c / crc64nvme не проверяются (нет в stdlib); целостность обеспечивают SHA-256 чанков хранилища


# ─── сервис ───

@dataclass
class GetPlan:
    status: int
    obj: StorageObject
    headers: dict[str, str]
    start: int
    end: int  # включительно; при size == 0 end = -1

    @property
    def length(self) -> int:
        return self.end - self.start + 1


@dataclass
class ListResult:
    contents: list[StorageObject] = field(default_factory=list)
    common_prefixes: list[str] = field(default_factory=list)
    truncated: bool = False
    next_marker: tuple[str, str] | None = None  # ('K', ключ) | ('P', общий префикс) — где остановились


def encode_token(kind: str, value: str) -> str:
    return base64.urlsafe_b64encode((kind + ":" + value).encode("utf-8")).decode().rstrip("=")


def decode_token(token: str) -> tuple[str, str]:
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode("utf-8")
        kind, sep, value = raw.partition(":")
        if sep and kind in ("K", "P"):
            return kind, value
    except (binascii.Error, UnicodeDecodeError, ValueError):
        pass
    raise errors.InvalidArgument("The continuation token provided is incorrect")


class S3Service:
    def __init__(self, get_core: Callable[[], "storage_core.StorageCore"], *, region: str = "us-east-1") -> None:
        self._get_core = get_core
        self.region = region

    @property
    def core(self):
        return self._get_core()  # StorageUnavailable, если Storage не готов

    # ── buckets ──

    def list_buckets(self):
        return self.core.list_buckets()

    def create_bucket(self, name: str, location: str = "") -> None:
        util.validate_bucket_name(name)
        if location and location != self.region:
            raise errors.S3Error("InvalidLocationConstraint", f"The specified location-constraint is not valid; "
                                 f"this endpoint serves region '{self.region}'", 400)
        try:
            self.core.create_bucket(name)
        except InvalidName:
            raise errors.InvalidBucketName() from None

    def head_bucket(self, name: str) -> None:
        self._bucket(name)

    def delete_bucket(self, name: str) -> None:
        self.core.delete_bucket(name)

    def _bucket(self, name: str):
        if name.startswith(storage_core.INTERNAL_BUCKET_PREFIX):
            raise errors.NoSuchBucket()
        return self.core.get_bucket(name)

    # ── объекты ──

    def head_object(self, bucket: str, key: str) -> StorageObject:
        self._bucket(bucket)
        return self.core.get_metadata_by_key(bucket, key)

    def object_headers(self, obj: StorageObject, overrides: dict[str, str] | None = None) -> dict[str, str]:
        meta, std = _s3_meta(obj)
        headers = {"ETag": util.quote_etag(obj.etag), "Last-Modified": util.http_date(obj.completed_at),
                   "Accept-Ranges": "bytes", "Content-Type": obj.mime_type}
        for k, v in std.items():
            headers["-".join(p.capitalize() for p in k.split("-"))] = v
        for k, v in meta.items():
            headers[f"x-amz-meta-{k}"] = v
        for k, v in (overrides or {}).items():
            headers[k] = v
        return headers

    def prepare_get(self, bucket: str, key: str, req_headers: dict[str, str],
                    overrides: dict[str, str] | None = None) -> GetPlan:
        obj = self.head_object(bucket, key)
        headers = self.object_headers(obj, overrides)
        status = self._conditions(obj, req_headers)
        if status == 304:
            return GetPlan(304, obj, {k: v for k, v in headers.items() if k in ("ETag", "Last-Modified")}, 0, -1)
        rng = util.parse_range(req_headers.get("range"), obj.size_bytes)
        if rng is None:
            headers["Content-Length"] = str(obj.size_bytes)
            return GetPlan(200, obj, headers, 0, obj.size_bytes - 1)
        start, end = rng
        headers["Content-Length"] = str(end - start + 1)
        headers["Content-Range"] = f"bytes {start}-{end}/{obj.size_bytes}"
        return GetPlan(206, obj, headers, start, end)

    @staticmethod
    def _conditions(obj: StorageObject, h: dict[str, str]) -> int:
        """200 — продолжать; 304 — не изменялся; бросает PreconditionFailed (412)."""
        etag = obj.etag or ""
        modified = util.parse_ts(obj.completed_at) if obj.completed_at else None
        norm = lambda v: [x.strip().strip('"') for x in v.split(",")]  # noqa: E731
        if (v := h.get("if-match")) is not None and v.strip() != "*" and etag not in norm(v):
            raise errors.PreconditionFailed()
        if modified and (v := h.get("if-unmodified-since")) is not None and h.get("if-match") is None:
            since = util.parse_http_date(v)
            if since and modified.replace(microsecond=0) > since:
                raise errors.PreconditionFailed()
        if (v := h.get("if-none-match")) is not None:
            if v.strip() == "*" or etag in norm(v):
                return 304
        elif modified and (v := h.get("if-modified-since")) is not None:
            since = util.parse_http_date(v)
            if since and modified.replace(microsecond=0) <= since:
                return 304
        return 200

    def open_stream(self, plan: GetPlan) -> Iterator[bytes]:
        if plan.obj.size_bytes == 0:
            return iter(())
        if plan.status == 200:
            return self.core.stream(plan.obj.id)
        return self.core.stream(plan.obj.id, plan.start, plan.end)

    def begin_put(self, bucket: str, key: str, headers: dict[str, str], *, expected_sha256: str | None):
        """Создать объект и начать загрузку. Возвращает UploadSession ядра."""
        self._bucket(bucket)
        util.validate_key(key)
        parsed = parse_object_headers(headers)
        obj = self.core.create_object(bucket, key, mime_type=parsed.content_type, metadata=parsed.metadata)
        return self.core.begin_upload(obj.id, expected_sha256=expected_sha256)

    def delete_object(self, bucket: str, key: str) -> None:
        self._bucket(bucket)
        try:
            obj = self.core.get_metadata_by_key(bucket, key)
        except ObjectNotFound:
            return  # как в S3: удаление отсутствующего ключа успешно
        try:
            self.core.delete_object(obj.id)
        except ObjectNotFound:
            pass

    def delete_objects(self, bucket: str, keys: list[str]) -> tuple[list[str], list[tuple[str, str, str]]]:
        self._bucket(bucket)
        deleted, failed = [], []
        for key in keys:
            try:
                self.delete_object(bucket, key)
                deleted.append(key)
            except Exception as exc:  # noqa: BLE001
                e = map_exception(exc)
                failed.append((key, e.code, e.message))
        return deleted, failed

    # ── листинг ──

    def list_objects(self, bucket: str, *, prefix: str, delimiter: str, max_keys: int, from_key: str) -> ListResult:
        """Общий алгоритм v1/v2. from_key — включительная нижняя граница. Токен продолжения кодирует
        ключ или общий префикс, на котором остановились (next_marker уже закодирован вызывающим)."""
        self._bucket(bucket)
        result = ListResult()
        if max_keys <= 0:
            return result
        items: list[tuple[str, Any]] = []  # ('key', obj) | ('prefix', str)
        cursor = from_key
        batch = max(100, min(max_keys + 1, 1000))
        done = False
        while not done and len(items) <= max_keys:
            objs = self.core.list_objects(bucket, prefix=prefix, from_key=cursor, limit=batch)
            if not objs:
                break
            restart = False
            for obj in objs:
                key = obj.object_key
                if delimiter:
                    idx = key.find(delimiter, len(prefix))
                    if idx >= 0:
                        common = key[:idx + len(delimiter)]
                        if not (items and items[-1] == ("prefix", common)):
                            items.append(("prefix", common))
                        nxt = _successor(common)  # перепрыгиваем всё содержимое общего префикса
                        if nxt is None:
                            done = True
                        else:
                            cursor = nxt
                        restart = True
                        break
                items.append(("key", obj))
                cursor = key + "\0"
                if len(items) > max_keys:
                    done = True
                    break
            if not restart and len(objs) < batch:
                break
        result.truncated = len(items) > max_keys
        for kind, value in items[:max_keys]:
            if kind == "key":
                result.contents.append(value)
            else:
                result.common_prefixes.append(value)
        if result.truncated:
            last_kind, last = items[max_keys - 1]
            result.next_marker = ("K", last.object_key) if last_kind == "key" else ("P", last)  # type: ignore[assignment]
        return result

    # ── multipart ──

    def create_multipart(self, bucket: str, key: str, headers: dict[str, str]):
        self._bucket(bucket)
        util.validate_key(key)
        parsed = parse_object_headers(headers)
        return self.core.multipart.create(bucket, key, mime_type=parsed.content_type, metadata=parsed.metadata)

    def begin_part(self, bucket: str, key: str, upload_id: str, part_number: int, *, expected_sha256: str | None):
        self._bucket(bucket)
        return self.core.multipart.begin_part(upload_id, part_number, expected_sha256=expected_sha256,
                                              bucket=bucket, key=key)

    def complete_multipart(self, bucket: str, key: str, upload_id: str, parts: list[tuple[int, str]]):
        self._bucket(bucket)
        return self.core.multipart.complete(upload_id, parts, bucket=bucket, key=key)

    def abort_multipart(self, bucket: str, key: str, upload_id: str) -> None:
        self._bucket(bucket)
        self.core.multipart.abort(upload_id, bucket=bucket, key=key)

    def list_parts(self, bucket: str, key: str, upload_id: str, marker: int, max_parts: int):
        self._bucket(bucket)
        return self.core.multipart.list_parts(upload_id, after_part=marker, limit=max_parts, bucket=bucket, key=key)

    def list_uploads(self, bucket: str, *, prefix: str, key_marker: str, upload_id_marker: str, max_uploads: int):
        self._bucket(bucket)
        after = (key_marker, upload_id_marker) if key_marker else None
        return self.core.multipart.list_uploads(bucket, prefix=prefix, after=after, limit=max_uploads)


def _successor(prefix: str) -> str | None:
    from polaris.storage.repository import key_successor

    return key_successor(prefix)
