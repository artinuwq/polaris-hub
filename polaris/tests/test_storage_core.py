"""Тесты Storage Core на in-memory драйвере: жизненный цикл, чанки, дедупликация, целостность."""
from __future__ import annotations

import io
import os
import threading
import unittest

from polaris.storage.errors import (
    BucketNotFound, ChecksumMismatch, IntegrityError, InvalidName, ObjectConflict, ObjectNotFound,
    ObjectNotReady, StorageBusy,
)
from polaris.storage.backends.base import TransientBackendError
from tests.storage_helpers import StorageTestCase, sha

DATA = bytes(range(256)) * 3  # 768 байт


class LifecycleTests(StorageTestCase):
    def test_put_and_get_roundtrip(self):
        obj = self.core.put_object("files", "docs/a.txt", b"hello world", mime_type=None)
        self.assertEqual(obj.status, "ready")
        self.assertEqual((obj.size_bytes, obj.checksum_sha256), (11, sha(b"hello world")))
        self.assertEqual(obj.mime_type, "text/plain")
        self.assertEqual(obj.filename, "a.txt")
        self.assertEqual(obj.backend, "memory")
        self.assertEqual(len(obj.etag), 32)
        self.assertEqual(b"".join(self.core.stream(obj.id)), b"hello world")
        self.assertEqual(self.core.get_metadata_by_key("files", "docs/a.txt").id, obj.id)

    def test_multiple_chunks_with_different_sizes(self):
        # 25 байт при чанке 10 -> 10 + 10 + 5; запись неровными порциями
        data = os.urandom(25)
        obj = self.core.create_object("files", "k")
        with self.core.begin_upload(obj.id) as s:
            for piece in (data[:3], data[3:4], data[4:19], data[19:]):
                s.write(piece)
            s.complete()
        sizes = [r["size_bytes"] for r in self.q("SELECT size_bytes FROM storage_chunks ORDER BY chunk_index")]
        self.assertEqual(sizes, [10, 10, 5])
        self.assertEqual(b"".join(self.core.stream(obj.id)), data)

    def test_chunk_sizes_are_stored_per_chunk(self):
        core = self.make_core(self.driver, chunk_size=7)
        core.put_object("files", "k7", DATA[:20])
        self.assertEqual([r["size_bytes"] for r in self.q(
            "SELECT size_bytes FROM storage_chunks ORDER BY chunk_index")], [7, 7, 6])

    def test_empty_object(self):
        obj = self.core.put_object("files", "empty", b"")
        self.assertEqual((obj.size_bytes, obj.checksum_sha256), (0, sha(b"")))
        self.assertEqual(b"".join(self.core.stream(obj.id)), b"")
        self.assertEqual(self.driver.upload_count, 0)
        self.assertTrue(self.core.delete_object(obj.id))

    def test_file_like_and_iterator_sources(self):
        a = self.core.put_object("files", "f", io.BytesIO(DATA))
        b = self.core.put_object("files", "g", iter([DATA[:100], DATA[100:]]))
        self.assertEqual(a.checksum_sha256, b.checksum_sha256)

    def test_not_ready_objects_are_never_served(self):
        obj = self.core.create_object("files", "p")
        with self.assertRaises(ObjectNotReady):
            self.core.get_metadata(obj.id)
        with self.assertRaises(ObjectNotReady):
            self.core.stream(obj.id)
        s = self.core.begin_upload(obj.id)
        s.write(b"x" * 15)
        with self.assertRaises(ObjectNotReady):
            self.core.get_metadata(obj.id)
        with self.assertRaises(ObjectNotFound):
            self.core.get_metadata_by_key("files", "p")
        s.abort()
        self.assertEqual(self.core.get_object(obj.id).status, "failed")
        with self.assertRaises(ObjectNotReady):
            self.core.get_metadata(obj.id)

    def test_validation(self):
        with self.assertRaises(BucketNotFound):
            self.core.create_object("nope-bucket", "k")
        with self.assertRaises(InvalidName):
            self.core.ensure_bucket("A")
        with self.assertRaises(InvalidName):
            self.core.create_object("files", "")
        with self.assertRaises(ObjectNotFound):
            self.core.get_object("00000000-0000-0000-0000-000000000000")

    def test_failed_object_can_be_retried(self):
        obj = self.core.create_object("files", "r")
        self.core.begin_upload(obj.id).abort()
        self.core.begin_upload(obj.id).abort()  # failed -> uploading допустимо
        with self.core.begin_upload(obj.id) as s:
            s.write(b"data")
            self.assertEqual(s.complete().status, "ready")

    def test_overwrite_key_replaces_previous_and_frees_old_content(self):
        old = self.core.put_object("files", "same", b"old content!!")
        new = self.core.put_object("files", "same", b"new content")
        self.assertEqual(self.core.get_metadata_by_key("files", "same").id, new.id)
        with self.assertRaises(ObjectNotFound):
            self.core.get_object(old.id)
        self.assertEqual(len(self.driver.blobs), 2)  # 11 байт -> 2 чанка у нового; старые удалены
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM storage_blobs")[0]["n"], 1)

    def test_delete_object(self):
        obj = self.core.put_object("files", "d", DATA)
        self.assertTrue(self.core.delete_object(obj.id))
        self.assertEqual(self.driver.blobs, {})
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM storage_chunks")[0]["n"], 0)
        with self.assertRaises(ObjectNotFound):
            self.core.delete_object(obj.id)

    def test_cannot_delete_uploading_object(self):
        obj = self.core.create_object("files", "u")
        with self.core.begin_upload(obj.id):
            with self.assertRaises(ObjectConflict):
                self.core.delete_object(obj.id)

    def test_upload_limit(self):
        core = self.make_core(self.driver, max_concurrent_uploads=1)
        a, b = core.create_object("files", "a"), core.create_object("files", "b")
        s = core.begin_upload(a.id)
        with self.assertRaises(StorageBusy):
            core.begin_upload(b.id)
        s.abort()
        core.begin_upload(b.id).abort()  # слот освобождён

    def test_download_limit(self):
        core = self.make_core(self.driver, max_concurrent_downloads=1)
        obj = core.put_object("files", "x", DATA)
        first = core.stream(obj.id)
        next(first)
        with self.assertRaises(StorageBusy):
            next(core.stream(obj.id))
        first.close()
        self.assertEqual(b"".join(core.stream(obj.id)), DATA)


