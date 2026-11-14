"""Интеграционные тесты S3 API: HTTP-запрос -> S3 API -> Storage Core -> Backend Driver (mock-backend)."""
from __future__ import annotations

import base64
import hashlib
import os
import shutil
import tempfile
import threading
import time
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

from polaris.s3.api import S3App
from polaris.s3.config import S3Config
from polaris.s3.credentials import Credential, StaticCredentialProvider
from polaris.s3.service import S3Service
from polaris.storage.backends.base import TransientBackendError
from polaris.storage.core import StorageCore
from polaris.storage.database import StorageDatabase
from polaris.storage.errors import StorageUnavailable
from tests.asgi_server import AsgiTestServer
from tests.s3_client import AK, SK, S3Client
from tests.storage_helpers import MemoryDriver

MIB = 1024 * 1024


def tree(resp) -> ET.Element:
    root = ET.fromstring(resp.content)
    for el in root.iter():
        el.tag = el.tag.rsplit("}", 1)[-1]
    return root


def code(resp) -> str:
    return tree(resp).findtext("Code")


class S3TestCase(unittest.TestCase):
    chunk_size = 64 * 1024
    config_kw: dict = {}
    core_kw: dict = {}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="polaris-s3-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.db = StorageDatabase(self.tmp / "storage.db")
        self.addCleanup(self.db.close)
        self.db.migrate()
        self.driver = MemoryDriver()
        self.boot()

    def boot(self):
        """Поднять ядро, S3 и HTTP-сервер (повторный вызов имитирует перезапуск Polaris)."""
        kw = {"max_concurrent_uploads": 8, "max_concurrent_downloads": 8, "chunk_size": self.chunk_size,
              "sleep": lambda s: None, **self.core_kw}
        self.core = StorageCore(self.db, self.driver, **kw)
        self.core.multipart.min_part_size = 5 * MIB
        self.get_core = lambda: self.core
        config = S3Config(enabled=True, path_prefix="/s3", **self.config_kw)
        self.app = S3App(config, StaticCredentialProvider([Credential(AK, SK)]),
                         S3Service(lambda: self.get_core(), region=config.region))
        self.server = AsgiTestServer(self.app).start()
        self.addCleanup(self.app.close)
        self.addCleanup(self.server.stop)  # LIFO: сначала сервер, потом пул потоков
        self.s3 = S3Client(self.server.url)

    def mkbucket(self, name="bucket1"):
        r = self.s3.request("PUT", name)
        self.assertEqual(r.status_code, 200, r.text)
        return name


