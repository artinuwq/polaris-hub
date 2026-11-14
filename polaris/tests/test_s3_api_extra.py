"""S3 API: аутентификация, потоковые тела, presigned, multipart, ошибки, ограничения, curl."""
from __future__ import annotations

import base64
import hashlib
import os
import shutil
import socket
import subprocess
import time
import unittest
import zlib
from datetime import datetime, timedelta, timezone

import requests

from polaris.storage.backends.base import TransientBackendError
from polaris.storage.errors import StorageUnavailable
from tests.s3_client import AK, SK, S3Client
from tests.test_s3_api import MIB, S3TestCase, code, tree


class AuthTests(S3TestCase):
    def test_anonymous_and_bad_credentials(self):
        r = self.s3.request("GET", sign=False)
        self.assertEqual((r.status_code, code(r)), (403, "AccessDenied"))
        r = self.s3.request("GET", secret="wrong-secret")
        self.assertEqual((r.status_code, code(r)), (403, "SignatureDoesNotMatch"))
        r = self.s3.request("GET", ak="AKIAUNKNOWNKEY")
        self.assertEqual((r.status_code, code(r)), (403, "InvalidAccessKeyId"))
        old = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y%m%dT%H%M%SZ")
        r = self.s3.request("GET", amz_date=old)
        self.assertEqual((r.status_code, code(r)), (403, "RequestTimeTooSkewed"))
        wrong_region = S3Client(self.server.url, region="eu-west-1")
        r = wrong_region.request("GET")
        self.assertEqual((r.status_code, code(r)), (400, "AuthorizationHeaderMalformed"))
        self.assertEqual(self.s3.request("GET").status_code, 200)

    def test_errors_never_leak_secrets_or_internals(self):
        r = self.s3.request("GET", secret="wrong-secret")
        for forbidden in (SK, "wrong-secret", "Traceback", "telegram", "sqlite", "file_id", "chat_id"):
            self.assertNotIn(forbidden.lower(), r.text.lower())

    def test_tampered_request_rejected(self):
        b = self.mkbucket()
        path, query = self.s3.url(b, "k")
        h, *_ = self.s3.sign_headers("PUT", path, query, {}, hashlib.sha256(b"abc").hexdigest())
        h.pop("host")
        r = requests.put(f"{self.server.url}{self.s3.url(b, 'other')[0]}", headers=h, data=b"abc")  # другой ключ
        self.assertEqual(code(r), "SignatureDoesNotMatch")
        r = requests.delete(f"{self.server.url}{path}", headers=h)  # другой метод
        self.assertEqual(code(r), "SignatureDoesNotMatch")

    def test_two_credentials_and_disabled_key(self):
        from polaris.s3.credentials import Credential, StaticCredentialProvider
        self.app.provider = StaticCredentialProvider([Credential(AK, SK), Credential("SECONDKEY", "second-secret"),
                                                      Credential("OLDKEY", "old", enabled=False)])
        self.assertEqual(S3Client(self.server.url, access_key="SECONDKEY", secret="second-secret").request("GET").status_code, 200)
        r = S3Client(self.server.url, access_key="OLDKEY", secret="old").request("GET")
        self.assertEqual(code(r), "InvalidAccessKeyId")