class ChecksumTests(StorageTestCase):
    def test_expected_sha_mismatch_fails_and_cleans_up(self):
        obj = self.core.create_object("files", "bad")
        with self.assertRaises(ChecksumMismatch):
            with self.core.begin_upload(obj.id, expected_sha256=sha(b"other")) as s:
                s.write(b"x" * 35)
                s.complete()
        self.assertEqual(self.core.get_object(obj.id).status, "failed")
        self.assertEqual(self.driver.blobs, {})
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM storage_blobs")[0]["n"], 0)

    def test_expected_size_mismatch(self):
        with self.assertRaises(ChecksumMismatch):
            self.core.put_object("files", "s", b"12345", expected_size=6)

    def test_expected_sha_ok(self):
        obj = self.core.put_object("files", "ok", DATA, expected_sha256=sha(DATA).upper())
        self.assertEqual(obj.checksum_sha256, sha(DATA))

    def test_stream_detects_corrupted_chunk_before_delivering_it(self):
        obj = self.core.put_object("files", "c", b"A" * 10 + b"B" * 10 + b"C" * 5)
        ref = self.q("SELECT backend_reference FROM storage_chunks WHERE chunk_index=1")[0][0]
        self.driver.blobs[ref] = b"X" * 10
        got = []
        with self.assertRaises(IntegrityError):
            for piece in self.core.stream(obj.id):
                got.append(piece)
        self.assertEqual(got, [b"A" * 10])  # испорченный чанк потребителю не выдан

    def test_stream_detects_wrong_size(self):
        obj = self.core.put_object("files", "w", b"A" * 12)
        ref = self.q("SELECT backend_reference FROM storage_chunks WHERE chunk_index=0")[0][0]
        self.driver.blobs[ref] = b"A" * 9
        with self.assertRaises(IntegrityError):
            list(self.core.stream(obj.id))

    def test_verify_object_shallow_and_deep(self):
        obj = self.core.put_object("files", "v", b"A" * 25)
        self.assertTrue(self.core.verify_object(obj.id).ok)
        ref = self.q("SELECT backend_reference FROM storage_chunks WHERE chunk_index=2")[0][0]
        self.driver.blobs[ref] = b"Z" * 5  # размер тот же, содержимое другое
        self.assertTrue(self.core.verify_object(obj.id).ok)  # поверхностная проверка этого не видит
        deep = self.core.verify_object(obj.id, deep=True)
        self.assertFalse(deep.ok)
        del self.driver.blobs[ref]
        shallow = self.core.verify_object(obj.id)
        self.assertFalse(shallow.ok)
        self.assertIn("чанк 2", shallow.problems[0])

    def test_backend_failure_midway_fails_object_and_removes_stored_chunks(self):
        calls = []

        def hook(data, ctx):
            calls.append(ctx.chunk_index)
            if ctx.chunk_index == 2:
                raise TransientBackendError("telegram недоступен")

        self.driver.upload_hook = hook
        obj = self.core.create_object("files", "m")
        with self.assertRaises(TransientBackendError):
            with self.core.begin_upload(obj.id) as s:
                s.write(b"x" * 100)
        self.assertEqual(self.core.get_object(obj.id).status, "failed")
        self.assertEqual(self.driver.blobs, {})  # уже сохранённые чанки 0 и 1 убраны
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM storage_blobs")[0]["n"], 0)

    def test_uncertain_upload_is_registered_as_suspected_orphan(self):
        def hook(data, ctx):
            raise TransientBackendError("таймаут", uncertain_attempts=2)

        self.driver.upload_hook = hook
        with self.assertRaises(TransientBackendError):
            self.core.put_object("files", "u", b"x" * 5)
        orphans = self.q("SELECT kind, backend_reference, marker FROM storage_orphans")
        self.assertEqual(len(orphans), 2)
        self.assertTrue(all(o["kind"] == "uncertain_upload" and o["backend_reference"] is None for o in orphans))
        self.assertIn("polaris-storage:v1", orphans[0]["marker"])


