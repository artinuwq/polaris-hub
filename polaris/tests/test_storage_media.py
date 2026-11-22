"""Воспроизведение видео из Storage: кэш чанков, чтение диапазонов, Range, токены плеера, безопасность."""
from __future__ import annotations

import hashlib
import os
import threading
import time
import unittest
from unittest import mock

from polaris.modules.storage import download_links, media
from polaris.modules.storage.admin import AdminError
from polaris.storage import core as storage_core
from polaris.storage.chunk_cache import ChunkCache
from polaris.storage.errors import IntegrityError, InvalidRange, StorageBusy
from tests.storage_helpers import StorageTestCase
from tests.test_storage_admin import AdminTestCase

DATA = os.urandom(5 * 1024 + 300)  # при чанке 1 КиБ — 6 чанков, последний неполный


def wait_for(cond, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


class ChunkCacheTests(unittest.TestCase):
    def test_hit_does_not_call_loader_again(self):
        cache, calls = ChunkCache(1000), []
        loader = lambda: calls.append(1) or b"x" * 10  # noqa: E731
        self.assertEqual(cache.fetch("a", loader), b"x" * 10)
        self.assertEqual(cache.fetch("a", loader), b"x" * 10)
        self.assertEqual(len(calls), 1)

    def test_lru_eviction_by_total_size_keeps_the_newest(self):
        cache = ChunkCache(100)
        for key in "abc":
            cache.fetch(key, lambda: b"z" * 40)
        self.assertEqual(cache.size, 80)
        self.assertFalse(cache.has("a"))
        self.assertTrue(cache.has("b") and cache.has("c"))
        cache.fetch("b", lambda: b"z" * 40)  # b снова «свежий»
        cache.fetch("d", lambda: b"z" * 40)
        self.assertTrue(cache.has("b") and cache.has("d") and not cache.has("c"))

    def test_one_oversized_chunk_is_still_kept(self):
        cache = ChunkCache(10)
        cache.fetch("big", lambda: b"x" * 50)
        self.assertTrue(cache.has("big"))

    def test_disabled_cache_always_loads(self):
        cache, calls = ChunkCache(0), []
        for _ in range(3):
            cache.fetch("a", lambda: calls.append(1) or b"x")
        self.assertEqual((len(calls), cache.enabled, cache.size), (3, False, 0))

    def test_concurrent_requests_share_one_download(self):
        cache, calls, gate = ChunkCache(1000), [], threading.Event()

        def loader():
            calls.append(1)
            gate.wait(2)
            return b"payload"
        results = []
        threads = [threading.Thread(target=lambda: results.append(cache.fetch("k", loader))) for _ in range(8)]
        for t in threads:
            t.start()
        time.sleep(0.15)
        gate.set()
        for t in threads:
            t.join(3)
        self.assertEqual((len(calls), results), (1, [b"payload"] * 8))

    def test_error_reaches_waiters_and_is_not_cached(self):
        cache, gate, attempts = ChunkCache(1000), threading.Event(), []

        def failing():
            attempts.append(1)
            gate.wait(2)
            raise IntegrityError("плохой чанк")
        errors = []

        def call():
            try:
                cache.fetch("k", failing)
            except IntegrityError as exc:
                errors.append(exc)
        threads = [threading.Thread(target=call) for _ in range(4)]
        for t in threads:
            t.start()
        time.sleep(0.15)
        gate.set()
        for t in threads:
            t.join(3)
        self.assertEqual((len(attempts), len(errors)), (1, 4))
        self.assertEqual(cache.fetch("k", lambda: b"ok"), b"ok")  # после сбоя можно повторить


class RangeParsingTests(unittest.TestCase):
    def test_valid_ranges(self):
        cases = {"bytes=0-": (0, 99), "bytes=10-20": (10, 20), "bytes=90-1000": (90, 99), "bytes=-10": (90, 99),
                 "bytes=-1000": (0, 99), "BYTES=5-6": (5, 6), " bytes=0-0 ": (0, 0), "bytes=99-": (99, 99)}
        for header, expected in cases.items():
            self.assertEqual(media.parse_range(header, 100), expected, header)

    def test_header_that_means_full_file(self):
        for header in (None, "", "items=1-2", "bytes=1-2,5-6", "bytes=5-2", "bytes=-", "bytes=a-b", "garbage"):
            self.assertIsNone(media.parse_range(header, 100), header)
        self.assertIsNone(media.parse_range("bytes=0-10", 0))  # пустой файл

    def test_unsatisfiable(self):
        for header in ("bytes=100-", "bytes=500-600", "bytes=-0"):
            with self.assertRaises(media.RangeNotSatisfiable, msg=header):
                media.parse_range(header, 100)

    def test_media_types_only(self):
        self.assertEqual(media.media_mime("a.bin", "video/mp4"), "video/mp4")
        self.assertEqual(media.media_mime("a.bin", "video/mp4; codecs=avc1"), "video/mp4")
        self.assertEqual(media.media_mime("x/clip.WEBM", "application/octet-stream"), "video/webm")
        self.assertEqual(media.media_mime("song.mp3", None), "audio/mpeg")
        self.assertEqual(media.media_mime("clip.mp4", "text/html"), "video/mp4")  # тип берётся по расширению, не из html
        for name, stored in (("page.html", "text/html"), ("pic.svg", "image/svg+xml"), ("a.zip", "application/zip"), ("noext", None)):
            self.assertIsNone(media.media_mime(name, stored), name)


class CoreRangeCacheTests(StorageTestCase):
    chunk_size = 1024

    def setUp(self):
        super().setUp()
        self.core = self.make_core(self.driver, chunk_cache_bytes=10 * 1024)
        self.core.ensure_bucket("files")
        self.obj = self.core.put_object("files", "v.bin", DATA)
        self.downloads = []
        real = self.driver.download
        self.driver.download = lambda ref: (self.downloads.append(ref), real(ref))[1]

    def read(self, start, end):
        return b"".join(self.core.stream(self.obj.id, start, end, read_ahead=True))

    def test_ranges_match_the_source_including_chunk_boundaries(self):
        for start, end in ((0, 0), (0, 1023), (1023, 1024), (500, 3500), (5000, len(DATA) - 1), (len(DATA) - 1, len(DATA) - 1)):
            self.assertEqual(self.read(start, end), DATA[start:end + 1], (start, end))

    def test_range_is_served_in_small_pieces(self):
        with mock.patch.object(storage_core, "_RANGE_PIECE", 100):
            pieces = list(self.core.stream(self.obj.id, 0, 999, read_ahead=True))
        self.assertGreaterEqual(len(pieces), 10)
        self.assertTrue(all(len(p) <= 100 for p in pieces))
        self.assertEqual(b"".join(pieces), DATA[:1000])

    def test_small_requests_in_one_chunk_download_it_once(self):
        for start in range(0, 1000, 100):
            self.assertEqual(self.read(start, start + 49), DATA[start:start + 50])
        first_chunk = self.downloads[0]
        self.assertEqual(self.downloads.count(first_chunk), 1)

    def test_next_chunk_is_prefetched_in_background(self):
        self.read(0, 99)
        self.assertTrue(wait_for(lambda: len(set(self.downloads)) >= 2), "следующий чанк должен подкачаться заранее")
        second = list(dict.fromkeys(self.downloads))[1]
        self.assertEqual(self.read(1024, 1100), DATA[1024:1101])
        time.sleep(0.1)
        self.assertEqual(self.downloads.count(second), 1, "подкачанный чанк не качается второй раз")

    def test_no_prefetch_after_the_last_chunk(self):
        self.read(len(DATA) - 10, len(DATA) - 1)
        time.sleep(0.15)
        self.assertEqual(len(set(self.downloads)), 1)

    def test_full_download_neither_fills_the_cache_nor_loses_verification(self):
        self.assertEqual(b"".join(self.core.stream(self.obj.id)), DATA)
        self.assertEqual(self.core._chunk_cache.size, 0)
        ref = next(iter(self.driver.blobs))
        self.driver.blobs[ref] = bytes(len(self.driver.blobs[ref]))
        with self.assertRaises(IntegrityError):
            b"".join(self.core.stream(self.obj.id))

    def test_corrupted_chunk_is_not_cached_and_recovers(self):
        ref = self.downloads and self.downloads[0] or sorted(self.driver.blobs)[0]
        good = self.driver.blobs[ref]
        self.driver.blobs[ref] = bytes(len(good))
        with self.assertRaises(IntegrityError):
            self.read(0, 10)
        self.driver.blobs[ref] = good
        self.assertEqual(self.read(0, 10), DATA[:11])

    def test_cache_hits_do_not_need_a_download_slot(self):
        core = self.make_core(self.driver, chunk_cache_bytes=10 * 1024, max_concurrent_downloads=1, range_slot_wait=0.05)
        self.assertEqual(b"".join(core.stream(self.obj.id, 0, 99, read_ahead=True)), DATA[:100])
        time.sleep(0.1)  # дать подкачке закончить и вернуть слот
        full = core.stream(self.obj.id)
        next(full)  # полное скачивание держит единственный слот
        self.assertEqual(b"".join(core.stream(self.obj.id, 10, 50, read_ahead=True)), DATA[10:51])  # чанк уже в кэше — слот не нужен
        with self.assertRaises(StorageBusy):  # а за новым чанком нужно ждать слот: ждём недолго и честно отказываем
            b"".join(core.stream(self.obj.id, 4000, 4100, read_ahead=True))
        full.close()

    def test_without_read_ahead_nothing_extra_is_read_or_cached(self):
        # прежний контракт для S3 и остальных: читаются только пересекающиеся чанки, кэш и подкачка не участвуют
        self.assertEqual(b"".join(self.core.stream(self.obj.id, 1100, 1200)), DATA[1100:1201])
        time.sleep(0.15)
        self.assertEqual(len(self.downloads), 1)
        self.assertEqual(self.core._chunk_cache.size, 0)

    def test_disabled_cache_keeps_working_the_old_way(self):
        core = self.make_core(self.driver, chunk_cache_bytes=0)
        self.assertEqual(b"".join(core.stream(self.obj.id, 700, 2600, read_ahead=True)), DATA[700:2601])
        with self.assertRaises(InvalidRange):
            core.stream(self.obj.id, len(DATA), len(DATA) + 5)


class AdminMediaTests(AdminTestCase):
    chunk_size = 1024

    def setUp(self):
        super().setUp()
        self.core = self.make_core(self.driver, chunk_size=1024, chunk_cache_bytes=64 * 1024, max_concurrent_uploads=8)
        self.service._core = self.core
        self.video = self.core.put_object("files", "movies/clip.webm", DATA, mime_type="video/webm")
        self.token = self.admin.media_link(self.video.id)["path"].rsplit("/", 1)[-1]

    def open(self, header=None, token=None):
        info, stream = self.admin.media_open(token or self.token, header)
        return info, (b"".join(stream) if stream is not None else None)

    def test_link_describes_the_media_and_hides_internals(self):
        link = self.admin.media_link(self.video.id)
        self.assertTrue(link["path"].startswith("/api/storage/media/"))
        self.assertEqual((link["content_type"], link["size"], link["file_name"], link["expires_in"]),
                         ("video/webm", len(DATA), "clip.webm", media.MEDIA_TTL))
        self.assertNotIn(self.video.id, link["path"])  # id объекта виден только внутри подписанного токена
        self.assertGreaterEqual(media.MEDIA_TTL, 2 * 3600)

    def test_non_media_and_missing_objects_get_no_link(self):
        page = self.core.put_object("files", "page.html", b"<script>1</script>", mime_type="text/html")
        for oid, status in ((page.id, 415), ("нет-такого", 404)):
            with self.assertRaises(AdminError) as ctx:
                self.admin.media_link(oid)
            self.assertEqual(ctx.exception.status, status)

    def test_without_range_whole_file(self):
        info, body = self.open()
        self.assertEqual((info["status"], info["size"], info["content_type"]), (200, len(DATA), "video/webm"))
        self.assertEqual(body, DATA)

    def test_ranges(self):
        cases = {"bytes=100-199": DATA[100:200], "bytes=0-": DATA, "bytes=-300": DATA[-300:], "bytes=1000-2100": DATA[1000:2101],
                 f"bytes={len(DATA) - 5}-{len(DATA) + 999}": DATA[-5:]}
        for header, expected in cases.items():
            info, body = self.open(header)
            self.assertEqual(body, expected, header)
            self.assertEqual(info["status"], 206 if header != "bytes=0-" else 206, header)
            self.assertEqual(info["end"] - info["start"] + 1, len(expected), header)

    def test_garbage_range_header_gives_the_whole_file(self):
        info, body = self.open("bytes=1-2,5-6")
        self.assertEqual((info["status"], body), (200, DATA))

    def test_range_outside_the_file_is_416_without_a_stream(self):
        info, stream = self.admin.media_open(self.token, f"bytes={len(DATA)}-")
        self.assertEqual((info["status"], info["size"], stream), (416, len(DATA), None))

    def test_empty_media_file(self):
        empty = self.core.put_object("files", "empty.mp4", b"", mime_type="video/mp4")
        token = self.admin.media_link(empty.id)["path"].rsplit("/", 1)[-1]
        for header in (None, "bytes=0-10"):
            info, body = self.open(header, token)
            self.assertEqual((info["status"], body), (200, b""))

    def test_player_requests_reuse_cached_chunks(self):
        downloads = []
        real = self.driver.download
        self.driver.download = lambda ref: (downloads.append(ref), real(ref))[1]
        for start in range(0, 900, 90):
            self.open(f"bytes={start}-{start + 89}")
        self.assertEqual(downloads.count(downloads[0]), 1)

    def test_tokens_cannot_be_swapped_between_download_and_media(self):
        download_token = self.admin.download_link(self.video.id)["path"].rsplit("/", 1)[-1]
        with self.assertRaises(AdminError) as ctx:
            self.admin.media_open(download_token, None)
        self.assertEqual(ctx.exception.status, 403)
        with self.assertRaises(AdminError):  # токен плеера не открывает обычную выдачу «скачать»
            self.admin.download_by_token(self.token)

    def test_forged_expired_and_foreign_tokens(self):
        secret = self.admin._link_secret()
        bad = {"мусор": "abc.def", "истёк": download_links.sign(secret, media.MEDIA_PREFIX + self.video.id, ttl=-5),
               "чужой секрет": download_links.sign("other", media.MEDIA_PREFIX + self.video.id),
               "без префикса": download_links.sign(secret, self.video.id), "подделка": self.token[:-3] + "AAA"}
        for label, token in bad.items():
            with self.assertRaises(AdminError, msg=label) as ctx:
                self.admin.media_open(token, None)
            self.assertEqual(ctx.exception.status, 403, label)

    def test_type_is_derived_from_the_extension_when_the_stored_one_is_generic(self):
        generic = self.core.put_object("files", "from-telegram.mp4", b"x" * 10, mime_type="application/octet-stream")
        link = self.admin.media_link(generic.id)
        self.assertEqual(link["content_type"], "video/mp4")
        self.assertEqual(self.open(None, link["path"].rsplit("/", 1)[-1])[0]["content_type"], "video/mp4")

    def test_storage_failures_become_clean_errors_before_any_bytes(self):
        class Failing:
            def __init__(self, error):
                self.error = error

            def __iter__(self):
                return self

            def __next__(self):
                raise self.error
        for error, status in ((StorageBusy("слоты"), 503), (IntegrityError("sha"), 502)):
            with mock.patch.object(self.core, "stream", return_value=Failing(error)):
                with self.assertRaises(AdminError) as ctx:
                    self.admin.media_open(self.token, "bytes=0-9")
            self.assertEqual(ctx.exception.status, status)
            self.assertNotIn("sha", ctx.exception.detail.lower())

    def test_corrupted_chunk_is_never_served(self):
        for ref in list(self.driver.blobs):
            self.driver.blobs[ref] = bytes(len(self.driver.blobs[ref]))
        with self.assertRaises(AdminError) as ctx:
            self.admin.media_open(self.token, "bytes=0-9")
        self.assertEqual(ctx.exception.status, 502)

    def test_deleted_object_is_gone_even_with_cached_chunks(self):
        self.open("bytes=0-99")
        self.core.delete_object(self.video.id)
        with self.assertRaises(AdminError) as ctx:
            self.admin.media_open(self.token, "bytes=0-99")
        self.assertEqual(ctx.exception.status, 404)


if __name__ == "__main__":
    unittest.main()