class StreamingBodyTests(S3TestCase):
    def setUp(self):
        super().setUp()
        self.b = self.mkbucket()

    def test_signed_chunks(self):
        data = os.urandom(300_000)
        r = self.s3.put_chunked(self.b, "signed", data)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.s3.get(self.b, "signed").content, data)
        self.assertEqual(r.headers["ETag"], f'"{hashlib.md5(data).hexdigest()}"')
        self.assertNotIn("aws-chunked", self.s3.get(self.b, "signed").headers.get("Content-Encoding", ""))

    def test_signed_chunks_with_trailer_and_unsigned_trailer(self):
        data = os.urandom(150_000)
        for mode in ("STREAMING-AWS4-HMAC-SHA256-PAYLOAD-TRAILER", "STREAMING-UNSIGNED-PAYLOAD-TRAILER"):
            r = self.s3.put_chunked(self.b, mode, data, mode=mode)
            self.assertEqual(r.status_code, 200, (mode, r.text))
            self.assertEqual(self.s3.get(self.b, mode).content, data)

    def test_trailer_checksum_mismatch_creates_nothing(self):
        data = os.urandom(100_000)
        bad = base64.b64encode(b"\0\0\0\1").decode()
        for mode in ("STREAMING-AWS4-HMAC-SHA256-PAYLOAD-TRAILER", "STREAMING-UNSIGNED-PAYLOAD-TRAILER"):
            r = self.s3.put_chunked(self.b, "bad-" + mode, data, mode=mode, trailer_crc32=bad)
            self.assertEqual((r.status_code, code(r)), (400, "BadDigest"), mode)
        keys = [k.text for k in tree(self.s3.get(self.b, "", query="list-type=2")).iter("Key")]
        self.assertEqual(keys, [])
        self.assertEqual(self.driver.blobs, {})

    def test_tampered_chunk_is_rejected(self):
        r = self.s3.put_chunked(self.b, "t", os.urandom(100_000), tamper=True)
        self.assertEqual((r.status_code, code(r)), (403, "SignatureDoesNotMatch"))
        self.assertEqual(self.driver.blobs, {})

    def test_size_mismatch_detected(self):
        data = os.urandom(10_000)
        r = self.s3.put_chunked(self.b, "m", data, headers={"x-amz-decoded-content-length": "9999"})
        self.assertEqual((r.status_code, code(r)), (400, "IncompleteBody"))

    def test_chunked_transfer_encoding_upload(self):
        data = os.urandom(200_000)
        path, query = self.s3.url(self.b, "te")
        h, *_ = self.s3.sign_headers("PUT", path, query, {}, "UNSIGNED-PAYLOAD")
        h.pop("host")
        r = requests.put(self.server.url + path, headers=h, data=(data[i:i + 10_000] for i in range(0, len(data), 10_000)))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.s3.get(self.b, "te").content, data)


class PresignedTests(S3TestCase):
    def setUp(self):
        super().setUp()
        self.b = self.mkbucket()

    def test_presigned_put_and_get_without_secret_in_request(self):
        put_url = self.s3.presign("PUT", self.b, "shared/file name.bin")
        self.assertNotIn(SK, put_url)
        data = os.urandom(120_000)
        r = requests.put(put_url, data=data)  # ни Authorization, ни секрета
        self.assertEqual(r.status_code, 200, r.text)
        get_url = self.s3.presign("GET", self.b, "shared/file name.bin",
                                  extra_query=[("response-content-type", "application/x-test")])
        g = requests.get(get_url)
        self.assertEqual((g.status_code, g.content, g.headers["Content-Type"]), (200, data, "application/x-test"))
        self.assertEqual(requests.get(get_url, headers={"Range": "bytes=10-19"}).content, data[10:20])

    def test_presigned_expired_tampered_and_wrong_method(self):
        self.s3.put(self.b, "k", b"data")
        old = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime("%Y%m%dT%H%M%SZ")
        expired = requests.get(self.s3.presign("GET", self.b, "k", expires=60, amz_date=old))
        self.assertEqual((expired.status_code, code(expired)), (403, "AccessDenied"))
        self.assertIn("expired", tree(expired).findtext("Message").lower())
        url = self.s3.presign("GET", self.b, "k")
        self.assertEqual(code(requests.get(url.replace("/k?", "/other?"))), "SignatureDoesNotMatch")
        self.assertEqual(code(requests.delete(url)), "SignatureDoesNotMatch")  # подпись GET не годится для DELETE
        self.assertEqual(code(requests.get(self.s3.presign("GET", self.b, "k", secret="wrong"))), "SignatureDoesNotMatch")
        self.assertEqual(requests.get(url).content, b"data")

    def test_presigned_put_cannot_overwrite_via_other_key(self):
        url = self.s3.presign("PUT", self.b, "allowed")
        self.assertEqual(requests.put(url.replace("/allowed?", "/forbidden?"), data=b"x").status_code, 403)
        self.assertEqual(code(requests.put(url.replace("/allowed?", "/forbidden?"), data=b"x")), "SignatureDoesNotMatch")