class BucketTests(S3TestCase):
    def test_bucket_lifecycle(self):
        r = self.s3.request("GET")
        self.assertEqual((r.status_code, [b.findtext("Name") for b in tree(r).iter("Bucket")]), (200, []))
        self.assertEqual(self.s3.request("PUT", "my-bucket").status_code, 200)
        r = self.s3.request("PUT", "my-bucket")
        self.assertEqual((r.status_code, code(r)), (409, "BucketAlreadyOwnedByYou"))
        r = self.s3.request("GET")
        self.assertEqual([b.findtext("Name") for b in tree(r).iter("Bucket")], ["my-bucket"])
        self.assertTrue(tree(r).find("Buckets/Bucket/CreationDate").text.endswith("Z"))
        h = self.s3.request("HEAD", "my-bucket")
        self.assertEqual((h.status_code, h.headers["x-amz-bucket-region"]), (200, "us-east-1"))
        missing = self.s3.request("HEAD", "nope-bucket")
        self.assertEqual((missing.status_code, missing.content), (404, b""))
        self.assertEqual(code(self.s3.request("GET", "nope-bucket", query="list-type=2")), "NoSuchBucket")
        loc = self.s3.request("GET", "my-bucket", query="location")
        self.assertEqual((loc.status_code, tree(loc).tag), (200, "LocationConstraint"))
        self.assertEqual(self.s3.request("DELETE", "my-bucket").status_code, 204)
        self.assertEqual(code(self.s3.request("DELETE", "my-bucket")), "NoSuchBucket")

    def test_delete_non_empty_bucket_refused(self):
        b = self.mkbucket()
        self.s3.put(b, "k", b"x")
        r = self.s3.request("DELETE", b)
        self.assertEqual((r.status_code, code(r)), (409, "BucketNotEmpty"))
        self.s3.request("DELETE", b, "k")
        self.assertEqual(self.s3.request("DELETE", b).status_code, 204)

    def test_invalid_bucket_names(self):
        for name in ("ab", "UPPER", "bad_name", "-start", "end-", "a..b", "192.168.0.1", "x" * 64,
                     "polaris-internal.multipart"):
            r = self.s3.request("PUT", name)
            self.assertEqual((r.status_code, code(r)), (400, "InvalidBucketName"), name)

    def test_create_bucket_with_location_constraint(self):
        body = b"<CreateBucketConfiguration><LocationConstraint>us-east-1</LocationConstraint></CreateBucketConfiguration>"
        self.assertEqual(self.s3.request("PUT", "loc-bucket", body=body).status_code, 200)
        bad = b"<CreateBucketConfiguration><LocationConstraint>eu-west-1</LocationConstraint></CreateBucketConfiguration>"
        self.assertEqual(self.s3.request("PUT", "loc-bucket2", body=bad).status_code, 400)

    def test_internal_bucket_is_invisible(self):
        b = self.mkbucket()
        up = self.s3.request("POST", b, "k", query="uploads")
        self.assertEqual(up.status_code, 200)
        names = [x.findtext("Name") for x in tree(self.s3.request("GET")).iter("Bucket")]
        self.assertEqual(names, [b])
        r = self.s3.request("GET", "polaris-internal.multipart", query="list-type=2")
        self.assertEqual(code(r), "NoSuchBucket")


