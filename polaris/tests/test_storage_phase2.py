"""Расширения Storage Core для S3 API: списки, buckets, диапазоны, сборка объекта, multipart."""
from __future__ import annotations

import hashlib
import os
import unittest

from polaris.storage.errors import (
    BucketExists, BucketNotEmpty, BucketNotFound, InvalidName, InvalidRange, MultipartError, ObjectNotFound,
)
from polaris.storage.recovery import Reconciler
from tests.storage_helpers import StorageTestCase, sha


class ListingTests(StorageTestCase):
    def put(self, *keys):
        for k in keys:
            self.core.put_object("files", k, k.encode())

    def keys(self, **kw):
        return [o.object_key for o in self.core.list_objects("files", **kw)]

    def test_sorted_by_utf8_bytes_and_limit(self):
        self.put("b", "a", "ä", "z", "a/b", "A")
        self.assertEqual(self.keys(), ["A", "a", "a/b", "b", "z", "ä"])
        self.assertEqual(self.keys(limit=2), ["A", "a"])

    def test_prefix_is_exact_range_not_pattern(self):
        self.put("a%b", "a_b", "a/1", "a/2", "ab", "a")
        self.assertEqual(self.keys(prefix="a/"), ["a/1", "a/2"])
        self.assertEqual(self.keys(prefix="a%"), ["a%b"])
        self.assertEqual(self.keys(prefix="a_"), ["a_b"])

    def test_from_key_is_inclusive_and_successor_is_nul(self):
        self.put("a", "b", "c")
        self.assertEqual(self.keys(from_key="b"), ["b", "c"])
        self.assertEqual(self.keys(from_key="b\0"), ["c"])

    def test_only_ready_objects_listed(self):
        self.put("ok")
        self.core.create_object("files", "pending")
        s = self.core.begin_upload(self.core.create_object("files", "up").id)
        self.assertEqual(self.keys(), ["ok"])
        s.abort()

    def test_unknown_bucket(self):
        with self.assertRaises(BucketNotFound):
            self.core.list_objects("nope")

    def test_non_ascii_prefix_with_max_code_point(self):
        self.put("\U0010ffff", "\U0010ffffx", "a")
        self.assertEqual(self.keys(prefix="\U0010ffff"), ["\U0010ffff", "\U0010ffffx"])


class BucketTests(StorageTestCase):
    def test_create_list_delete(self):
        self.core.create_bucket("alpha")
        with self.assertRaises(BucketExists):
            self.core.create_bucket("alpha")
        self.assertEqual([b.name for b in self.core.list_buckets()], ["alpha", "files"])
        self.core.delete_bucket("alpha")
        with self.assertRaises(BucketNotFound):
            self.core.delete_bucket("alpha")

    def test_non_empty_bucket_cannot_be_deleted_but_dead_rows_do_not_block(self):
        self.core.put_object("files", "k", b"x")
        with self.assertRaises(BucketNotEmpty):
            self.core.delete_bucket("files")
        obj = self.core.get_metadata_by_key("files", "k")
        self.core.delete_object(obj.id)
        self.core.begin_upload(self.core.create_object("files", "dead").id).abort()  # failed-запись
        self.core.create_object("files", "pending")
        self.core.delete_bucket("files")
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM storage_objects")[0]["n"], 0)

    def test_internal_bucket_hidden_and_reserved(self):
        self.core.multipart.create("files", "k")
        self.core.ensure_bucket("polaris-internal.multipart")
        self.assertNotIn("polaris-internal.multipart", [b.name for b in self.core.list_buckets()])
        self.assertIn("polaris-internal.multipart", [b.name for b in self.core.list_buckets(include_internal=True)])
        with self.assertRaises(InvalidName):
            self.core.create_bucket("polaris-internal.x")


