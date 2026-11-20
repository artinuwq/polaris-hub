"""Файловый менеджер Storage: просмотр по папкам, создание/удаление папок, загрузка файлов."""
from __future__ import annotations

import io
import unittest

from polaris.modules.storage import download_links
from polaris.modules.storage.admin import AdminError
from tests.test_storage_admin import AdminTestCase


class FileManagerTests(AdminTestCase):
    with_s3 = False

    def setUp(self):
        super().setUp()
        for key in ("readme.txt", "docs/a.txt", "docs/sub/b.txt", "docs/sub/c.txt", "docs2/x.txt", "z/y.txt"):
            self.core.put_object("files", key, key.encode())

    def names(self, view):
        return sorted(f["name"] for f in view["folders"]), sorted(o["name"] for o in view["objects"])

    def test_root_shows_top_level_folders_and_files(self):
        view = self.admin.bucket_browse("files")
        self.assertEqual(self.names(view), (["docs/", "docs2/", "z/"], ["readme.txt"]))
        self.assertIsNone(view["next_cursor"])

    def test_folder_content_uses_relative_names(self):
        view = self.admin.bucket_browse("files", "docs/")
        self.assertEqual(self.names(view), (["sub/"], ["a.txt"]))
        self.assertEqual(view["objects"][0]["key"], "docs/a.txt")
        self.assertEqual(self.names(self.admin.bucket_browse("files", "docs/sub/")), ([], ["b.txt", "c.txt"]))

    def test_sibling_with_common_text_prefix_is_not_part_of_the_folder(self):
        self.assertEqual(self.names(self.admin.bucket_browse("files", "docs2/")), ([], ["x.txt"]))

    def test_pagination_visits_everything_exactly_once(self):
        for small_page in (500, 2, 1):
            self.admin._LIST_PAGE = small_page  # проверяем и порционное чтение, и «прыжки» через папки
            seen, cursor = [], ""
            for _ in range(20):
                view = self.admin.bucket_browse("files", "", cursor, limit=2)
                seen += [f["name"] for f in view["folders"]] + [o["name"] for o in view["objects"]]
                cursor = view["next_cursor"]
                if not cursor:
                    break
            self.assertEqual(sorted(seen), ["docs/", "docs2/", "readme.txt", "z/"], small_page)

    def test_created_folder_is_visible_even_when_empty_and_marker_is_hidden_inside(self):
        self.admin.folder_create("files", "photos/2026")
        self.assertIn("photos/", self.names(self.admin.bucket_browse("files"))[0])
        inside = self.admin.bucket_browse("files", "photos/")
        self.assertEqual(self.names(inside), (["2026/"], []))
        self.assertEqual(self.names(self.admin.bucket_browse("files", "photos/2026/")), ([], []))
        self.admin.folder_create("files", "photos/2026/")  # повторное создание безвредно

    def test_empty_file_can_be_created_and_is_listed(self):
        """«Создать файл» в интерфейсе — это загрузка пустого тела по ключу."""
        info = self.admin.object_upload("files", "docs/new.txt", io.BytesIO(b""))
        self.assertEqual((info["key"], info["size"]), ("docs/new.txt", 0))
        inside = self.admin.bucket_browse("files", "docs/")
        self.assertIn(("new.txt", 0), [(o["name"], o["size"]) for o in inside["objects"]])

    def test_exact_key_probe_finds_the_file_before_longer_names(self):
        """Перед созданием интерфейс проверяет, нет ли уже такого файла: тот же запрос, что у списка объектов."""
        for key in ("docs/n.txt.bak", "docs/n.txt"):
            self.core.put_object("files", key, b"x")
        found = self.admin.bucket_objects("files", prefix="docs/n.txt", limit=1)["objects"]
        self.assertEqual([o["key"] for o in found], ["docs/n.txt"])  # точное имя идёт раньше «n.txt.bak»
        missing = self.admin.bucket_objects("files", prefix="docs/none.txt", limit=1)["objects"]
        self.assertEqual(missing, [])

    def test_folder_delete_removes_only_that_folder(self):
        self.admin.folder_create("files", "docs/empty/")
        result = self.admin.folder_delete("files", "docs/")
        self.assertEqual((result["deleted"], result["failed"]), (4, 0))  # a, b, c и маркер empty/
        root = self.admin.bucket_browse("files")
        self.assertEqual(self.names(root), (["docs2/", "z/"], ["readme.txt"]))
        self.assertEqual(self.admin.bucket_browse("files", "docs/")["folders"], [])

    def test_bucket_root_cannot_be_wiped_through_folder_delete(self):
        for bad in ("", "/", None):
            with self.assertRaises(AdminError) as cm:
                self.admin.folder_delete("files", bad)
            self.assertEqual(cm.exception.status, 422, bad)
        self.assertEqual(len(self.admin.bucket_objects("files")["objects"]), 6)

    def test_upload_creates_and_overwrites(self):
        brief = self.admin.object_upload("files", "docs/new.bin", io.BytesIO(b"hello"), None)
        self.assertEqual((brief["key"], brief["size"]), ("docs/new.bin", 5))
        self.admin.object_upload("files", "docs/new.bin", io.BytesIO(b"bye"), "text/plain")
        listed = [o for o in self.admin.bucket_browse("files", "docs/")["objects"] if o["name"] == "new.bin"]
        self.assertEqual(len(listed), 1)  # перезапись заменяет, а не дублирует
        self.assertEqual((listed[0]["size"], listed[0]["content_type"]), (3, "text/plain"))
        info, stream = self.admin.object_download(listed[0]["id"])
        self.assertEqual(b"".join(stream), b"bye")

    def test_upload_empty_and_multi_chunk_file(self):
        self.admin.object_upload("files", "empty.dat", io.BytesIO(b""), None)
        big = bytes(range(256)) * 1000  # 256 000 байт > чанк теста (64 КиБ)
        self.admin.object_upload("files", "big.dat", io.BytesIO(big), None)
        objs = {o["name"]: o for o in self.admin.bucket_browse("files")["objects"]}
        self.assertEqual((objs["empty.dat"]["size"], objs["big.dat"]["size"]), (0, len(big)))
        self.assertEqual(b"".join(self.admin.object_download(objs["big.dat"]["id"])[1]), big)

    def test_dangerous_or_malformed_paths_are_rejected(self):
        for key in ("", "/abs.txt", "a//b.txt", "../x.txt", "a/./b.txt", "dir/", "bad\nname", "x" * 1100):
            with self.assertRaises(AdminError) as cm:
                self.admin.object_upload("files", key, io.BytesIO(b"x"), None)
            self.assertEqual(cm.exception.status, 422, repr(key))
        for prefix in ("/abs/", "a//", "../", "a/../b/"):
            with self.assertRaises(AdminError) as cm:
                self.admin.folder_create("files", prefix)
            self.assertEqual(cm.exception.status, 422, repr(prefix))

    def test_signed_download_link_roundtrip(self):
        oid = self.admin.bucket_browse("files", "docs/")["objects"][0]["id"]
        link = self.admin.download_link(oid)
        self.assertEqual((link["file_name"], link["expires_in"]), ("a.txt", 300))
        token = link["path"].rsplit("/", 1)[-1]
        info, stream = self.admin.download_by_token(token)
        self.assertEqual((info["key"], b"".join(stream)), ("docs/a.txt", b"docs/a.txt"))

    def test_download_links_cannot_be_forged_expired_or_reused_for_other_objects(self):
        oid = self.admin.bucket_browse("files", "docs/")["objects"][0]["id"]
        other = self.admin.bucket_browse("files")["objects"][0]["id"]
        secret = self.settings.telegram_bot_token
        token = download_links.sign(secret, oid)
        payload, sig = token.split(".")
        forged = download_links.sign(secret, other).split(".")[0] + "." + sig  # чужой payload с подписью от другого
        for bad in (download_links.sign(secret, oid, ttl=-1), forged, token[:-2] + "xx", "", "garbage", token + ".x",
                    download_links.sign("other-secret", oid)):
            with self.assertRaises(AdminError) as cm:
                self.admin.download_by_token(bad)
            self.assertEqual(cm.exception.status, 403, bad)

    def test_link_for_deleted_object_is_gone(self):
        oid = self.admin.bucket_browse("files", "docs/")["objects"][0]["id"]
        token = self.admin.download_link(oid)["path"].rsplit("/", 1)[-1]
        self.admin.object_delete(oid)
        with self.assertRaises(AdminError) as cm:
            self.admin.download_by_token(token)
        self.assertEqual(cm.exception.status, 404)

    # ─── клиентская нарезка (multipart из Mini App) ───

    MIB = 1024 * 1024

    def part(self, upload_id, n, data):
        return self.admin.multipart_part(upload_id, n, data)["etag"]

    def test_multipart_start_tells_the_client_how_to_cut(self):
        start = self.admin.multipart_start("files", "big/video.bin", "video/mp4")
        self.assertRegex(start["upload_id"], r"^[0-9a-f]{32}$")
        self.assertGreaterEqual(start["part_size"], 5 * self.MIB)  # не меньше минимума S3 для не последней части
        self.assertEqual((start["max_parallel"], start["max_parts"]), (2, 10000))  # слоты загрузки Storage по умолчанию

    def test_multipart_roundtrip_out_of_order_parts(self):
        upload = self.admin.multipart_start("files", "big/data.bin")["upload_id"]
        first, second, tail = bytes([1]) * (5 * self.MIB), bytes([2]) * (5 * self.MIB), b"tail-bytes"
        etags = {3: self.part(upload, 3, tail), 1: self.part(upload, 1, first), 2: self.part(upload, 2, second)}
        brief = self.admin.multipart_finish(upload, [{"part_number": n, "etag": etags[n]} for n in (1, 2, 3)])
        self.assertEqual((brief["key"], brief["size"]), ("big/data.bin", 10 * self.MIB + len(tail)))
        listed = [o for o in self.admin.bucket_browse("files", "big/")["objects"] if o["name"] == "data.bin"]
        self.assertEqual(len(listed), 1)
        self.assertEqual(b"".join(self.admin.object_download(listed[0]["id"])[1]), first + second + tail)

    def test_retried_part_replaces_the_failed_attempt(self):
        upload = self.admin.multipart_start("files", "retry.bin")["upload_id"]
        self.part(upload, 1, b"old")
        etag = self.part(upload, 1, b"new")  # повтор после сбоя сети
        brief = self.admin.multipart_finish(upload, [{"part_number": 1, "etag": etag}])
        self.assertEqual(b"".join(self.admin.object_download(brief["id"])[1]), b"new")

    def test_parts_can_be_uploaded_in_parallel(self):
        from concurrent.futures import ThreadPoolExecutor
        upload = self.admin.multipart_start("files", "parallel.bin")["upload_id"]
        sizes = [5 * self.MIB, 5 * self.MIB, 5 * self.MIB, 100]
        with ThreadPoolExecutor(4) as pool:
            etags = list(pool.map(lambda i: self.part(upload, i + 1, bytes([i + 1]) * sizes[i]), range(4)))
        brief = self.admin.multipart_finish(upload, [{"part_number": i + 1, "etag": e} for i, e in enumerate(etags)])
        self.assertEqual(brief["size"], sum(sizes))

    def test_busy_server_answers_503_and_the_retry_succeeds(self):
        upload = self.admin.multipart_start("files", "busy.bin")["upload_id"]
        slots = self.core._upload_slots
        taken = 0
        while slots.acquire(blocking=False):
            taken += 1
        try:
            with self.assertRaises(AdminError) as cm:
                self.admin.multipart_part(upload, 1, b"data")
            self.assertEqual(cm.exception.status, 503)  # клиент повторит
        finally:
            for _ in range(taken):
                slots.release()
        etag = self.part(upload, 1, b"data")
        brief = self.admin.multipart_finish(upload, [{"part_number": 1, "etag": etag}])
        self.assertEqual(b"".join(self.admin.object_download(brief["id"])[1]), b"data")

    def test_multipart_finish_rejects_bad_requests(self):
        upload = self.admin.multipart_start("files", "bad.bin")["upload_id"]
        etag = self.part(upload, 1, b"x" * 10)
        etag2 = self.part(upload, 2, b"y" * 10)
        cases = [
            ([], 422), (None, 422), ([{"part_number": 1}], 422), ([{"part_number": 1, "etag": "0" * 32}], 422),
            ([{"part_number": 9, "etag": etag}], 422),
            ([{"part_number": 2, "etag": etag}, {"part_number": 1, "etag": etag}], 422),  # порядок
            ([{"part_number": 1, "etag": etag}, {"part_number": 2, "etag": etag2}], 422),  # не последняя часть < 5 МиБ
        ]
        for parts, status in cases:
            with self.assertRaises(AdminError) as cm:
                self.admin.multipart_finish(upload, parts)
            self.assertEqual(cm.exception.status, status, parts)

    def test_unknown_or_aborted_upload_is_404_and_bad_key_is_422(self):
        upload = self.admin.multipart_start("files", "gone.bin")["upload_id"]
        self.part(upload, 1, b"x")
        self.admin.multipart_abort(upload)
        for call in (lambda: self.part(upload, 1, b"x"), lambda: self.admin.multipart_finish(upload, [{"part_number": 1, "etag": "a"}]),
                     lambda: self.part("not-an-id", 1, b"x")):
            with self.assertRaises(AdminError) as cm:
                call()
            self.assertEqual(cm.exception.status, 404)
        for key in ("", "/abs", "../x", "dir/"):
            with self.assertRaises(AdminError) as cm:
                self.admin.multipart_start("files", key)
            self.assertEqual(cm.exception.status, 422, key)
        with self.assertRaises(AdminError) as cm:
            self.admin.multipart_start("nope", "a")
        self.assertEqual(cm.exception.status, 404)

    def test_part_number_out_of_range_is_422(self):
        upload = self.admin.multipart_start("files", "range.bin")["upload_id"]
        for n in (0, 10001):
            with self.assertRaises(AdminError) as cm:
                self.admin.multipart_part(upload, n, b"x")
            self.assertEqual(cm.exception.status, 422)

    def test_unknown_bucket_is_404(self):
        for call in (lambda: self.admin.bucket_browse("nope"), lambda: self.admin.folder_create("nope", "a/"),
                     lambda: self.admin.folder_delete("nope", "a/"),
                     lambda: self.admin.object_upload("nope", "a", io.BytesIO(b"x"), None)):
            with self.assertRaises(AdminError) as cm:
                call()
            self.assertEqual(cm.exception.status, 404)


if __name__ == "__main__":
    unittest.main()