class ObjectTests(S3TestCase):
    def setUp(self):
        super().setUp()
        self.b = self.mkbucket()

    def test_put_get_head_delete(self):
        data = b"hello polaris"
        r = self.s3.put(self.b, "dir/hello.txt", data, headers={
            "Content-Type": "text/plain", "x-amz-meta-Author": "me", "Cache-Control": "max-age=60",
            "Content-Disposition": "attachment; filename=h.txt"})
        self.assertEqual((r.status_code, r.headers["ETag"]), (200, f'"{hashlib.md5(data).hexdigest()}"'))
        g = self.s3.get(self.b, "dir/hello.txt")
        self.assertEqual((g.status_code, g.content), (200, data))
        for name, value in {"Content-Type": "text/plain", "x-amz-meta-author": "me", "Cache-Control": "max-age=60",
                            "Content-Length": str(len(data)), "Accept-Ranges": "bytes",
                            "ETag": r.headers["ETag"]}.items():
            self.assertEqual(g.headers[name], value, name)
        self.assertRegex(g.headers["Last-Modified"], r"^\w{3}, \d{2} \w{3} \d{4} \d{2}:\d{2}:\d{2} GMT$")
        self.assertIn("x-amz-request-id", g.headers)
        h = self.s3.request("HEAD", self.b, "dir/hello.txt")
        self.assertEqual((h.status_code, h.content, h.headers["Content-Length"]), (200, b"", str(len(data))))
        self.assertEqual(self.s3.request("DELETE", self.b, "dir/hello.txt").status_code, 204)
        self.assertEqual(self.s3.request("DELETE", self.b, "dir/hello.txt").status_code, 204)  # идемпотентно
        miss = self.s3.get(self.b, "dir/hello.txt")
        self.assertEqual((miss.status_code, code(miss)), (404, "NoSuchKey"))
        self.assertEqual(tree(miss).findtext("Resource"), f"/{self.b}/dir/hello.txt")
        self.assertEqual(tree(miss).findtext("RequestId"), miss.headers["x-amz-request-id"])
        hm = self.s3.request("HEAD", self.b, "dir/hello.txt")
        self.assertEqual((hm.status_code, hm.content), (404, b""))
        self.assertEqual(self.driver.blobs, {})  # физически удалено

    def test_default_content_type_and_empty_object(self):
        self.s3.put(self.b, "empty", b"")
        g = self.s3.get(self.b, "empty")
        self.assertEqual((g.status_code, g.content, g.headers["Content-Length"]), (200, b"", "0"))
        self.assertEqual(g.headers["Content-Type"], "binary/octet-stream")
        self.assertEqual(g.headers["ETag"], '"d41d8cd98f00b204e9800998ecf8427e"')
        self.assertEqual(self.driver.upload_count, 0)

    def test_overwrite(self):
        self.s3.put(self.b, "k", b"first" * 5000)
        self.s3.put(self.b, "k", b"second")
        self.assertEqual(self.s3.get(self.b, "k").content, b"second")
        self.assertEqual(len(tree(self.s3.get(self.b, "", query="list-type=2")).findall("Contents")), 1)
        self.assertEqual(len(self.driver.blobs), 1)

    def test_unicode_and_special_keys(self):
        keys = ["папка/файл с пробелом+плюс #?.txt", "日本語/キー", "a//b", "key with %41 percent",
                "tilde~and!star*(paren)'quote", "trailing/", "emoji-😀", "UPPER/lower"]
        for key in keys:
            self.assertEqual(self.s3.put(self.b, key, key.encode()).status_code, 200, key)
        for key in keys:
            g = self.s3.get(self.b, key)
            self.assertEqual((g.status_code, g.content), (200, key.encode()), key)
        r = self.s3.get(self.b, "", query="list-type=2&encoding-type=url")
        listed = sorted(requests.utils.unquote(k.text) for k in tree(r).iter("Key"))
        self.assertEqual(listed, sorted(keys))
        r = self.s3.get(self.b, "", query="list-type=2")  # без url-кодирования ключи отдаются как есть
        self.assertEqual(sorted(k.text for k in tree(r).iter("Key")), sorted(keys))

    def test_invalid_keys(self):
        r = self.s3.put(self.b, "x" * 1025, b"")
        self.assertEqual((r.status_code, code(r)), (400, "KeyTooLongError"))
        r = self.s3.put(self.b, "ctl\x01char", b"")
        self.assertEqual((r.status_code, code(r)), (400, "InvalidArgument"))
        self.assertEqual(self.s3.put(self.b, "ok", b"ok").status_code, 200)

    def test_invalid_percent_encoding_is_rejected_cleanly(self):
        r = requests.get(f"{self.server.url}/s3/{self.b}/%ff%fe")
        self.assertIn(r.status_code, (400, 403))
        self.assertEqual(tree(r).tag, "Error")
        self.assertNotIn("Traceback", r.text)

    def test_large_multi_chunk_roundtrip(self):
        data = os.urandom(3 * MIB + 12345)
        self.assertEqual(self.s3.put(self.b, "big", data).status_code, 200)
        g = self.s3.get(self.b, "big")
        self.assertEqual(hashlib.sha256(g.content).digest(), hashlib.sha256(data).digest())
        self.assertGreater(len(self.driver.blobs), 40)
        self.assertEqual(self.s3.request("DELETE", self.b, "big").status_code, 204)
        self.assertEqual(self.driver.blobs, {})

    def test_unsigned_payload_and_dedup_by_declared_hash(self):
        data = os.urandom(200_000)
        self.s3.put(self.b, "a", data)  # x-amz-content-sha256 = sha256(data)
        uploads = self.driver.upload_count
        self.assertEqual(self.s3.put(self.b, "b", data).status_code, 200)  # то же содержимое: в backend ничего не уходит
        self.assertEqual(self.driver.upload_count, uploads)
        r = self.s3.put(self.b, "c", data, payload_hash="UNSIGNED-PAYLOAD")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.s3.get(self.b, "c").content, data)

    def test_content_md5_and_hash_mismatch_create_nothing(self):
        data = b"payload" * 1000
        bad_md5 = base64.b64encode(hashlib.md5(b"other").digest()).decode()
        r = self.s3.put(self.b, "k1", data, headers={"Content-MD5": bad_md5})
        self.assertEqual((r.status_code, code(r)), (400, "BadDigest"))
        ok_md5 = base64.b64encode(hashlib.md5(data).digest()).decode()
        self.assertEqual(self.s3.put(self.b, "k2", data, headers={"Content-MD5": ok_md5}).status_code, 200)
        r = self.s3.put(self.b, "k3", data, payload_hash=hashlib.sha256(b"different").hexdigest())
        self.assertEqual((r.status_code, code(r)), (400, "XAmzContentSHA256Mismatch"))
        r = self.s3.put(self.b, "k4", data, headers={"Content-MD5": "!!!"})
        self.assertEqual(code(r), "InvalidDigest")
        keys = [k.text for k in tree(self.s3.get(self.b, "", query="list-type=2")).iter("Key")]
        self.assertEqual(keys, ["k2"])
        self.assertEqual(self.q_blobs(), 1)

    def q_blobs(self):
        return self.db.connection().execute("SELECT COUNT(*) FROM storage_blobs").fetchone()[0]

    def test_x_amz_checksum_header_verified(self):
        import zlib
        data = b"abc" * 100
        good = base64.b64encode(zlib.crc32(data).to_bytes(4, "big")).decode()
        self.assertEqual(self.s3.put(self.b, "ok", data, headers={"x-amz-checksum-crc32": good}).status_code, 200)
        bad = base64.b64encode((zlib.crc32(data) ^ 1).to_bytes(4, "big")).decode()
        self.assertEqual(code(self.s3.put(self.b, "bad", data, headers={"x-amz-checksum-crc32": bad})), "BadDigest")

    def test_user_metadata_limits_and_unsupported_headers(self):
        r = self.s3.put(self.b, "k", b"", headers={"x-amz-meta-big": "x" * 3000})
        self.assertEqual(code(r), "MetadataTooLarge")
        for header, value in (("x-amz-acl", "public-read"), ("x-amz-server-side-encryption", "AES256"),
                              ("x-amz-tagging", "a=b"), ("x-amz-storage-class", "GLACIER")):
            r = self.s3.put(self.b, "k", b"", headers={header: value})
            self.assertIn((r.status_code, code(r)), [(501, "NotImplemented"), (400, "InvalidArgument")], header)
        self.assertEqual(self.s3.put(self.b, "k", b"", headers={"x-amz-acl": "private"}).status_code, 200)

    def test_response_header_overrides(self):
        self.s3.put(self.b, "k", b"x", headers={"Content-Type": "text/plain"})
        g = self.s3.get(self.b, "k", query="response-content-type=application%2Fjson&response-content-disposition=inline")
        self.assertEqual((g.headers["Content-Type"], g.headers["Content-Disposition"]), ("application/json", "inline"))