class RangeTests(StorageTestCase):
    def setUp(self):
        super().setUp()
        self.data = os.urandom(95)  # чанки 10 x 9 + 5
        self.obj = self.core.put_object("files", "r", self.data)

    def rng(self, a, b):
        return b"".join(self.core.stream(self.obj.id, a, b))

    def test_ranges_across_chunk_boundaries(self):
        for a, b in [(0, 0), (0, 9), (9, 10), (5, 34), (90, 94), (94, 94), (0, 94), (10, 19), (33, 33)]:
            self.assertEqual(self.rng(a, b), self.data[a:b + 1], (a, b))

    def test_only_overlapping_chunks_are_downloaded(self):
        downloads = []
        original = self.driver.download
        self.driver.download = lambda ref: (downloads.append(ref), original(ref))[1]
        self.rng(25, 31)  # чанки 2 и 3
        self.assertEqual(len(downloads), 2)

    def test_invalid_ranges(self):
        for a, b in [(5, 4), (-1, 3), (0, 95), (95, 95), (None, 5), (5, None)]:
            with self.assertRaises(InvalidRange):
                self.core.stream(self.obj.id, a, b)

    def test_corrupted_chunk_in_range_is_detected(self):
        ref = self.q("SELECT backend_reference FROM storage_chunks WHERE chunk_index=2")[0][0]
        self.driver.blobs[ref] = b"X" * 10
        with self.assertRaises(Exception):
            self.rng(20, 25)
        self.assertEqual(self.rng(0, 19), self.data[:20])  # соседние чанки читаются

    def test_range_does_not_need_whole_object_hash(self):
        self.assertEqual(b"".join(self.core.stream(self.obj.id)), self.data)