class DedupTests(StorageTestCase):
    def test_posthoc_dedup_shares_content_and_drops_duplicate(self):
        a = self.core.put_object("files", "a", DATA)
        stored = len(self.driver.blobs)
        b = self.core.put_object("files", "b", DATA)  # хеш заранее неизвестен: загрузили и свернули дубликат
        self.assertNotEqual(a.id, b.id)
        self.assertEqual(a.blob_id, b.blob_id)
        self.assertEqual(len(self.driver.blobs), stored)
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM storage_blobs")[0]["n"], 1)

    def test_known_hash_skips_backend_upload_entirely(self):
        self.core.put_object("files", "a", DATA)
        uploads = self.driver.upload_count
        b = self.core.put_object("files", "b", DATA, expected_sha256=sha(DATA))
        self.assertEqual(self.driver.upload_count, uploads)
        self.assertEqual(b.status, "ready")
        self.assertEqual(b"".join(self.core.stream(b.id)), DATA)

    def test_known_hash_with_wrong_data_is_rejected_and_keeps_original(self):
        a = self.core.put_object("files", "a", DATA)
        obj = self.core.create_object("files", "evil")
        with self.assertRaises(ChecksumMismatch):  # заявил чужой хеш, не имея содержимого
            with self.core.begin_upload(obj.id, expected_sha256=sha(DATA)) as s:
                s.write(b"i do not have the file")
                s.complete()
        self.assertEqual(self.core.get_object(obj.id).status, "failed")
        self.assertEqual(self.core.get_object(obj.id).blob_id, None)
        self.assertEqual(b"".join(self.core.stream(a.id)), DATA)

    def test_shared_content_survives_until_last_reference(self):
        a = self.core.put_object("files", "a", DATA)
        b = self.core.put_object("files", "b", DATA)
        before = len(self.driver.blobs)  # дубликат b уже свёрнут при завершении загрузки
        self.core.delete_object(a.id)
        self.assertEqual(b"".join(self.core.stream(b.id)), DATA)
        self.assertEqual(len(self.driver.blobs), before)  # общее содержимое не тронуто
        self.core.delete_object(b.id)
        self.assertEqual(self.driver.blobs, {})

    def test_dedup_does_not_cross_scope_boundaries(self):
        self.core.ensure_bucket("other")  # по умолчанию scope = имя bucket
        a = self.core.put_object("files", "a", DATA)
        uploads = self.driver.upload_count
        b = self.core.put_object("other", "a", DATA, expected_sha256=sha(DATA))
        self.assertNotEqual(a.blob_id, b.blob_id)
        self.assertGreater(self.driver.upload_count, uploads)  # из другого scope ничего не переиспользовано
        self.assertIsNone(self.core.find_blob("other", sha(b"nothing")))
        self.assertEqual([o.id for o in self.core.find_objects_by_sha256("other", sha(DATA))], [b.id])

    def test_shared_scope_between_buckets(self):
        self.core.ensure_bucket("one", dedup_scope="team")
        self.core.ensure_bucket("two", dedup_scope="team")
        a = self.core.put_object("one", "x", DATA)
        b = self.core.put_object("two", "y", DATA, expected_sha256=sha(DATA))
        self.assertEqual(a.blob_id, b.blob_id)
        self.assertEqual(self.core.find_blob("two", sha(DATA)).id, a.blob_id)

    def test_find_by_sha256(self):
        a = self.core.put_object("files", "a", DATA)
        self.assertEqual(self.core.find_blob("files", sha(DATA)).id, a.blob_id)
        self.assertEqual([o.id for o in self.core.find_objects_by_sha256("files", sha(DATA))], [a.id])
        self.assertEqual(self.core.find_objects_by_sha256("files", sha(b"zzz")), [])

    def test_concurrent_identical_uploads_keep_single_physical_copy(self):
        barrier = threading.Barrier(4)
        errors = []

        def work(i):
            try:
                core = self.make_core(self.driver, max_concurrent_uploads=4)
                barrier.wait()
                core.put_object("files", f"k{i}", DATA)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        ts = [threading.Thread(target=work, args=(i,)) for i in range(4)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(errors, [])
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM storage_blobs WHERE status='ready'")[0]["n"], 1)
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM storage_blobs")[0]["n"], 1)
        self.assertEqual(len(self.driver.blobs), 77)  # 768 байт / 10 = 77 чанков, дубликаты удалены
        ids = [r["id"] for r in self.q("SELECT id FROM storage_objects")]
        self.assertEqual(len(ids), 4)
        for oid in ids:
            self.assertEqual(b"".join(self.core.stream(oid)), DATA)

    def test_concurrent_writes_to_same_key_leave_exactly_one_ready(self):
        errors = []

        def work(i):
            try:
                self.make_core(self.driver, max_concurrent_uploads=4).put_object("files", "k", f"v{i}".encode() * 4)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        ts = [threading.Thread(target=work, args=(i,)) for i in range(5)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(errors, [])
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM storage_objects WHERE status='ready'")[0]["n"], 1)
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM storage_objects")[0]["n"], 1)


if __name__ == "__main__":
    unittest.main()
