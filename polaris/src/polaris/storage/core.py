"""Storage Core: жизненный цикл объектов, чанки, дедупликация, координация backend.

Ядро ничего не знает о конкретном backend: физические операции идут через BackendDriver,
ссылки на данные — непрозрачные строки. SQL — в repository.py.
"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import re
import threading
import time
from typing import Any, Iterable, Iterator

from polaris.shared.exceptions import ConfigurationError
from polaris.storage import repository as repo
from polaris.storage.backends.base import (
    BackendDriver,
    BackendError,
    DeleteRejected,
    UploadContext,
    UploadResult,
    make_marker,
)
from polaris.storage.database import StorageDatabase
from polaris.storage.errors import (
    BucketNotFound,
    ChecksumMismatch,
    IntegrityError,
    InvalidName,
    ObjectConflict,
    ObjectNotFound,
    ObjectNotReady,
    StorageBusy,
    StorageError,
)
from polaris.storage.models import (
    BlobStatus,
    ChunkStatus,
    IntegrityReport,
    ObjectStatus,
    StorageBlob,
    StorageBucket,
    StorageChunk,
    StorageObject,
)

logger = logging.getLogger(__name__)

_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_READ_PIECE = 1024 * 1024


def _validate_bucket_name(name: str) -> str:
    if not isinstance(name, str) or not _BUCKET_RE.match(name):
        raise InvalidName("имя bucket: 3-63 символа, строчные латинские буквы, цифры, '-' и '.'")
    return name


def _validate_key(key: str) -> str:
    if not isinstance(key, str) or not key or "\x00" in key or len(key.encode("utf-8")) > 1024:
        raise InvalidName("ключ объекта: непустая строка до 1024 байт UTF-8 без NUL")
    return key


def _check_chunks(blob: StorageBlob, chunks: list[StorageChunk]) -> list[str]:
    problems = []
    if blob.chunk_count is not None and blob.chunk_count != len(chunks):
        problems.append(f"чанков {len(chunks)}, ожидалось {blob.chunk_count}")
    if [c.chunk_index for c in chunks] != list(range(len(chunks))):
        problems.append("индексы чанков не образуют последовательность 0..n-1")
    bad = [c.chunk_index for c in chunks if c.status != ChunkStatus.STORED]
    if bad:
        problems.append(f"чанки не в статусе stored: {bad}")
    if sum(c.size_bytes for c in chunks) != blob.size_bytes:
        problems.append("сумма размеров чанков не равна размеру содержимого")
    return problems


class UploadSession:
    """Потоковая загрузка одного объекта: write() любыми порциями -> complete().

    Один сеанс = один поток. Используйте как контекстный менеджер: выход без complete()
    отменяет загрузку и освобождает слот параллельных загрузок. Память — не более одного
    чанка на сеанс.
    """

    def __init__(self, core: "StorageCore", object_id: str, blob_id: str, scope: str,
                 verify_only: bool, expected_sha256: str | None, expected_size: int | None) -> None:
        self.object_id = object_id
        self.blob_id = blob_id
        self.scope = scope
        self.verify_only = verify_only  # содержимое уже есть: данные только сверяются, в backend не уходят
        self.expected_sha256 = expected_sha256
        self.expected_size = expected_size
        self._core = core
        self._sha = hashlib.sha256()
        self._md5 = hashlib.md5(usedforsecurity=False)
        self._buf = bytearray()
        self._index = 0
        self._size = 0
        self._finished = False
        self._beat = time.monotonic()

    def write(self, data: bytes | bytearray | memoryview) -> None:
        if self._finished:
            raise ObjectConflict("загрузка уже завершена или отменена")
        if not len(data):
            return
        try:
            self._sha.update(data)
            self._md5.update(data)
            self._size += len(data)
            if self.verify_only:
                self._core._heartbeat(self)
                return
            self._buf += data
            size = self._core.chunk_size
            while len(self._buf) >= size:
                chunk = bytes(self._buf[:size])
                del self._buf[:size]
                self._core._store_chunk(self, chunk)
        except BaseException:
            self.abort()
            raise

    def complete(self) -> StorageObject:
        if self._finished:
            raise ObjectConflict("загрузка уже завершена или отменена")
        try:
            return self._core._complete(self)
        except BaseException:
            self.abort()
            raise

    def abort(self) -> None:
        if not self._finished:
            self._core._abort(self)

    def __enter__(self) -> "UploadSession":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.abort()


class StorageCore:
    def __init__(
        self,
        db: StorageDatabase,
        driver: BackendDriver,
        *,
        chunk_size: int,
        max_concurrent_uploads: int = 2,
        max_concurrent_downloads: int = 4,
        delete_max_attempts: int = 10,
        stale_upload_seconds: int = 1800,
        heartbeat_interval: float = 20.0,
        sleep=time.sleep,
    ) -> None:
        if not 0 < chunk_size <= driver.max_chunk_size:
            raise ConfigurationError(
                f"размер чанка {chunk_size} недопустим для backend {driver.name} (максимум {driver.max_chunk_size})"
            )
        self.db = db
        self.driver = driver
        self.chunk_size = chunk_size
        self.delete_max_attempts = delete_max_attempts
        self.stale_upload_seconds = stale_upload_seconds
        self._heartbeat_interval = heartbeat_interval
        self._sleep = sleep
        self._upload_slots = threading.BoundedSemaphore(max_concurrent_uploads)
        self._download_slots = threading.BoundedSemaphore(max_concurrent_downloads)
        self._active_lock = threading.Lock()
        self._active_objects: set[str] = set()
        self._active_blobs: set[str] = set()

    # ─── buckets ───

    def ensure_bucket(self, name: str, *, dedup_scope: str | None = None, settings: dict | None = None) -> StorageBucket:
        """Создать bucket, если его нет. По умолчанию scope дедупликации = имя bucket (без обмена содержимым)."""
        _validate_bucket_name(name)
        with self.db.transaction() as conn:
            existing = repo.get_bucket(conn, name)
            if existing:
                if dedup_scope is not None and dedup_scope != existing.dedup_scope:
                    raise StorageError("у существующего bucket другой dedup_scope")
                return existing
            return repo.insert_bucket(conn, name, dedup_scope or name, settings or {})

    def get_bucket(self, name: str) -> StorageBucket:
        bucket = repo.get_bucket(self.db.connection(), name)
        if bucket is None:
            raise BucketNotFound(name)
        return bucket

    # ─── создание и загрузка ───

    def create_object(self, bucket: str, key: str, *, filename: str | None = None,
                      mime_type: str | None = None, metadata: dict[str, Any] | None = None) -> StorageObject:
        _validate_key(key)
        filename = filename if filename is not None else key.rsplit("/", 1)[-1]
        mime_type = mime_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        with self.db.transaction() as conn:
            b = repo.get_bucket(conn, bucket)
            if b is None:
                raise BucketNotFound(bucket)
            oid = repo.insert_object(conn, b.id, key, filename, mime_type, metadata or {})
            return repo.get_object(conn, oid)  # type: ignore[return-value]

    def begin_upload(self, object_id: str, *, expected_sha256: str | None = None,
                     expected_size: int | None = None) -> UploadSession:
        """Начать загрузку. Если задан expected_sha256 и такое содержимое уже есть в scope bucket-а,
        в backend ничего не отправляется: поток целиком читается и сверяется (владение содержимым
        доказывается, а не заявляется), после чего объект привязывается к существующему blob."""
        if expected_sha256 is not None:
            expected_sha256 = expected_sha256.lower()
            if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
                raise InvalidName("expected_sha256: 64 hex-символа")
        if not self._upload_slots.acquire(blocking=False):
            raise StorageBusy("достигнут лимит параллельных загрузок")
        try:
            with self.db.transaction() as conn:
                obj = repo.get_object(conn, object_id)
                if obj is None:
                    raise ObjectNotFound(object_id)
                if obj.status not in (ObjectStatus.PENDING, ObjectStatus.FAILED):
                    raise ObjectConflict(f"загрузка невозможна для объекта в статусе {obj.status}")
                existing = repo.find_ready_blob(conn, obj.dedup_scope, expected_sha256) if expected_sha256 else None
                if existing:
                    blob_id, verify_only = existing.id, True
                else:
                    blob_id, verify_only = repo.insert_blob(conn, obj.dedup_scope, self.driver.name), False
                repo.begin_object_upload(conn, object_id, blob_id)
            session = UploadSession(self, object_id, blob_id, obj.dedup_scope, verify_only, expected_sha256, expected_size)
        except BaseException:
            self._upload_slots.release()
            raise
        with self._active_lock:
            self._active_objects.add(object_id)
            self._active_blobs.add(blob_id)
        return session

    def put_object(self, bucket: str, key: str, source: bytes | Any, *, filename: str | None = None,
                   mime_type: str | None = None, metadata: dict | None = None,
                   expected_sha256: str | None = None, expected_size: int | None = None) -> StorageObject:
        """Удобная обёртка: create_object + потоковая загрузка из bytes / файлового объекта / итератора."""
        obj = self.create_object(bucket, key, filename=filename, mime_type=mime_type, metadata=metadata)
        with self.begin_upload(obj.id, expected_sha256=expected_sha256, expected_size=expected_size) as session:
            if isinstance(source, (bytes, bytearray, memoryview)):
                view = memoryview(source)
                for pos in range(0, len(view), _READ_PIECE):
                    session.write(view[pos:pos + _READ_PIECE])
            elif hasattr(source, "read"):
                while True:
                    piece = source.read(_READ_PIECE)
                    if not piece:
                        break
                    session.write(piece)
            else:
                for piece in source:
                    session.write(piece)
            return session.complete()

    def complete_upload(self, session: UploadSession) -> StorageObject:
        return session.complete()

    def abort_upload(self, session: UploadSession) -> None:
        session.abort()

    # ─── внутренняя механика загрузки ───

    def _heartbeat(self, session: UploadSession) -> None:
        now = time.monotonic()
        if now - session._beat < self._heartbeat_interval:
            return
        session._beat = now
        with self.db.transaction() as conn:
            repo.touch_object(conn, session.object_id)
            if not session.verify_only:
                repo.touch_blob(conn, session.blob_id)

    def _store_chunk(self, session: UploadSession, data: bytes) -> None:
        sha = hashlib.sha256(data).hexdigest()
        index = session._index
        # намерение фиксируется ДО отправки: если процесс умрёт после отправки, останется pending-чанк
        with self.db.transaction() as conn:
            chunk_id = repo.insert_pending_chunk(conn, session.blob_id, index, len(data), sha)
            repo.touch_blob(conn, session.blob_id)
            repo.touch_object(conn, session.object_id)
        session._beat = time.monotonic()
        ctx = UploadContext(session.blob_id, index, sha, len(data))
        try:
            result = self.driver.upload(data, ctx)
        except BaseException as exc:
            self._discard_pending_chunk(chunk_id, ctx, getattr(exc, "uncertain_attempts", 0))
            raise
        self._record_stored(chunk_id, result, ctx)
        session._index += 1

    def _discard_pending_chunk(self, chunk_id: str, ctx: UploadContext, uncertain: int) -> None:
        try:
            with self.db.transaction() as conn:
                for _ in range(uncertain):
                    repo.insert_orphan(conn, self.driver.name, "uncertain_upload", blob_id=ctx.blob_id,
                                       chunk_index=ctx.chunk_index, marker=ctx.marker,
                                       note="результат отправки неизвестен (таймаут/5xx)")
                repo.delete_chunk(conn, chunk_id)
        except Exception:  # noqa: BLE001
            logger.exception("Storage: не удалось зафиксировать неудачную отправку чанка %s", ctx.marker)

    def _record_stored(self, chunk_id: str, result: UploadResult, ctx: UploadContext) -> None:
        last: Exception | None = None
        for attempt in range(3):
            try:
                with self.db.transaction() as conn:
                    repo.mark_chunk_stored(conn, chunk_id, result.reference)
                    for _ in range(result.uncertain_attempts):
                        repo.insert_orphan(conn, self.driver.name, "uncertain_upload", blob_id=ctx.blob_id,
                                           chunk_index=ctx.chunk_index, marker=ctx.marker,
                                           note="повторная отправка после неопределённого результата")
                return
            except Exception as exc:  # noqa: BLE001
                last = exc
                self._sleep(0.2 * (attempt + 1))
        # Данные в backend есть, а записать ссылку не удалось. Они точно наши — удаляем сразу.
        logger.critical("Storage: не удалось записать метаданные чанка %s после успешной отправки", ctx.marker)
        try:
            self.driver.delete(result.reference)
            tracked = True
        except Exception:  # noqa: BLE001
            try:
                with self.db.transaction() as conn:
                    repo.insert_orphan(conn, self.driver.name, "db_write_lost", blob_id=ctx.blob_id,
                                       chunk_index=ctx.chunk_index, marker=ctx.marker, reference=result.reference)
                tracked = True
            except Exception:  # noqa: BLE001
                tracked = False
                logger.critical("Storage ORPHAN backend=%s reference=%s marker=%s",
                                self.driver.name, result.reference, ctx.marker)
        if tracked:  # судьба сообщения известна — pending-строка больше не нужна (иначе появится ложный suspected)
            try:
                with self.db.transaction() as conn:
                    repo.delete_chunk(conn, chunk_id)
            except Exception:  # noqa: BLE001
                pass
        raise StorageError("не удалось сохранить метаданные чанка") from last

    def _complete(self, session: UploadSession) -> StorageObject:
        if not session.verify_only and session._buf:
            chunk = bytes(session._buf)
            session._buf.clear()
            self._store_chunk(session, chunk)
        sha, md5, size = session._sha.hexdigest(), session._md5.hexdigest(), session._size
        if session.expected_sha256 and session.expected_sha256 != sha:
            raise ChecksumMismatch("SHA-256 полученных данных не совпал с ожидаемым")
        if session.expected_size is not None and session.expected_size != size:
            raise ChecksumMismatch(f"размер данных {size}, ожидалось {session.expected_size}")

        discard_blob: str | None = None
        replaced: str | None = None
        with self.db.transaction() as conn:
            obj = repo.get_object(conn, session.object_id)
            if obj is None or obj.status != ObjectStatus.UPLOADING or obj.blob_id != session.blob_id:
                raise ObjectConflict("загрузка отменена или восстановлена параллельной операцией")
            blob = repo.get_blob(conn, session.blob_id)
            if blob is None:
                raise ObjectConflict("blob загрузки исчез")
            if session.verify_only:
                if blob.status != BlobStatus.READY or blob.checksum_sha256 != sha:
                    raise ObjectConflict("целевое содержимое изменилось во время загрузки")
                target = blob
            else:
                if blob.status != BlobStatus.UPLOADING:
                    raise ObjectConflict("загрузка отменена или восстановлена параллельной операцией")
                problems = _check_chunks(
                    StorageBlob(**{**blob.__dict__, "size_bytes": size, "chunk_count": session._index}),
                    repo.list_chunks(conn, blob.id),
                )
                if problems:
                    raise StorageError("некорректный набор чанков: " + "; ".join(problems))
                existing = repo.find_ready_blob(conn, blob.dedup_scope, sha)
                if existing is not None and existing.id != blob.id:
                    # такое содержимое уже есть: наш дубликат уходит на удаление, объект ссылается на старое
                    target = existing
                    repo.set_blob_status(conn, blob.id, BlobStatus.DELETING)
                    discard_blob = blob.id
                else:
                    repo.mark_blob_ready(conn, blob.id, size, session._index, sha, md5)
                    target = repo.get_blob(conn, blob.id)  # type: ignore[assignment]
            old = repo.get_ready_object_by_key(conn, obj.bucket_id, obj.object_key)
            if old is not None and old.id != obj.id:
                repo.set_object_status(conn, old.id, ObjectStatus.DELETING)
                replaced = old.id
            repo.mark_object_ready(conn, obj.id, target)  # заодно переключает blob_id на target
            ready = repo.get_object(conn, obj.id)
        self._finish_session(session)
        if discard_blob:
            self._purge_blob(discard_blob)
        if replaced:
            self._finish_object_deletion(replaced)
        return ready  # type: ignore[return-value]

    def _abort(self, session: UploadSession) -> None:
        purge: str | None = None
        try:
            with self.db.transaction() as conn:
                obj = repo.get_object(conn, session.object_id)
                if obj is not None and obj.status == ObjectStatus.UPLOADING and obj.blob_id == session.blob_id:
                    repo.fail_object(conn, obj.id)
                    if session.verify_only:
                        purge = self._release_blob(conn, session.blob_id)
                    else:
                        blob = repo.get_blob(conn, session.blob_id)
                        if blob is not None and blob.status == BlobStatus.UPLOADING:
                            repo.set_blob_status(conn, blob.id, BlobStatus.FAILED)
                            purge = blob.id
        finally:
            self._finish_session(session)
        if purge:
            self._purge_blob(purge)

    def _finish_session(self, session: UploadSession) -> None:
        if session._finished:
            return
        session._finished = True
        session._buf.clear()
        with self._active_lock:
            self._active_objects.discard(session.object_id)
            self._active_blobs.discard(session.blob_id)
        self._upload_slots.release()

    def is_active(self, *, object_id: str | None = None, blob_id: str | None = None) -> bool:
        with self._active_lock:
            return (object_id in self._active_objects) or (blob_id in self._active_blobs)

    # ─── чтение ───

    def get_object(self, object_id: str) -> StorageObject:
        """Запись объекта в любом статусе (служебное чтение). Для выдачи файла — get_metadata/stream."""
        obj = repo.get_object(self.db.connection(), object_id)
        if obj is None:
            raise ObjectNotFound(object_id)
        return obj

    def get_metadata(self, object_id: str) -> StorageObject:
        obj = self.get_object(object_id)
        if obj.status != ObjectStatus.READY:
            raise ObjectNotReady(f"объект в статусе {obj.status}")
        return obj

    def get_metadata_by_key(self, bucket: str, key: str) -> StorageObject:
        conn = self.db.connection()
        b = repo.get_bucket(conn, bucket)
        if b is None:
            raise BucketNotFound(bucket)
        obj = repo.get_ready_object_by_key(conn, b.id, key)
        if obj is None:
            raise ObjectNotFound(f"{bucket}/{key}")
        return obj

    def find_blob(self, bucket: str, sha256: str) -> StorageBlob | None:
        """Готовое физическое содержимое с таким SHA-256 в границах dedup_scope этого bucket."""
        scope = self.get_bucket(bucket).dedup_scope
        return repo.find_ready_blob(self.db.connection(), scope, sha256.lower())

    def find_objects_by_sha256(self, bucket: str, sha256: str) -> list[StorageObject]:
        """Готовые объекты с таким содержимым — только внутри dedup_scope bucket-а."""
        scope = self.get_bucket(bucket).dedup_scope
        return repo.list_objects_by_sha(self.db.connection(), scope, sha256.lower())

    def _ready_chunks(self, obj: StorageObject) -> tuple[StorageBlob, list[StorageChunk]]:
        conn = self.db.connection()
        blob = repo.get_blob(conn, obj.blob_id) if obj.blob_id else None
        if blob is None or blob.status != BlobStatus.READY:
            raise IntegrityError("у готового объекта нет готового содержимого")
        chunks = repo.list_chunks(conn, blob.id)
        problems = _check_chunks(blob, chunks)
        if problems:
            raise IntegrityError("; ".join(problems))
        return blob, chunks

    def stream(self, object_id: str) -> Iterator[bytes]:
        """Поток содержимого готового объекта.

        Каждый чанк целиком скачивается и проверяется (размер, SHA-256) ДО выдачи потребителю;
        итоговый SHA-256 проверяется в конце. Если поток оборвался исключением (в т.ч. IntegrityError),
        результат нельзя считать успешным — даже если часть байт уже получена.
        """
        obj = self.get_metadata(object_id)
        blob, chunks = self._ready_chunks(obj)
        return self._iter_stream(obj, blob, chunks)

    def _iter_stream(self, obj: StorageObject, blob: StorageBlob, chunks: list[StorageChunk]) -> Iterator[bytes]:
        if not self._download_slots.acquire(blocking=False):
            raise StorageBusy("достигнут лимит параллельных скачиваний")
        try:
            total, size = hashlib.sha256(), 0
            for chunk in chunks:
                data = self._fetch_chunk(chunk)
                total.update(data)
                size += len(data)
                yield bytes(data)
            if size != blob.size_bytes or total.hexdigest() != blob.checksum_sha256:
                raise IntegrityError(f"итоговая проверка объекта {obj.id} не пройдена")
        finally:
            self._download_slots.release()

    def _fetch_chunk(self, chunk: StorageChunk) -> bytes | bytearray:
        data = self.driver.download(chunk.backend_reference)  # type: ignore[arg-type]
        if len(data) != chunk.size_bytes:
            raise IntegrityError(f"чанк {chunk.chunk_index}: размер {len(data)}, ожидалось {chunk.size_bytes}")
        if hashlib.sha256(data).hexdigest() != chunk.checksum_sha256:
            raise IntegrityError(f"чанк {chunk.chunk_index}: SHA-256 не совпал")
        return data

    def verify_object(self, object_id: str, *, deep: bool = False) -> IntegrityReport:
        """Проверка целостности. По умолчанию — метаданные и размеры чанков в backend (без скачивания);
        deep=True скачивает все чанки и сверяет SHA-256 и итоговый хеш."""
        obj = self.get_metadata(object_id)
        conn = self.db.connection()
        blob = repo.get_blob(conn, obj.blob_id) if obj.blob_id else None
        if blob is None or blob.status != BlobStatus.READY:
            return IntegrityReport(False, ["нет готового содержимого"])
        chunks = repo.list_chunks(conn, blob.id)
        problems = _check_chunks(blob, chunks)
        if (obj.size_bytes, obj.checksum_sha256) != (blob.size_bytes, blob.checksum_sha256):
            problems.append("метаданные объекта расходятся с содержимым")
        checked = 0
        total = hashlib.sha256()
        for chunk in chunks:
            if chunk.status != ChunkStatus.STORED:
                continue
            try:
                if deep:
                    data = self._fetch_chunk(chunk)
                    total.update(data)
                else:
                    meta = self.driver.get_metadata(chunk.backend_reference)  # type: ignore[arg-type]
                    if meta.size_bytes is not None and meta.size_bytes != chunk.size_bytes:
                        problems.append(f"чанк {chunk.chunk_index}: в backend {meta.size_bytes} байт, ожидалось {chunk.size_bytes}")
                checked += 1
            except (IntegrityError, BackendError) as exc:
                problems.append(f"чанк {chunk.chunk_index}: {exc}")
        if deep and not problems and total.hexdigest() != blob.checksum_sha256:
            problems.append("итоговый SHA-256 не совпал")
        return IntegrityReport(not problems, problems, checked)

    def backend_health(self):
        return self.driver.health_check()

    # ─── удаление ───

    def delete_object(self, object_id: str) -> bool:
        """Логическое удаление (мгновенно) + физическое. True — физически удалено всё; False — часть
        чанков осталась (сбой/отказ backend), состояние записано в БД и будет обработано reconcile."""
        with self.db.transaction() as conn:
            obj = repo.get_object(conn, object_id)
            if obj is None:
                raise ObjectNotFound(object_id)
            if obj.status == ObjectStatus.UPLOADING:
                raise ObjectConflict("объект загружается: сначала отмените загрузку")
            repo.set_object_status(conn, object_id, ObjectStatus.DELETING)
        return self._finish_object_deletion(object_id)

    def _finish_object_deletion(self, object_id: str) -> bool:
        with self.db.transaction() as conn:
            obj = repo.get_object(conn, object_id)
            if obj is None:
                return True
            repo.delete_object(conn, object_id)
            purge = self._release_blob(conn, obj.blob_id) if obj.blob_id else None
        return self._purge_blob(purge) if purge else True

    def _release_blob(self, conn, blob_id: str) -> str | None:
        """В транзакции: если на готовый blob больше нет ссылок — перевести в deleting. Возвращает его id."""
        blob = repo.get_blob(conn, blob_id)
        if blob is None or blob.status != BlobStatus.READY or repo.count_blob_refs(conn, blob_id) > 0:
            return None
        repo.set_blob_status(conn, blob_id, BlobStatus.DELETING)
        return blob_id

    def _purge_blob(self, blob_id: str) -> bool:
        """Физически удалить чанки blob (failed/deleting). Ничего не бросает: итог пишется в БД."""
        chunks = repo.list_chunks(self.db.connection(), blob_id)
        for chunk in chunks:
            if chunk.status == ChunkStatus.DELETE_REJECTED:
                continue
            if chunk.status == ChunkStatus.DELETE_FAILED and chunk.delete_attempts >= self.delete_max_attempts:
                continue
            if chunk.status == ChunkStatus.PENDING:
                # отправка была начата, результат неизвестен, ссылки нет — удалить нечем
                with self.db.transaction() as conn:
                    repo.insert_orphan(
                        conn, self.driver.name, "interrupted_upload", blob_id=blob_id,
                        chunk_index=chunk.chunk_index,
                        marker=make_marker(blob_id, chunk.chunk_index, chunk.checksum_sha256),
                        note="загрузка прервана до получения ссылки",
                    )
                    repo.delete_chunk(conn, chunk.id)
                continue
            try:
                self.driver.delete(chunk.backend_reference)  # type: ignore[arg-type]
            except DeleteRejected as exc:
                self._chunk_delete_failed(chunk.id, str(exc), rejected=True)
            except Exception as exc:  # noqa: BLE001
                self._chunk_delete_failed(chunk.id, str(exc), rejected=False)
            else:
                with self.db.transaction() as conn:
                    repo.delete_chunk(conn, chunk.id)
        with self.db.transaction() as conn:
            if repo.count_chunks(conn, blob_id) > 0:
                return False
            blob = repo.get_blob(conn, blob_id)
            if blob is not None and blob.status in (BlobStatus.FAILED, BlobStatus.DELETING):
                repo.delete_blob(conn, blob_id)
            return True

    def _chunk_delete_failed(self, chunk_id: str, error: str, *, rejected: bool) -> None:
        logger.warning("Storage: удаление чанка %s не удалось (%s): %s", chunk_id,
                       "отказ backend" if rejected else "временный сбой", error)
        with self.db.transaction() as conn:
            repo.record_chunk_delete_failure(conn, chunk_id, error, rejected=rejected)