def part_etag(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


class MultipartTests(S3TestCase):
    def setUp(self):
        super().setUp()
        self.b = self.mkbucket()

    def create(self, key="big/file.bin", headers=None):
        r = self.s3.request("POST", self.b, key, query="uploads", headers=headers)
        self.assertEqual(r.status_code, 200, r.text)
        return tree(r).findtext("UploadId")

    def upload(self, uid, number, data, key="big/file.bin"):
        r = self.s3.request("PUT", self.b, key, query=f"partNumber={number}&uploadId={uid}", body=data)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers["ETag"], f'"{part_etag(data)}"')
        return r.headers["ETag"]

    @staticmethod
    def complete_xml(parts):
        body = "".join(f"<Part><PartNumber>{n}</PartNumber><ETag>{e}</ETag></Part>" for n, e in parts)
        return f"<CompleteMultipartUpload>{body}</CompleteMultipartUpload>".encode()

    def test_full_cycle(self):
        uid = self.create(headers={"Content-Type": "application/x-bin", "x-amz-meta-origin": "test"})
        p = [os.urandom(5 * MIB), os.urandom(5 * MIB + 7), os.urandom(123_456)]
        etags = {n: self.upload(uid, n, d) for n, d in ((3, p[2]), (1, p[0]), (2, p[1]))}  # порядок произвольный
        listed = tree(self.s3.request("GET", self.b, "big/file.bin", query=f"uploadId={uid}"))
        self.assertEqual([x.findtext("PartNumber") for x in listed.iter("Part")], ["1", "2", "3"])
        self.assertEqual([x.findtext("Size") for x in listed.iter("Part")], [str(len(d)) for d in p])
        self.assertEqual(self.s3.get(self.b, "big/file.bin").status_code, 404)  # до Complete не виден
        uploads = tree(self.s3.get(self.b, "", query="uploads"))
        self.assertEqual([u.findtext("UploadId") for u in uploads.iter("Upload")], [uid])
        before = self.driver.upload_count
        r = self.s3.request("POST", self.b, "big/file.bin", query=f"uploadId={uid}",
                            body=self.complete_xml([(n, etags[n]) for n in (1, 2, 3)]))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.driver.upload_count, before)  # Complete не отправляет данные в backend
        t = tree(r)
        md5s = b"".join(hashlib.md5(d).digest() for d in p)
        expected_etag = f'"{hashlib.md5(md5s).hexdigest()}-3"'
        self.assertEqual((t.findtext("Key"), t.findtext("ETag")), ("big/file.bin", expected_etag))
        g = self.s3.get(self.b, "big/file.bin")
        self.assertEqual((g.headers["ETag"], g.headers["Content-Type"], g.headers["x-amz-meta-origin"]),
                         (expected_etag, "application/x-bin", "test"))
        data = b"".join(p)
        self.assertEqual(hashlib.sha256(g.content).digest(), hashlib.sha256(data).digest())
        rg = self.s3.get(self.b, "big/file.bin", headers={"Range": f"bytes={5 * MIB - 10}-{5 * MIB + 10}"})
        self.assertEqual(rg.content, data[5 * MIB - 10:5 * MIB + 11])
        self.assertEqual(tree(self.s3.get(self.b, "", query="uploads")).findall("Upload"), [])
        self.assertEqual(self.s3.request("DELETE", self.b, "big/file.bin").status_code, 204)
        self.assertEqual(self.driver.blobs, {})

    def test_abort_frees_everything(self):
        uid = self.create()
        self.upload(uid, 1, os.urandom(5 * MIB))
        self.assertTrue(self.driver.blobs)
        self.assertEqual(self.s3.request("DELETE", self.b, "big/file.bin", query=f"uploadId={uid}").status_code, 204)
        self.assertEqual(self.driver.blobs, {})
        r = self.s3.request("DELETE", self.b, "big/file.bin", query=f"uploadId={uid}")
        self.assertEqual((r.status_code, code(r)), (404, "NoSuchUpload"))
        r = self.s3.request("PUT", self.b, "big/file.bin", query=f"partNumber=1&uploadId={uid}", body=b"x")
        self.assertEqual(code(r), "NoSuchUpload")

    def test_error_cases(self):
        uid = self.create()
        small, last = os.urandom(1000), os.urandom(10)
        e1, e2 = self.upload(uid, 1, small), self.upload(uid, 2, last)
        cases = [
            ([(1, e1), (2, e2)], 400, "EntityTooSmall"),
            ([(2, e2), (1, e1)], 400, "InvalidPartOrder"),
            ([(1, '"deadbeef"')], 400, "InvalidPart"),
            ([(7, e1)], 400, "InvalidPart"),
        ]
        for parts, status, err in cases:
            r = self.s3.request("POST", self.b, "big/file.bin", query=f"uploadId={uid}", body=self.complete_xml(parts))
            self.assertEqual((r.status_code, code(r)), (status, err), err)
        for body in (b"not xml", b"<CompleteMultipartUpload/>", b"<Other/>",
                     b'<!DOCTYPE x [<!ENTITY a "b">]><CompleteMultipartUpload/>'):
            r = self.s3.request("POST", self.b, "big/file.bin", query=f"uploadId={uid}", body=body)
            self.assertEqual((r.status_code, code(r)), (400, "MalformedXML"))
        r = self.s3.request("POST", self.b, "big/file.bin", query="uploadId=" + "0" * 32, body=self.complete_xml([(1, e1)]))
        self.assertEqual((r.status_code, code(r)), (404, "NoSuchUpload"))
        r = self.s3.request("POST", self.b, "wrong/key", query=f"uploadId={uid}", body=self.complete_xml([(1, e1)]))
        self.assertEqual(code(r), "NoSuchUpload")  # upload принадлежит другому ключу
        for number in ("0", "10001", "abc"):
            r = self.s3.request("PUT", self.b, "big/file.bin", query=f"partNumber={number}&uploadId={uid}", body=b"x")
            self.assertEqual((r.status_code, code(r)), (400, "InvalidArgument"), number)
        self.assertEqual(self.s3.request("POST", self.b, "big/file.bin", query=f"uploadId={uid}",
                                         body=self.complete_xml([(2, e2)])).status_code, 200)  # последняя часть может быть малой

    def test_reuploaded_part_replaces_old_and_unlisted_part_dropped(self):
        uid = self.create()
        self.upload(uid, 1, os.urandom(5 * MIB))
        new = os.urandom(5 * MIB + 3)
        e1 = self.upload(uid, 1, new)
        e2 = self.upload(uid, 2, b"tail")
        e3 = self.upload(uid, 3, b"unused")
        r = self.s3.request("POST", self.b, "big/file.bin", query=f"uploadId={uid}", body=self.complete_xml([(1, e1), (2, e2)]))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.s3.get(self.b, "big/file.bin").content, new + b"tail")
        self.assertEqual(self.q_chunks_owned_by_other_objects(), 0)

    def q_chunks_owned_by_other_objects(self):
        rows = self.db.connection().execute(
            "SELECT COUNT(*) FROM storage_objects WHERE bucket_id=(SELECT id FROM storage_buckets WHERE name=?)",
            ("polaris-internal.multipart",)).fetchone()
        return rows[0]

    def test_list_parts_pagination(self):
        uid = self.create()
        for n in (1, 2, 3):
            self.upload(uid, n, bytes([n]) * 10)
        t = tree(self.s3.request("GET", self.b, "big/file.bin", query=f"uploadId={uid}&max-parts=2"))
        self.assertEqual((t.findtext("IsTruncated"), t.findtext("NextPartNumberMarker")), ("true", "2"))
        t = tree(self.s3.request("GET", self.b, "big/file.bin", query=f"uploadId={uid}&part-number-marker=2"))
        self.assertEqual(([x.findtext("PartNumber") for x in t.iter("Part")], t.findtext("IsTruncated")), (["3"], "false"))

    def test_upload_survives_restart(self):
        uid = self.create()
        data = os.urandom(5 * MIB)
        etag = self.upload(uid, 1, data)
        self.server.stop()
        self.app.close()
        self.boot()  # «перезапуск Polaris»: новое ядро, S3 и сервер на той же БД и backend
        listed = tree(self.s3.request("GET", self.b, "big/file.bin", query=f"uploadId={uid}"))
        self.assertEqual([x.findtext("ETag") for x in listed.iter("Part")], [f'"{part_etag(data)}"'])
        e2 = self.upload(uid, 2, b"after restart")
        r = self.s3.request("POST", self.b, "big/file.bin", query=f"uploadId={uid}", body=self.complete_xml([(1, etag), (2, e2)]))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.s3.get(self.b, "big/file.bin").content, data + b"after restart")

    def test_bucket_with_open_upload_cannot_be_deleted(self):
        self.create()
        self.assertEqual(code(self.s3.request("DELETE", self.b)), "BucketNotEmpty")


