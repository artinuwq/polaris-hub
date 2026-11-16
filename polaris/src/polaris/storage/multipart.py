"""Multipart-загрузки: состояние в SQLite, части — обычные объекты служебного bucket-а.

Это отдельный уровень по отношению к чанкам backend-а: часть может быть любого размера,
а чанки (<= 19 MiB для Telegram) создаёт ядро при записи части. Complete не читает и не
отправляет данные: чанки частей переносятся в итоговый blob одной транзакцией
(StorageCore.compose_object), поэтому большой файл не скачивается обратно из Telegram.

Гарантии:
* части не видны как готовый объект до Complete (лежат в polaris-internal.multipart);
* перезагрузка части с тем же номером атомарно заменяет прежнюю (старые данные удаляются);
* Abort физически удаляет части средствами этапа 1 (сбои Telegram фиксируются в БД и
  повторяются при reconcile); прерванный Abort доводится до конца;
* части, загруженные параллельно с Abort/Complete и оставшиеся без загрузки, подбираются reconcile.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone

from polaris.storage import repository as repo
from polaris.storage.errors import BucketNotFound, MultipartError, ObjectNotFound, StorageError
from polaris.storage.models import MultipartPart, MultipartUpload

logger = logging.getLogger(__name__)

MAX_PARTS = 10_000
DEFAULT_MIN_PART_SIZE = 5 * 1024 * 1024
_UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _bucket_for_parts() -> str:
    from polaris.storage.core import MULTIPART_BUCKET

    return MULTIPART_BUCKET


def normalize_etag(value: str) -> str:
    return value.strip().strip('"').lower()


class MultipartManager:
    def __init__(self, core, *, min_part_size: int = DEFAULT_MIN_PART_SIZE) -> None:
        self._core = core
        self.min_part_size = min_part_size

    @property
    def _db(self):
        return self._core.db

    @staticmethod
    def part_key(upload_id: str, part_number: int) -> str:
        return f"{upload_id}/{part_number:05d}"

    # ─── жизненный цикл ───

    def create(self, bucket: str, key: str, *, filename: str | None = None, mime_type: str = "application/octet-stream",
               metadata: dict | None = None) -> MultipartUpload:
        from polaris.storage.core import _validate_key

        _validate_key(key)
        upload_id = uuid.uuid4().hex
        with self._db.transaction() as conn:
            b = repo.get_bucket(conn, bucket)
            if b is None:
                raise BucketNotFound(bucket)
            name = filename if filename is not None else key.rsplit("/", 1)[-1]
            repo.insert_upload(conn, upload_id, b.id, key, name, mime_type, metadata or {})
            return repo.get_upload(conn, upload_id)  # type: ignore[return-value]

    def get(self, upload_id: str, *, bucket: str | None = None, key: str | None = None) -> MultipartUpload:
        upload = repo.get_upload(self._db.connection(), upload_id) if _UPLOAD_ID_RE.match(upload_id or "") else None
        if upload is None or upload.status != "open" or (bucket is not None and upload.bucket != bucket) \
                or (key is not None and upload.object_key != key):
            raise MultipartError("NoSuchUpload", "загрузка не найдена, завершена или отменена")
        return upload

    def begin_part(self, upload_id: str, part_number: int, *, expected_sha256: str | None = None,
                   expected_size: int | None = None, bucket: str | None = None, key: str | None = None):
        """Начать запись части; возвращает UploadSession ядра (write/complete/abort)."""
        if not 1 <= part_number <= MAX_PARTS:
            raise MultipartError("InvalidArgument", f"номер части должен быть от 1 до {MAX_PARTS}")
        self.get(upload_id, bucket=bucket, key=key)
        core = self._core
        core.ensure_bucket(_bucket_for_parts())
        obj = core.create_object(_bucket_for_parts(), self.part_key(upload_id, part_number),
                                 mime_type="application/octet-stream")
        return core.begin_upload(obj.id, expected_sha256=expected_sha256, expected_size=expected_size, dedup=False)

    def list_parts(self, upload_id: str, *, after_part: int = 0, limit: int = 1000,
                   bucket: str | None = None, key: str | None = None) -> tuple[list[MultipartPart], bool]:
        """Готовые части по возрастанию номера, номер > after_part. Второй элемент — есть ли ещё."""
        self.get(upload_id, bucket=bucket, key=key)
        objs = self._part_objects(upload_id, from_number=after_part + 1, limit=limit + 1)
        parts = [self._to_part(o) for o in objs]
        return parts[:limit], len(parts) > limit

    def _part_objects(self, upload_id: str, *, from_number: int = 1, limit: int = MAX_PARTS + 1, conn=None):
        conn = conn or self._db.connection()
        b = repo.get_bucket(conn, _bucket_for_parts())
        if b is None:
            return []
        return repo.list_ready_objects(conn, b.id, f"{upload_id}/", self.part_key(upload_id, from_number), limit)

    @staticmethod
    def _to_part(obj) -> MultipartPart:
        return MultipartPart(int(obj.object_key.rsplit("/", 1)[1]), obj.size_bytes, obj.etag or "",
                             obj.completed_at or obj.created_at, obj.id)

    def list_all(self, limit: int = 500) -> list[dict]:
        """Все multipart-загрузки (для диагностики): bucket, ключ, число и размер загруженных частей, состояние."""
        conn = self._db.connection()
        b = repo.get_bucket(conn, _bucket_for_parts())
        stats = repo.part_stats(conn, b.id) if b is not None else {}
        out = []
        for u in repo.list_all_uploads(conn, limit):
            count, size = stats.get(u.id, (0, 0))
            out.append({"upload_id": u.id, "bucket": u.bucket, "key": u.object_key, "status": u.status,
                        "created_at": u.created_at, "parts": count, "bytes": size})
        return out

    def list_uploads(self, bucket: str, *, prefix: str = "", after: tuple[str, str] | None = None,
                     limit: int = 1000) -> tuple[list[MultipartUpload], bool]:
        b = self._core.get_bucket(bucket)
        rows = repo.list_uploads(self._db.connection(), b.id, prefix, after, limit + 1)
        return rows[:limit], len(rows) > limit

    def complete(self, upload_id: str, requested: list[tuple[int, str]], *, bucket: str | None = None,
                 key: str | None = None):
        """Собрать объект из перечисленных частей. requested — [(номер, ETag)] по возрастанию номеров."""
        if not requested:
            raise MultipartError("MalformedXML", "не указана ни одна часть")
        numbers = [n for n, _ in requested]
        if any(b <= a for a, b in zip(numbers, numbers[1:])):
            raise MultipartError("InvalidPartOrder", "номера частей должны идти строго по возрастанию")
        discard: list[str] = []
        with self._db.transaction() as conn:
            upload = repo.get_upload(conn, upload_id) if _UPLOAD_ID_RE.match(upload_id or "") else None
            if upload is None or upload.status != "open" or (bucket is not None and upload.bucket != bucket) \
                    or (key is not None and upload.object_key != key):
                raise MultipartError("NoSuchUpload", "загрузка не найдена, завершена или отменена")
            stored = {self._to_part(o).part_number: o for o in self._part_objects(upload_id, conn=conn)}
            chosen = []
            for number, etag in requested:
                obj = stored.get(number)
                if obj is None or normalize_etag(etag) != (obj.etag or ""):
                    raise MultipartError("InvalidPart", f"часть {number} не найдена или ETag не совпадает")
                chosen.append(obj)
            for obj in chosen[:-1]:
                if obj.size_bytes < self.min_part_size:
                    raise MultipartError("EntityTooSmall", "часть меньше минимального размера (все, кроме последней)")
            used = {o.id for o in chosen}
            discard = [o.id for o in stored.values() if o.id not in used]
            etag = hashlib.md5(b"".join(bytes.fromhex(o.etag) for o in chosen), usedforsecurity=False).hexdigest()
            etag = f"{etag}-{len(chosen)}"
            result = self._core.compose_object(upload.bucket, upload.object_key, [o.id for o in chosen],
                                               filename=upload.filename, mime_type=upload.mime_type,
                                               metadata=upload.metadata, etag=etag)
            repo.delete_upload(conn, upload_id)
        for object_id in discard:  # непереданные части выбрасываются, как в S3
            self._delete_part(object_id)
        return result

    def abort(self, upload_id: str, *, bucket: str | None = None, key: str | None = None) -> None:
        with self._db.transaction() as conn:
            upload = repo.get_upload(conn, upload_id) if _UPLOAD_ID_RE.match(upload_id or "") else None
            if upload is None or (bucket is not None and upload.bucket != bucket) \
                    or (key is not None and upload.object_key != key):
                raise MultipartError("NoSuchUpload", "загрузка не найдена, завершена или отменена")
            repo.set_upload_status(conn, upload_id, "aborting")
        self._finish_abort(upload_id)

    def _finish_abort(self, upload_id: str) -> None:
        for obj in self._part_objects(upload_id):
            self._delete_part(obj.id)
        with self._db.transaction() as conn:
            repo.delete_upload(conn, upload_id)

    def _delete_part(self, object_id: str) -> None:
        try:
            self._core.delete_object(object_id)
        except ObjectNotFound:
            pass
        except StorageError:
            logger.exception("Storage: не удалось удалить часть multipart %s", object_id)

    # ─── восстановление ───

    def abort_expired(self, older_than_seconds: int) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        count = 0
        for upload in repo.list_uploads_older_than(self._db.connection(), cutoff):
            try:
                self.abort(upload.id)
                count += 1
            except MultipartError:
                pass
        return count

    def reconcile(self) -> dict[str, int]:
        """Довести прерванные Abort до конца и удалить части, у которых нет загрузки."""
        finished = orphans = 0
        conn = self._db.connection()
        for upload in repo.list_uploads_with_status(conn, "aborting"):
            self._finish_abort(upload.id)
            finished += 1
        known = repo.all_upload_ids(conn)
        b = repo.get_bucket(conn, _bucket_for_parts())
        if b is not None:
            for obj in repo.list_ready_objects(conn, b.id, "", "", 1000):
                if obj.object_key.split("/", 1)[0] not in known:
                    self._delete_part(obj.id)
                    orphans += 1
        return {"aborts_finished": finished, "orphan_parts_removed": orphans}