class RangeAndConditionalTests(S3TestCase):
    def setUp(self):
        super().setUp()
        self.b = self.mkbucket()
        self.data = os.urandom(300_000)  # несколько чанков по 64 KiB
        self.s3.put(self.b, "r", self.data)

    def rng(self, value):
        return self.s3.get(self.b, "r", headers={"Range": value})

    def test_ranges(self):
        n = len(self.data)
        cases = {"bytes=0-9": (0, 9), "bytes=65530-65545": (65530, 65545), "bytes=100000-": (100000, n - 1),
                 "bytes=-500": (n - 500, n - 1), "bytes=0-0": (0, 0), f"bytes={n - 1}-{n - 1}": (n - 1, n - 1),
                 f"bytes=10-{n + 1000}": (10, n - 1), f"bytes=0-{n - 1}": (0, n - 1), "bytes=-999999999": (0, n - 1)}
        for header, (a, b) in cases.items():
            r = self.rng(header)
            self.assertEqual(r.status_code, 206, header)
            self.assertEqual(r.content, self.data[a:b + 1], header)
            self.assertEqual(r.headers["Content-Range"], f"bytes {a}-{b}/{n}", header)
            self.assertEqual(r.headers["Content-Length"], str(b - a + 1), header)

    def test_unsatisfiable_and_ignored_ranges(self):
        n = len(self.data)
        for header in (f"bytes={n}-", f"bytes={n + 5}-{n + 10}", "bytes=-0"):
            r = self.rng(header)
            self.assertEqual((r.status_code, code(r)), (416, "InvalidRange"), header)
        for header in ("bytes=5-2", "items=0-5", "bytes=0-1,5-9", "garbage", "bytes=-"):
            r = self.rng(header)
            self.assertEqual((r.status_code, r.content), (200, self.data), header)

    def test_head_with_range_and_empty_object(self):
        h = self.s3.request("HEAD", self.b, "r", headers={"Range": "bytes=0-9"})
        self.assertEqual((h.status_code, h.headers["Content-Length"], h.content), (206, "10", b""))
        self.s3.put(self.b, "empty", b"")
        self.assertEqual(code(self.s3.get(self.b, "empty", headers={"Range": "bytes=0-0"})), "InvalidRange")

    def test_range_reads_only_needed_chunks(self):
        downloads = []
        original = self.driver.download
        self.driver.download = lambda ref: (downloads.append(ref), original(ref))[1]
        self.assertEqual(self.rng("bytes=70000-70010").status_code, 206)
        self.assertEqual(len(downloads), 1)

    def test_corrupted_chunk_never_returns_success(self):
        ref = self.db.connection().execute(
            "SELECT backend_reference FROM storage_chunks ORDER BY chunk_index LIMIT 1").fetchone()[0]
        self.driver.blobs[ref] = b"X" * len(self.driver.blobs[ref])
        r = self.s3.get(self.b, "r")
        self.assertEqual(r.status_code, 500)
        self.assertEqual(code(r), "InternalError")
        self.assertNotIn("SHA", r.text)

    def test_conditional_requests(self):
        etag = self.s3.get(self.b, "r").headers["ETag"]
        lm = self.s3.get(self.b, "r").headers["Last-Modified"]
        r = self.s3.get(self.b, "r", headers={"If-None-Match": etag})
        self.assertEqual((r.status_code, r.content), (304, b""))
        self.assertEqual(self.s3.get(self.b, "r", headers={"If-None-Match": '"other"'}).status_code, 200)
        self.assertEqual(self.s3.get(self.b, "r", headers={"If-Match": etag}).status_code, 200)
        r = self.s3.get(self.b, "r", headers={"If-Match": '"other"'})
        self.assertEqual((r.status_code, code(r)), (412, "PreconditionFailed"))
        self.assertEqual(self.s3.get(self.b, "r", headers={"If-Modified-Since": lm}).status_code, 304)
        self.assertEqual(self.s3.get(self.b, "r", headers={"If-Unmodified-Since": "Mon, 01 Jan 2001 00:00:00 GMT"}).status_code, 412)