class DeleteObjectsTests(S3TestCase):
    def test_multi_delete(self):
        b = self.mkbucket()
        for k in ("a", "b", "c d"):
            self.s3.put(b, k, k.encode())
        body = b"<Delete><Object><Key>a</Key></Object><Object><Key>c d</Key></Object><Object><Key>missing</Key></Object></Delete>"
        r = self.s3.request("POST", b, query="delete", body=body)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(sorted(k.text for k in tree(r).iter("Key")), ["a", "c d", "missing"])
        left = [k.text for k in tree(self.s3.get(b, "", query="list-type=2")).iter("Key")]
        self.assertEqual(left, ["b"])
        r = self.s3.request("POST", b, query="delete", body=b"<Delete><Quiet>true</Quiet><Object><Key>b</Key></Object></Delete>")
        self.assertEqual(tree(r).findall("Deleted"), [])
        self.assertEqual(code(self.s3.request("POST", b, query="delete", body=b"junk")), "MalformedXML")


class UnsupportedAndFailureTests(S3TestCase):
    def setUp(self):
        super().setUp()
        self.b = self.mkbucket()
        self.s3.put(self.b, "k", b"x")

    def test_unsupported_operations_return_501_not_fake_success(self):
        cases = [("PUT", self.b, "k", "acl", {}), ("GET", self.b, "k", "tagging", {}), ("GET", self.b, "", "versioning", {}),
                 ("PUT", self.b, "", "policy", {}), ("GET", self.b, "", "lifecycle", {}),
                 ("PUT", self.b, "k2", "", {"x-amz-copy-source": f"/{self.b}/k"}), ("PUT", self.b, "", "cors", {})]
        for method, bucket, key, query, headers in cases:
            r = self.s3.request(method, bucket, key or None, query=query, headers=headers)
            self.assertEqual((r.status_code, code(r)), (501, "NotImplemented"), (method, query, headers))
        self.assertEqual(code(self.s3.request("PATCH", self.b, "k")), "MethodNotAllowed")
        self.assertEqual(self.s3.request("PUT", "bucket-two", headers={"x-amz-acl": "public-read"}).status_code, 501)

    def test_conditional_writes_are_refused_not_ignored(self):
        for header in ("If-None-Match", "If-Match"):
            r = self.s3.put(self.b, "k", b"overwritten", headers={header: "*"})
            self.assertEqual((r.status_code, code(r)), (501, "NotImplemented"), header)
        self.assertEqual(self.s3.get(self.b, "k").content, b"x")  # объект не перезаписан

    def test_storage_unavailable_returns_503_without_details(self):
        def broken():
            raise StorageUnavailable("секретная причина: токен 123:ABC")
        self.get_core = broken
        for r in (self.s3.request("GET"), self.s3.get(self.b, "k"), self.s3.put(self.b, "n", b"x")):
            self.assertEqual((r.status_code, code(r)), (503, "ServiceUnavailable"))
            self.assertNotIn("123:ABC", r.text)

    def test_backend_failure_gives_503_and_leaves_no_object(self):
        def boom(data, ctx):
            raise TransientBackendError("Telegram sendDocument HTTP 502 chat_id=-100123")
        self.driver.upload_hook = boom
        r = self.s3.put(self.b, "new", os.urandom(100_000))
        self.assertEqual((r.status_code, code(r)), (503, "ServiceUnavailable"))
        for secret in ("Telegram", "chat_id", "502", "-100123"):
            self.assertNotIn(secret, r.text)
        self.driver.upload_hook = None
        self.assertEqual(self.s3.get(self.b, "new").status_code, 404)
        self.assertEqual(self.s3.put(self.b, "new", b"fine").status_code, 200)  # повтор после восстановления

    def test_upload_limit_gives_slowdown(self):
        self.core_kw = {"max_concurrent_uploads": 1}
        self.boot()
        b = self.mkbucket("bucket2")
        gate, started = [False], []

        def slow(data, ctx):
            started.append(1)
            while not gate[0]:
                time.sleep(0.01)
        self.driver.upload_hook = slow
        results = []
        t = __import__("threading").Thread(target=lambda: results.append(self.s3.put(b, "slow", os.urandom(100_000))))
        t.start()
        while not started:
            time.sleep(0.01)
        r = self.s3.put(b, "second", b"x" * 10)
        self.assertEqual((r.status_code, code(r)), (503, "SlowDown"))
        gate[0] = True
        t.join()
        self.assertEqual(results[0].status_code, 200)

    def test_event_loop_not_blocked_by_slow_backend(self):
        gate, started = [False], []

        def slow(data, ctx):
            started.append(1)
            while not gate[0]:
                time.sleep(0.01)
        self.driver.upload_hook = slow
        t = __import__("threading").Thread(target=lambda: self.s3.put(self.b, "slow", os.urandom(100_000)))
        t.start()
        while not started:
            time.sleep(0.01)
        start = time.monotonic()
        self.assertEqual(self.s3.request("HEAD", self.b).status_code, 200)
        self.assertLess(time.monotonic() - start, 0.5)
        gate[0] = True
        t.join()

    def test_client_disconnect_mid_upload_aborts_cleanly(self):
        baseline = dict(self.driver.blobs)  # объект "k" из setUp остаётся
        path, query = self.s3.url(self.b, "cut")
        h, *_ = self.s3.sign_headers("PUT", path, query, {"content-length": "500000"}, "UNSIGNED-PAYLOAD")
        sock = socket.create_connection(("127.0.0.1", self.server.port))
        head = f"PUT {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in h.items()) + "\r\n"
        sock.sendall(head.encode() + os.urandom(150_000))
        time.sleep(0.3)
        sock.close()
        for _ in range(100):
            states = {r[0] for r in self.db.connection().execute("SELECT status FROM storage_objects WHERE object_key='cut'")}
            if states == {"failed"} and self.driver.blobs == baseline:
                break
            time.sleep(0.05)
        self.assertEqual(states, {"failed"})
        self.assertEqual(self.driver.blobs, baseline)
        self.assertEqual(self.s3.get(self.b, "cut").status_code, 404)
        self.assertEqual(self.s3.put(self.b, "next", b"slot released").status_code, 200)

    def test_oversized_request_rejected_before_reading_body(self):
        self.config_kw = {"max_request_size": 1000}
        self.boot()
        b = self.mkbucket("bucket3")
        r = self.s3.put(b, "big", b"x" * 5000)
        self.assertEqual((r.status_code, code(r)), (400, "EntityTooLarge"))
        self.assertEqual(self.s3.put(b, "small", b"x" * 500).status_code, 200)

    def test_wrong_prefix_and_root_forms(self):
        self.assertEqual(requests.get(f"{self.server.url}/other/path").status_code, 404)
        for path in ("/s3", "/s3/"):
            h, *_ = self.s3.sign_headers("GET", path, "", {}, hashlib.sha256(b"").hexdigest())
            h.pop("host")
            self.assertEqual(requests.get(self.server.url + path, headers=h).status_code, 200, path)

    def test_concurrent_uploads_and_downloads(self):
        import concurrent.futures as cf
        payloads = {f"k{i}": os.urandom(200_000 + i) for i in range(8)}
        with cf.ThreadPoolExecutor(8) as pool:
            codes = list(pool.map(lambda kv: S3Client(self.server.url).put(self.b, *kv).status_code, payloads.items()))
            self.assertEqual(codes, [200] * 8)
            got = list(pool.map(lambda k: S3Client(self.server.url).get(self.b, k).content, payloads))
        self.assertEqual(got, list(payloads.values()))