class ComposeAndMultipartTests(StorageTestCase):
    mp = property(lambda self: self.core.multipart)

    def setUp(self):
        super().setUp()
        self.mp.min_part_size = 20

    def upload_part(self, upload, number, data):
        with self.mp.begin_part(upload.id, number) as s:
            s.write(data)
            return s.complete()

    def test_full_cycle_without_backend_traffic_on_complete(self):
        up = self.mp.create("files", "big/file.bin", mime_type="text/x-test", metadata={"s3": {"meta": {"a": "b"}}})
        p1, p2, p3 = os.urandom(25), os.urandom(33), os.urandom(7)
        parts = [self.upload_part(up, 1, p1), self.upload_part(up, 3, p3), self.upload_part(up, 2, p2)]
        self.assertEqual([p.part_number for p in self.mp.list_parts(up.id)[0]], [1, 2, 3])
        with self.assertRaises(ObjectNotFound):
            self.core.get_metadata_by_key("files", "big/file.bin")  # до Complete не виден
        uploads, deletes = self.driver.upload_count, len(self.driver.deleted)
        etags = {1: parts[0].etag, 2: parts[2].etag, 3: parts[1].etag}
        obj = self.mp.complete(up.id, [(1, f'"{etags[1]}"'), (2, etags[2]), (3, etags[3])])
        self.assertEqual((self.driver.upload_count, len(self.driver.deleted)), (uploads, deletes))
        data = p1 + p2 + p3
        self.assertEqual(b"".join(self.core.stream(obj.id)), data)
        self.assertEqual(obj.size_bytes, len(data))
        self.assertEqual(obj.mime_type, "text/x-test")
        self.assertEqual(obj.metadata, {"s3": {"meta": {"a": "b"}}})
        md5s = b"".join(hashlib.md5(p).digest() for p in (p1, p2, p3))
        self.assertEqual(obj.etag, hashlib.md5(md5s).hexdigest() + "-3")
        self.assertEqual(b"".join(self.core.stream(obj.id, 20, 60)), data[20:61])  # диапазон поверх составных чанков
        self.assertTrue(self.core.verify_object(obj.id, deep=True).ok)
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM storage_multipart_uploads")[0]["n"], 0)
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM storage_objects WHERE bucket_id != (SELECT id FROM storage_buckets WHERE name='files')")[0]["n"], 0)
        with self.assertRaises(MultipartError):
            self.mp.get(up.id)

    def test_composite_content_is_never_deduplicated_or_found_by_hash(self):
        up = self.mp.create("files", "k")
        part = self.upload_part(up, 1, b"z" * 30)
        obj = self.mp.complete(up.id, [(1, part.etag)])
        plain = self.core.put_object("files", "plain", b"z" * 30)
        self.assertNotEqual(obj.blob_id, plain.blob_id)
        self.assertEqual(self.core.find_blob("files", sha(b"z" * 30)).id, plain.blob_id)

    def test_composite_deleted_physically_and_overwrite_replaces(self):
        for payload in (b"1" * 21, b"2" * 22):
            up = self.mp.create("files", "k")
            self.mp.complete(up.id, [(1, self.upload_part(up, 1, payload).etag)])
        obj = self.core.get_metadata_by_key("files", "k")
        self.assertEqual(b"".join(self.core.stream(obj.id)), b"2" * 22)
        self.assertEqual(len(self.driver.blobs), 3)  # 22 байт = 3 чанка; чанки первого объекта удалены
        self.assertTrue(self.core.delete_object(obj.id))
        self.assertEqual(self.driver.blobs, {})

    def test_reuploading_part_replaces_old_data(self):
        up = self.mp.create("files", "k")
        self.upload_part(up, 1, b"a" * 25)
        second = self.upload_part(up, 1, b"b" * 25)
        self.assertEqual(len(self.mp.list_parts(up.id)[0]), 1)
        self.assertEqual(len(self.driver.blobs), 3)  # старая часть удалена физически
        obj = self.mp.complete(up.id, [(1, second.etag)])
        self.assertEqual(b"".join(self.core.stream(obj.id)), b"b" * 25)

    def test_identical_parts_do_not_share_chunks(self):
        up = self.mp.create("files", "k")
        a, b = self.upload_part(up, 1, b"s" * 25), self.upload_part(up, 2, b"s" * 25)
        self.assertEqual(self.driver.upload_count, 6)  # дедупликации нет: чанки принадлежат одной части
        obj = self.mp.complete(up.id, [(1, a.etag), (2, b.etag)])
        self.assertEqual(b"".join(self.core.stream(obj.id)), b"s" * 50)
        self.core.delete_object(obj.id)
        self.assertEqual(self.driver.blobs, {})

    def test_validation_errors_leave_upload_intact(self):
        up = self.mp.create("files", "k")
        p1, p2 = self.upload_part(up, 1, b"a" * 25), self.upload_part(up, 2, b"b" * 5)
        cases = [([(2, p2.etag), (1, p1.etag)], "InvalidPartOrder"), ([(1, "deadbeef")], "InvalidPart"),
                 ([(9, p1.etag)], "InvalidPart"), ([], "MalformedXML"),
                 ([(2, p2.etag), (3, p2.etag)], "InvalidPart")]
        for requested, code in cases:
            with self.assertRaises(MultipartError) as cm:
                self.mp.complete(up.id, requested)
            self.assertEqual(cm.exception.code, code)
        self.mp.min_part_size = 100
        with self.assertRaises(MultipartError) as cm:
            self.mp.complete(up.id, [(1, p1.etag), (2, p2.etag)])
        self.assertEqual(cm.exception.code, "EntityTooSmall")
        self.mp.min_part_size = 20
        self.assertEqual(len(self.mp.list_parts(up.id)[0]), 2)  # ничего не потеряно
        self.assertEqual(self.mp.complete(up.id, [(1, p1.etag), (2, p2.etag)]).size_bytes, 30)

    def test_last_part_may_be_small_and_unlisted_parts_are_discarded(self):
        up = self.mp.create("files", "k")
        p1, p2 = self.upload_part(up, 1, b"a" * 25), self.upload_part(up, 2, b"b" * 25)
        obj = self.mp.complete(up.id, [(1, p1.etag)])
        self.assertEqual(obj.size_bytes, 25)
        self.assertEqual(len(self.driver.blobs), 3)  # чанки неиспользованной части 2 удалены

    def test_abort_deletes_everything(self):
        up = self.mp.create("files", "k")
        self.upload_part(up, 1, b"a" * 25)
        self.upload_part(up, 2, b"b" * 25)
        self.mp.abort(up.id)
        self.assertEqual(self.driver.blobs, {})
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM storage_multipart_uploads")[0]["n"], 0)
        with self.assertRaises(MultipartError):
            self.mp.begin_part(up.id, 1)
        with self.assertRaises(MultipartError):
            self.mp.abort(up.id)

    def test_interrupted_abort_is_finished_by_reconcile(self):
        up = self.mp.create("files", "k")
        self.upload_part(up, 1, b"a" * 25)
        from polaris.storage import repository as repo
        with self.db.transaction() as c:
            repo.set_upload_status(c, up.id, "aborting")  # упали сразу после пометки
        with self.assertRaises(MultipartError):
            self.mp.begin_part(up.id, 2)
        report = Reconciler(self.core).run(stale_seconds=3600)
        self.assertEqual(report.multipart_aborts_finished, 1)
        self.assertEqual(self.driver.blobs, {})

    def test_part_finished_after_abort_is_collected_as_orphan(self):
        up = self.mp.create("files", "k")
        session = self.mp.begin_part(up.id, 1)
        session.write(b"x" * 25)
        self.mp.abort(up.id)  # Abort прошёл, пока часть ещё загружалась
        session.complete()
        self.assertTrue(self.driver.blobs)
        report = Reconciler(self.core).run(stale_seconds=3600)
        self.assertEqual(report.orphan_parts_removed, 1)
        self.assertEqual(self.driver.blobs, {})

    def test_state_survives_restart(self):
        up = self.mp.create("files", "k")
        part = self.upload_part(up, 1, b"a" * 25)
        other = self.make_core(self.driver)  # «перезапуск»: новый экземпляр ядра на той же БД
        other.multipart.min_part_size = 20
        self.assertEqual([p.part_number for p in other.multipart.list_parts(up.id)[0]], [1])
        obj = other.multipart.complete(up.id, [(1, part.etag)])
        self.assertEqual(b"".join(other.stream(obj.id)), b"a" * 25)

    def test_invalid_part_numbers_and_unknown_upload(self):
        up = self.mp.create("files", "k")
        for n in (0, 10001, -1):
            with self.assertRaises(MultipartError):
                self.mp.begin_part(up.id, n)
        for bad in ("nope", "0" * 32, ""):
            with self.assertRaises(MultipartError):
                self.mp.get(bad)
        with self.assertRaises(MultipartError):
            self.mp.get(up.id, bucket="files", key="other")

    def test_list_parts_pagination_and_list_uploads(self):
        up = self.mp.create("files", "a/k")
        up2 = self.mp.create("files", "b/k")
        for n in (1, 2, 3):
            self.upload_part(up, n, bytes([n]) * 3)
        page, more = self.mp.list_parts(up.id, limit=2)
        self.assertEqual(([p.part_number for p in page], more), ([1, 2], True))
        page, more = self.mp.list_parts(up.id, after_part=2, limit=2)
        self.assertEqual(([p.part_number for p in page], more), ([3], False))
        uploads, more = self.mp.list_uploads("files")
        self.assertEqual([u.id for u in uploads], [up.id, up2.id])
        self.assertEqual([u.id for u in self.mp.list_uploads("files", prefix="b/")[0]], [up2.id])
        after, _ = self.mp.list_uploads("files", after=(uploads[0].object_key, uploads[0].id))
        self.assertEqual([u.id for u in after], [up2.id])

    def test_bucket_with_open_upload_cannot_be_deleted(self):
        self.core.create_bucket("tmpbucket")
        up = self.mp.create("tmpbucket", "k")
        with self.assertRaises(BucketNotEmpty):
            self.core.delete_bucket("tmpbucket")
        self.mp.abort(up.id)
        self.core.delete_bucket("tmpbucket")

    def test_expired_uploads_are_aborted(self):
        up = self.mp.create("files", "k")
        self.upload_part(up, 1, b"a" * 25)
        self.assertEqual(self.mp.abort_expired(3600), 0)
        self.assertEqual(self.mp.abort_expired(-1), 1)
        self.assertEqual(self.driver.blobs, {})

    def test_failed_backend_during_part_leaves_no_visible_state(self):
        from polaris.storage.backends.base import TransientBackendError
        up = self.mp.create("files", "k")

        def boom(data, ctx):
            raise TransientBackendError("down")
        self.driver.upload_hook = boom
        with self.assertRaises(TransientBackendError):
            self.upload_part(up, 1, b"a" * 25)
        self.driver.upload_hook = None
        self.assertEqual(self.mp.list_parts(up.id)[0], [])
        self.assertEqual(self.upload_part(up, 1, b"a" * 25).size_bytes, 25)  # повтор той же части работает


if __name__ == "__main__":
    unittest.main()
