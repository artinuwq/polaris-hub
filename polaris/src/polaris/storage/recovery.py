"""Reconciliation: обнаружение незавершённых операций и безопасное восстановление.

Принципы:
* Автоматически удаляется только то, что БД однозначно считает нашим и ненужным:
  чанки заброшенных/дублирующих/удаляемых blob и «потерянные» ссылки (db_write_lost).
* Сообщения, чью принадлежность подтвердить нельзя (неопределённый результат отправки,
  прерванная загрузка без ссылки), только регистрируются в storage_orphans и в отчёте.
* Загрузки, активные в этом процессе, не трогаются; чужие считаются заброшенными только
  после stale_upload_seconds без прогресса (каждый чанк обновляет updated_at).
* Операции идемпотентны, можно запускать повторно и параллельно с рабочей нагрузкой.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from polaris.storage import repository as repo
from polaris.storage.backends.base import DeleteRejected
from polaris.storage.core import StorageCore
from polaris.storage.models import BlobStatus, ObjectStatus

logger = logging.getLogger(__name__)


@dataclass
class ReconcileReport:
    abandoned_uploads: int = 0
    finished_object_deletions: int = 0
    blobs_purged: int = 0
    blobs_still_pending: int = 0
    orphans_deleted: int = 0
    suspected_orphans: int = 0  # без ссылки: принадлежность не подтверждена, вручную
    orphans_pending: int = 0  # со ссылкой: удаление будет повторено
    chunks_delete_rejected: int = 0
    chunks_delete_exhausted: int = 0
    anomalies: list[str] = field(default_factory=list)

    @property
    def needs_attention(self) -> bool:
        return bool(
            self.anomalies or self.suspected_orphans or self.orphans_pending or self.chunks_delete_rejected or self.chunks_delete_exhausted
        )

    def summary(self) -> str:
        parts = [f"{k}={v}" for k, v in self.__dict__.items() if k != "anomalies" and v]
        parts += self.anomalies
        return ", ".join(parts) or "без изменений"


class Reconciler:
    def __init__(self, core: StorageCore) -> None:
        self.core = core
        self.db = core.db

    def run(self, *, stale_seconds: int | None = None, limit: int = 500) -> ReconcileReport:
        core = self.core
        stale = core.stale_upload_seconds if stale_seconds is None else stale_seconds
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=stale)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        report = ReconcileReport()

        # 1. заброшенные загрузки: blob в uploading без прогресса
        for blob in repo.list_blobs_with_status(self.db.connection(), BlobStatus.UPLOADING, cutoff, limit):
            if core.is_active(blob_id=blob.id):
                continue
            with self.db.transaction() as conn:
                fresh = repo.get_blob(conn, blob.id)
                if fresh is None or fresh.status != BlobStatus.UPLOADING:
                    continue
                for obj in repo.list_objects_of_blob(conn, blob.id):
                    if obj.status == ObjectStatus.UPLOADING:
                        repo.fail_object(conn, obj.id)
                repo.set_blob_status(conn, blob.id, BlobStatus.FAILED)
            report.abandoned_uploads += 1
            logger.warning("Storage: загрузка blob %s заброшена, чанки будут удалены", blob.id)

        # 2. объекты в uploading, чей blob уже готов (дедуп-сверка прервалась) или пропал
        for obj in repo.list_objects_with_status(self.db.connection(), ObjectStatus.UPLOADING, cutoff, limit):
            if core.is_active(object_id=obj.id):
                continue
            purge = None
            with self.db.transaction() as conn:
                fresh = repo.get_object(conn, obj.id)
                if fresh is None or fresh.status != ObjectStatus.UPLOADING:
                    continue
                blob = repo.get_blob(conn, fresh.blob_id) if fresh.blob_id else None
                if blob is not None and blob.status == BlobStatus.UPLOADING:
                    continue  # им занимается шаг 1 по собственному таймеру blob
                repo.fail_object(conn, fresh.id)
                if blob is not None:
                    purge = core._release_blob(conn, blob.id)
            report.abandoned_uploads += 1
            if purge:
                core._purge_blob(purge)

        # 3. логическое удаление, прерванное на полпути
        for obj in repo.list_objects_with_status(self.db.connection(), ObjectStatus.DELETING, None, limit):
            core._finish_object_deletion(obj.id)
            report.finished_object_deletions += 1

        # 4. blob в failed/deleting: повторить физическое удаление
        for status in (BlobStatus.FAILED, BlobStatus.DELETING):
            for blob in repo.list_blobs_with_status(self.db.connection(), status, None, limit):
                if core._purge_blob(blob.id):
                    report.blobs_purged += 1
                else:
                    report.blobs_still_pending += 1

        # 5. «потерянные» ссылки: данные точно наши и нигде не учтены
        for orphan in repo.list_orphans(self.db.connection(), with_reference=True, limit=limit):
            if orphan.delete_attempts >= core.delete_max_attempts:
                continue
            try:
                core.driver.delete(orphan.backend_reference)  # type: ignore[arg-type]
            except DeleteRejected as exc:
                self._orphan_failed(orphan.id, str(exc), exhausted=True)
            except Exception as exc:  # noqa: BLE001
                self._orphan_failed(orphan.id, str(exc))
            else:
                with self.db.transaction() as conn:
                    repo.delete_orphan(conn, orphan.id)
                report.orphans_deleted += 1

        # 6. диагностика: только отчёт, ничего не удаляется
        conn = self.db.connection()
        for blob in repo.list_unreferenced_ready_blobs(conn):
            report.anomalies.append(f"готовый blob {blob.id} без ссылающихся объектов")
        for blob in repo.list_inconsistent_ready_blobs(conn):
            report.anomalies.append(f"blob {blob.id}: метаданные чанков не согласованы с blob")
        for obj in repo.list_ready_objects_with_unready_blob(conn):
            report.anomalies.append(f"объект {obj.id} ready, но его содержимое не готово")
        report.suspected_orphans = repo.count_orphans(conn, with_reference=False)
        report.orphans_pending = repo.count_orphans(conn, with_reference=True)
        report.chunks_delete_rejected = repo.count_chunks_by_status(conn, "delete_rejected")
        report.chunks_delete_exhausted = repo.count_exhausted_failed_chunks(conn, core.delete_max_attempts)
        if report.needs_attention:
            logger.warning("Storage reconcile: требуется внимание: %s", report.summary())
        return report

    def _orphan_failed(self, orphan_id: str, error: str, *, exhausted: bool = False) -> None:
        with self.db.transaction() as conn:
            repo.record_orphan_delete_failure(conn, orphan_id, error)
            if exhausted:  # отказ backend не лечится повтором
                conn.execute("UPDATE storage_orphans SET delete_attempts = ? WHERE id = ?",
                             (self.core.delete_max_attempts, orphan_id))