@unittest.skipUnless(shutil.which("curl") and "aws-sigv4" in subprocess.run(["curl", "--help", "all"], capture_output=True, text=True).stdout,
                     "нужен curl с поддержкой --aws-sigv4")
class CurlSigV4Tests(S3TestCase):
    """curl — независимая реализация SigV4 (не наш код подписи)."""

    def curl(self, *args, input_bytes=None):
        cmd = ["curl", "-sS", "-i", "--aws-sigv4", "aws:amz:us-east-1:s3", "--user", f"{AK}:{SK}", *args]
        out = subprocess.run(cmd, capture_output=True, input=input_bytes, timeout=60).stdout
        head, _, body = out.partition(b"\r\n\r\n")
        status = int(head.split(b" ", 2)[1])
        return status, head.decode("latin-1"), body

    def test_curl_roundtrip(self):
        base = f"{self.server.url}/s3"
        data = os.urandom(200_000)
        digest = hashlib.sha256(data).hexdigest()
        self.assertEqual(self.curl("-X", "PUT", f"{base}/curlbucket")[0], 200)
        status, _, _ = self.curl("-X", "PUT", "--data-binary", "@-", "-H", f"x-amz-content-sha256: {digest}",
                                 "-H", "Content-Type: application/octet-stream", f"{base}/curlbucket/dir/file%20one.bin",
                                 input_bytes=data)
        self.assertEqual(status, 200)
        status, head, body = self.curl(f"{base}/curlbucket/dir/file%20one.bin", "-H", f"x-amz-content-sha256: UNSIGNED-PAYLOAD")
        self.assertEqual((status, body), (200, data))
        status, _, body = self.curl(f"{base}/curlbucket?list-type=2&prefix=dir%2F", "-H", "x-amz-content-sha256: UNSIGNED-PAYLOAD")
        self.assertEqual((status, [k.text for k in tree(type("R", (), {"content": body})).iter("Key")]), (200, ["dir/file one.bin"]))
        status, _, body = self.curl(f"{base}/curlbucket/dir/file%20one.bin", "-H", "x-amz-content-sha256: UNSIGNED-PAYLOAD",
                                    "-H", "Range: bytes=100-199")
        self.assertEqual((status, body), (206, data[100:200]))
        status, _, _ = self.curl(f"{base}/curlbucket", "--aws-sigv4", "aws:amz:us-east-1:s3",
                                 "-H", "x-amz-content-sha256: UNSIGNED-PAYLOAD", "-X", "DELETE")
        self.assertEqual(status, 409)  # непустой bucket
        self.assertEqual(self.curl("-X", "DELETE", f"{base}/curlbucket/dir/file%20one.bin", "-H", "x-amz-content-sha256: UNSIGNED-PAYLOAD")[0], 204)
        self.assertEqual(self.curl("-X", "DELETE", f"{base}/curlbucket", "-H", "x-amz-content-sha256: UNSIGNED-PAYLOAD")[0], 204)

    def test_curl_wrong_secret(self):
        cmd = ["curl", "-sS", "-i", "--aws-sigv4", "aws:amz:us-east-1:s3", "--user", f"{AK}:nope", f"{self.server.url}/s3/"]
        out = subprocess.run(cmd, capture_output=True, timeout=30).stdout
        self.assertIn(b"403", out.split(b"\r\n", 1)[0])
        self.assertIn(b"SignatureDoesNotMatch", out)

    def test_curl_fetches_presigned_urls(self):
        self.mkbucket("presign-bucket")
        put_url = self.s3.presign("PUT", "presign-bucket", "via curl.txt")
        subprocess.run(["curl", "-sS", "-X", "PUT", "--data-binary", "curl body", put_url], capture_output=True, timeout=30)
        out = subprocess.run(["curl", "-sS", self.s3.presign("GET", "presign-bucket", "via curl.txt")],
                             capture_output=True, timeout=30).stdout
        self.assertEqual(out, b"curl body")


if __name__ == "__main__":
    unittest.main()