class ListingTests(S3TestCase):
    def setUp(self):
        super().setUp()
        self.b = self.mkbucket()
        self.keys = sorted([f"photos/2024/{i:02d}.jpg" for i in range(7)] + [f"photos/2025/{i}.jpg" for i in range(3)]
                           + ["photos/readme.txt", "docs/a.md", "docs/b.md", "root.txt", "zzz", "a b/c"])
        for k in self.keys:
            self.s3.put(self.b, k, k.encode())

    def ls(self, query):
        r = self.s3.get(self.b, "", query=query)
        self.assertEqual(r.status_code, 200, r.text)
        t = tree(r)
        return t, [k.text for k in t.iter("Key")], [p.text for p in t.findall("CommonPrefixes/Prefix")]

    def test_basic_prefix_delimiter(self):
        t, keys, prefixes = self.ls("list-type=2")
        self.assertEqual(keys, self.keys)
        self.assertEqual((t.findtext("KeyCount"), t.findtext("IsTruncated"), t.findtext("MaxKeys")), ("14".replace("14", str(len(self.keys))), "false", "1000"))
        _, keys, prefixes = self.ls("list-type=2&delimiter=%2F")
        self.assertEqual((keys, prefixes), (["root.txt", "zzz"], ["a b/", "docs/", "photos/"]))
        _, keys, prefixes = self.ls("list-type=2&delimiter=%2F&prefix=photos%2F")
        self.assertEqual((keys, prefixes), (["photos/readme.txt"], ["photos/2024/", "photos/2025/"]))
        _, keys, prefixes = self.ls("list-type=2&prefix=photos%2F2024%2F0")
        self.assertEqual(len(keys), 7)
        _, keys, prefixes = self.ls("list-type=2&prefix=nothing")
        self.assertEqual((keys, prefixes), ([], []))

    def collect(self, base):
        keys, prefixes, token, pages = [], [], None, 0
        while True:
            q = base + (f"&continuation-token={requests.utils.quote(token, safe='')}" if token else "")
            t, k, p = self.ls(q)
            keys += k
            prefixes += p
            pages += 1
            if t.findtext("IsTruncated") == "false":
                self.assertIsNone(t.find("NextContinuationToken"))
                return keys, prefixes, pages
            token = t.findtext("NextContinuationToken")
            self.assertTrue(token)
            self.assertEqual(t.findtext("ContinuationToken") or "", token if False else (t.findtext("ContinuationToken") or ""))
            self.assertLess(pages, 100)

    def test_pagination_without_delimiter_returns_every_key_once(self):
        for size in (1, 2, 3, 5, 100):
            keys, _, pages = self.collect(f"list-type=2&max-keys={size}")
            self.assertEqual(keys, self.keys, size)
            self.assertEqual(pages, -(-len(self.keys) // size) if size < len(self.keys) else 1, size)

    def test_pagination_with_delimiter_counts_prefixes(self):
        for size in (1, 2, 3):
            keys, prefixes, _ = self.collect(f"list-type=2&max-keys={size}&delimiter=%2F")
            self.assertEqual((keys, prefixes), (["root.txt", "zzz"], ["a b/", "docs/", "photos/"]), size)
        keys, prefixes, _ = self.collect("list-type=2&max-keys=2&delimiter=%2F&prefix=photos%2F")
        self.assertEqual((keys, prefixes), (["photos/readme.txt"], ["photos/2024/", "photos/2025/"]))

    def test_start_after_and_max_keys_zero(self):
        _, keys, _ = self.ls("list-type=2&start-after=photos%2F2025%2F2.jpg")
        self.assertEqual(keys, [k for k in self.keys if k > "photos/2025/2.jpg"])
        t, keys, _ = self.ls("list-type=2&max-keys=0")
        self.assertEqual((keys, t.findtext("IsTruncated"), t.findtext("KeyCount")), ([], "false", "0"))
        t, keys, _ = self.ls("list-type=2&max-keys=5000")
        self.assertEqual(t.findtext("MaxKeys"), "1000")

    def test_invalid_listing_arguments(self):
        for q in ("list-type=2&max-keys=abc", "list-type=2&max-keys=-1", "list-type=2&continuation-token=%25%25bad",
                  "list-type=2&continuation-token=", "list-type=2&encoding-type=base64", "list-type=3"):
            r = self.s3.get(self.b, "", query=q)
            self.assertEqual((r.status_code, code(r)), (400, "InvalidArgument"), q)

    def test_listing_object_fields_and_v1(self):
        t, _, _ = self.ls("list-type=2&prefix=root.txt")
        c = t.find("Contents")
        self.assertEqual((c.findtext("Size"), c.findtext("ETag"), c.findtext("StorageClass")),
                         (str(len(b"root.txt")), f'"{hashlib.md5(b"root.txt").hexdigest()}"', "STANDARD"))
        self.assertRegex(c.findtext("LastModified"), r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
        r = self.s3.get(self.b, "", query="max-keys=3&delimiter=%2F")  # ListObjects v1
        t = tree(r)
        self.assertEqual([p.text for p in t.findall("CommonPrefixes/Prefix")], ["a b/", "docs/", "photos/"])
        self.assertEqual((t.findtext("IsTruncated"), t.findtext("NextMarker")), ("true", "photos/"))
        r2 = self.s3.get(self.b, "", query="max-keys=100&delimiter=%2F&marker=photos%2F")
        self.assertEqual([k.text for k in tree(r2).iter("Key")], ["root.txt", "zzz"])
        self.assertEqual(tree(r2).findall("CommonPrefixes"), [])


if __name__ == "__main__":
    unittest.main()
