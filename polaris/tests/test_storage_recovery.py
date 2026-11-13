"""Тесты восстановления: сбои на каждом шаге жизненного цикла и безопасная reconciliation."""
from __future__ import annotations

import unittest
from unittest import mock

from polaris.storage import repository as repo
from polaris.storage.backends.base import DeleteRejected, TransientBackendError
from polaris.storage.errors import StorageError
from polaris.storage.recovery import Reconciler
from tests.storage_helpers import StorageTestCase, sha

DATA = b"0123456789" * 3 + b"abcde"  # 35 байт -> 4 чанка


class RecoveryTests(StorageTestCase):
    def reconcile(self, **kw):
        return Reconciler(self.core).run(stale_seconds=kw.pop("stale_seconds", 0), **kw)

    def crash_upload(self, key="crashed", payload=DATA[:25]):
        """Имитация падения процесса посреди загрузки: сеанс брошен, в памяти процесса о нём ничего нет."""
        obj = self.core.create_object("files", key)
        session = self.core.begin_upload(obj.id)
        session.write(payload)
        with self.core._active_lock:
            self.core._active_objects.clear()
            self.core._active_blobs.clear()
        return obj, session

    def test_interrupted_upload_is_failed_and_stored_chunks_removed(self):
        obj, _ = self.crash_upload()
        self.assertEqual(len(self.driver.blobs), 2)  # чанки 0 и 1 в backend, хвост в памяти пропал
        report = self.reconcile()
        self.assertEqual(report.abandoned_uploads, 1)
        self.assertEqual(self.core.get_object(obj.id).status, "failed")
        self.assertEqual(self.driver.blobs, {})
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM storage_blobs")[0]["n"], 0)

    def test_recent_or_active_uploads_are_left_alone(self):
        obj = self.core.create_object("files", "live")
        session = self.core.begin_upload(obj.id)
        session.write(DATA)
        self.assertEqual(self.reconcile().abandoned_uploads, 0)  # активна в этом процессе
        session.complete()
        obj2, _ = self.crash_upload("recent")
        self.assertEqual(Reconciler(self.core).run(stale_seconds=3600).abandoned_uploads, 0)  # ещё не просрочена
        self.assertEqual(self.core.get_object(obj2.id).status, "uploading")

    def test_pending_chunk_without_reference_becomes_suspected_orphan_not_deleted(self):
        obj, _ = self.crash_upload()
        blob_id = self.core.get_object(obj.id).blob_id
        with self.db.transaction() as c:  # сбой между отправкой и записью в БД: чанк так и остался pending
            repo.insert_pending_chunk(c, blob_id, 2, 5, sha(b"abcde"))
        self.reconcile()
        orphans = self.q("SELECT kind, backend_reference, marker, chunk_index FROM storage_orphans")
        self.assertEqual(len(orphans), 1)
        self.assertEqual((orphans[0]["kind"], orphans[0]["backend_reference"], orphans[0]["chunk_index"]),
                         ("interrupted_upload", None, 2))
        self.assertIn(blob_id, orphans[0]["marker"])
        again = self.reconcile()  # повторный запуск ничего не удаляет и не плодит записи
        self.assertEqual(len(self.q("SELECT * FROM storage_orphans")), 1)
        self.assertTrue(again.needs_attention)
        self.assertEqual(again.suspected_orphans, 1)

    def test_db_write_failure_after_successful_send_removes_backend_data(self):
        with mock.patch.object(repo, "mark_chunk_stored", side_effect=RuntimeError("disk I/O error")):
            with self.assertRaises(StorageError):
                self.core.put_object("files", "x", b"abcdefghij")
        self.assertEqual(self.driver.blobs, {})  # данные точно наши — удалены сразу
        self.assertEqual(self.core.get_object(self.q("SELECT id FROM storage_objects")[0]["id"]).status, "failed")

    def test_db_write_failure_and_delete_failure_keeps_reference_for_retry(self):
        self.driver.delete_errors = [TransientBackendError("down")]
        with mock.patch.object(repo, "mark_chunk_stored", side_effect=RuntimeError("disk I/O error")):
            with self.assertRaises(StorageError):
                self.core.put_object("files", "x", b"abcdefghij")
        orphan = self.q("SELECT kind, backend_reference FROM storage_orphans")[0]
        self.assertEqual(orphan["kind"], "db_write_lost")
        self.assertIsNotNone(orphan["backend_reference"])
        self.assertEqual(len(self.driver.blobs), 1)
        report = self.reconcile()  # backend снова доступен
        self.assertEqual(report.orphans_deleted, 1)
        self.assertEqual(self.driver.blobs, {})
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM storage_orphans")[0]["n"], 0)

    def test_partial_deletion_is_recorded_and_completed_later(self):
        obj = self.core.put_object("files", "p", DATA)
        self.driver.delete_errors = [TransientBackendError("t1")] + [TransientBackendError("t2")]
        self.assertFalse(self.core.delete_object(obj.id))  # два чанка не удалились
        left = self.q("SELECT status, delete_attempts, last_error FROM storage_chunks")
        self.assertEqual(len(left), 2)
        self.assertTrue(all(r["status"] == "delete_failed" and r["delete_attempts"] == 1 for r in left))
        self.assertEqual(self.q("SELECT status FROM storage_blobs")[0]["status"], "deleting")
        with self.assertRaises(Exception):
            self.core.get_object(obj.id)  # логически объект уже удалён
        report = self.reconcile()
        self.assertEqual(report.blobs_purged, 1)
        self.assertEqual(self.driver.blobs, {})
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM storage_blobs")[0]["n"], 0)

    def test_rejected_delete_is_terminal_and_reported(self):
        obj = self.core.put_object("files", "r", DATA[:10])
        self.driver.delete_errors = [DeleteRejected("message can't be deleted", status=400)]
        self.assertFalse(self.core.delete_object(obj.id))
        report = self.reconcile()
        self.assertEqual(report.chunks_delete_rejected, 1)
        self.assertTrue(report.needs_attention)
        self.assertEqual(report.blobs_still_pending, 1)
        self.assertEqual(self.q("SELECT delete_attempts FROM storage_chunks")[0][0], 1)  # повторно не пытались
        self.assertEqual(len(self.driver.blobs), 1)

    def test_transient_delete_failures_stop_after_max_attempts(self):
        core = self.make_core(self.driver, delete_max_attempts=2)
        obj = core.put_object("files", "e", DATA[:10])
        self.driver.delete_errors = [TransientBackendError("x")] * 10
        core.delete_object(obj.id)
        for _ in range(3):
            report = Reconciler(core).run(stale_seconds=0)
        self.assertEqual(self.q("SELECT delete_attempts FROM storage_chunks")[0][0], 2)
        self.assertEqual(report.chunks_delete_exhausted, 1)

    def test_object_stuck_in_deleting_is_finished(self):
        obj = self.core.put_object("files", "s", DATA)
        with self.db.transaction() as c:  # упали после пометки, до удаления
            repo.set_object_status(c, obj.id, "deleting")
        report = self.reconcile()
        self.assertEqual(report.finished_object_deletions, 1)
        self.assertEqual(self.driver.blobs, {})

    def test_deleting_one_of_two_references_after_crash_keeps_content(self):
        a = self.core.put_object("files", "a", DATA)
        b = self.core.put_object("files", "b", DATA)
        with self.db.transaction() as c:
            repo.set_object_status(c, a.id, "deleting")
        self.reconcile()
        self.assertEqual(b"".join(self.core.stream(b.id)), DATA)

    def test_verify_only_upload_crash_releases_pinned_blob(self):
        a = self.core.put_object("files", "a", DATA)
        obj = self.core.create_object("files", "dup")
        session = self.core.begin_upload(obj.id, expected_sha256=sha(DATA))
        self.assertTrue(session.verify_only)
        with self.core._active_lock:
            self.core._active_objects.clear()
        self.core.delete_object(a.id)  # blob закреплён незавершённой загрузкой: физически не удаляется
        self.assertTrue(self.driver.blobs)
        self.reconcile()  # загрузка заброшена -> ссылка снята -> blob освобождён
        self.assertEqual(self.core.get_object(obj.id).status, "failed")
        self.assertEqual(self.driver.blobs, {})

    def test_concurrent_reconcile_runs_are_safe(self):
        self.crash_upload()
        obj = self.core.put_object("files", "d", DATA)
        self.core.delete_object(obj.id)
        import threading
        errors = []
        ts = [threading.Thread(target=lambda: errors.extend(self._safe())) for _ in range(4)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(errors, [])
        self.assertEqual(self.driver.blobs, {})

    def _safe(self):
        try:
            self.reconcile()
            return []
        except Exception as exc:  # noqa: BLE001
            return [exc]

    def test_anomalies_are_reported_but_never_auto_deleted(self):
        obj = self.core.put_object("files", "a", DATA)
        with self.db.transaction() as c:
            c.execute("DELETE FROM storage_objects WHERE id=?", (obj.id,))  # blob остался без ссылок
        blobs = len(self.driver.blobs)
        report = self.reconcile()
        self.assertTrue(any("без ссылающихся объектов" in a for a in report.anomalies))
        self.assertEqual(len(self.driver.blobs), blobs)
        with self.db.transaction() as c:
            c.execute("UPDATE storage_chunks SET size_bytes = size_bytes + 1 WHERE chunk_index = 0")
        self.assertTrue(any("не согласованы" in a for a in self.reconcile().anomalies))

    def test_clean_state_reports_nothing(self):
        self.core.put_object("files", "a", DATA)
        report = self.reconcile()
        self.assertFalse(report.needs_attention)
        self.assertEqual(report.summary(), "без изменений")


if __name__ == "__main__":
    unittest.main()
