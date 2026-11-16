"""S3: права ключей Access Management проверяются на каждой операции (в т.ч. по presigned URL), отзыв и ротация."""
from __future__ import annotations

import os
import unittest

import requests

from polaris.s3.api import S3App
from polaris.s3.config import S3Config
from polaris.s3.credentials import (
    CompositeCredentialProvider, Credential, DatabaseCredentialProvider, StaticCredentialProvider,
)
from polaris.s3.service import S3Service
from polaris.storage.access_keys import AccessKeyStore, MasterKey
from tests.asgi_server import AsgiTestServer
from tests.s3_client import AK, SK, S3Client
from tests.test_s3_api import MIB, S3TestCase, code, tree

ALL = ["ListBucket", "ReadObject", "WriteObject", "DeleteObject", "MultipartUpload"]


class KeyedS3TestCase(S3TestCase):
    """S3 с ключами из конфигурации (AK/SK, полный доступ) и из Access Management."""
    store_available = True

    def boot(self):
        super().boot()
        self.master = MasterKey(b"m" * 32)
        self.store = AccessKeyStore(self.db, self.master)
        provider = CompositeCredentialProvider(
            StaticCredentialProvider([Credential(AK, SK)]),
            DatabaseCredentialProvider(lambda: self.store if self.store_available else None))
        self.app.close()
        self.server.stop()
        self.app = S3App(S3Config(enabled=True, path_prefix="/s3"), provider, S3Service(lambda: self.core))
        self.server = AsgiTestServer(self.app).start()
        self.addCleanup(self.app.close)
        self.addCleanup(self.server.stop)
        self.s3 = S3Client(self.server.url)  # админ-ключ из конфигурации

    def key(self, permissions, buckets, name="k"):
        key, secret = self.store.create(name, "", permissions, buckets)
        return key, S3Client(self.server.url, access_key=key.access_key_id, secret=secret)

    def seed(self, bucket="alpha-bucket", key="dir/obj.txt", data=b"payload"):
        self.s3.request("PUT", bucket)
        self.s3.put(bucket, key, data)


class PermissionMatrix(KeyedS3TestCase):
    def setUp(self):
        super().setUp()
        self.seed("alpha-bucket")
        self.seed("beta-bucket")

    def status(self, r):
        return r.status_code, (code(r) if r.status_code >= 400 and r.content else "")

    def test_read_only_key(self):
        _, c = self.key(["ListBucket", "ReadObject"], ["alpha-bucket"])
        self.assertEqual(c.get("alpha-bucket", "dir/obj.txt").content, b"payload")
        self.assertEqual(c.request("HEAD", "alpha-bucket", "dir/obj.txt").status_code, 200)
        self.assertEqual(c.request("HEAD", "alpha-bucket").status_code, 200)
        self.assertEqual(c.get("alpha-bucket", "", query="list-type=2").status_code, 200)
        self.assertEqual(c.get("alpha-bucket", "", query="location").status_code, 200)
        denied = {
            "put": c.put("alpha-bucket", "new", b"x"), "delete": c.request("DELETE", "alpha-bucket", "dir/obj.txt"),
            "multi-create": c.request("POST", "alpha-bucket", "m", query="uploads"),
            "list-uploads": c.get("alpha-bucket", "", query="uploads"),
            "delete-objects": c.request("POST", "alpha-bucket", query="delete",
                                        body=b"<Delete><Object><Key>dir/obj.txt</Key></Object></Delete>"),
            "create-bucket": c.request("PUT", "gamma-bucket"), "delete-bucket": c.request("DELETE", "alpha-bucket"),
        }
        for name, r in denied.items():
            self.assertEqual(self.status(r), (403, "AccessDenied"), name)
        self.assertEqual(self.s3.get("alpha-bucket", "dir/obj.txt").content, b"payload")  # ничего не изменилось

    def test_write_only_key_cannot_read_or_list(self):
        _, c = self.key(["WriteObject"], ["alpha-bucket"])
        self.assertEqual(c.put("alpha-bucket", "w.txt", b"w").status_code, 200)
        for r in (c.get("alpha-bucket", "w.txt"), c.get("alpha-bucket", "", query="list-type=2"),
                  c.request("HEAD", "alpha-bucket", "w.txt"), c.request("DELETE", "alpha-bucket", "w.txt")):
            self.assertEqual(self.status(r), (403, "AccessDenied") if r.content else (403, ""))
        self.assertEqual(self.s3.get("alpha-bucket", "w.txt").content, b"w")

    def test_delete_permission_is_separate_from_write(self):
        _, writer = self.key(["WriteObject"], ["alpha-bucket"])
        _, deleter = self.key(["DeleteObject"], ["alpha-bucket"])
        self.assertEqual(writer.put("alpha-bucket", "dir/obj.txt", b"overwritten").status_code, 200)  # перезапись = запись
        self.assertEqual(writer.request("DELETE", "alpha-bucket", "dir/obj.txt").status_code, 403)
        self.assertEqual(deleter.get("alpha-bucket", "dir/obj.txt").status_code, 403)
        body = b"<Delete><Object><Key>dir/obj.txt</Key></Object></Delete>"
        self.assertEqual(writer.request("POST", "alpha-bucket", query="delete", body=body).status_code, 403)
        self.assertEqual(deleter.request("POST", "alpha-bucket", query="delete", body=body).status_code, 200)
        self.assertEqual(self.s3.get("alpha-bucket", "dir/obj.txt").status_code, 404)

    def test_bucket_scope_for_every_operation(self):
        _, c = self.key(ALL, ["alpha-bucket"])
        for bucket in ("beta-bucket", "absent-bucket"):  # чужой и несуществующий неразличимы
            for r in (c.get(bucket, "dir/obj.txt"), c.put(bucket, "k", b"x"), c.get(bucket, "", query="list-type=2"),
                      c.request("HEAD", bucket), c.request("DELETE", bucket, "dir/obj.txt"),
                      c.request("POST", bucket, "m", query="uploads"), c.get(bucket, "", query="uploads"),
                      c.request("POST", bucket, query="delete", body=b"<Delete><Object><Key>a</Key></Object></Delete>")):
                self.assertEqual(r.status_code, 403, bucket)
                if r.content:
                    self.assertEqual(code(r), "AccessDenied")
        self.assertEqual(self.s3.get("beta-bucket", "dir/obj.txt").content, b"payload")

    def test_list_buckets_is_filtered(self):
        names = lambda client: sorted(b.findtext("Name") for b in tree(client.request("GET")).iter("Bucket"))  # noqa: E731
        _, one = self.key(["ReadObject"], ["beta-bucket"])
        _, both = self.key(["ListBucket"], ["alpha-bucket", "beta-bucket"])
        _, everything = self.key(["ReadObject"], None)
        self.assertEqual(names(one), ["beta-bucket"])
        self.assertEqual(names(both), ["alpha-bucket", "beta-bucket"])
        self.assertEqual(names(everything), ["alpha-bucket", "beta-bucket"])
        self.assertEqual(names(self.s3), ["alpha-bucket", "beta-bucket"])

    def test_all_buckets_key_with_limited_permissions(self):
        _, c = self.key(["ReadObject"], None)
        self.assertEqual(c.get("beta-bucket", "dir/obj.txt").content, b"payload")
        self.assertEqual(c.put("beta-bucket", "k", b"x").status_code, 403)

    def test_bucket_management_is_reserved_for_config_keys(self):
        _, full = self.key(ALL, None)  # даже «все права на все buckets» не включает создание/удаление bucket-ов
        self.assertEqual(full.request("PUT", "gamma-bucket").status_code, 403)
        self.assertEqual(full.request("DELETE", "beta-bucket").status_code, 403)
        self.assertEqual(self.s3.request("PUT", "gamma-bucket").status_code, 200)
        self.assertEqual(self.s3.request("DELETE", "gamma-bucket").status_code, 204)

    def test_unsupported_operation_still_requires_permission(self):
        _, c = self.key(["ReadObject"], ["alpha-bucket"])
        r = c.request("PUT", "alpha-bucket", "k2", headers={"x-amz-copy-source": "/alpha-bucket/dir/obj.txt"})
        self.assertEqual(r.status_code, 403)  # 403, а не 501: неавторизованному не раскрываем особенности сервера

    def test_config_key_keeps_full_access(self):
        self.assertEqual(self.s3.get("beta-bucket", "dir/obj.txt").status_code, 200)
        self.assertEqual(self.s3.put("beta-bucket", "new", b"1").status_code, 200)
        self.assertEqual(self.s3.request("DELETE", "beta-bucket", "new").status_code, 204)


class MultipartPermissions(KeyedS3TestCase):
    def setUp(self):
        super().setUp()
        self.seed("alpha-bucket")
        self.seed("beta-bucket")

    def test_multipart_needs_write_and_multipart(self):
        _, write_only = self.key(["WriteObject"], ["alpha-bucket"])
        self.assertEqual(write_only.request("POST", "alpha-bucket", "big", query="uploads").status_code, 403)
        _, mp = self.key(["WriteObject", "MultipartUpload"], ["alpha-bucket"])
        r = mp.request("POST", "alpha-bucket", "big", query="uploads")
        self.assertEqual(r.status_code, 200)
        uid = tree(r).findtext("UploadId")
        data = os.urandom(5 * MIB)
        up = mp.request("PUT", "alpha-bucket", "big", query=f"partNumber=1&uploadId={uid}", body=data)
        self.assertEqual(up.status_code, 200)
        body = f"<CompleteMultipartUpload><Part><PartNumber>1</PartNumber><ETag>{up.headers['ETag']}</ETag></Part></CompleteMultipartUpload>".encode()
        self.assertEqual(mp.request("POST", "alpha-bucket", "big", query=f"uploadId={uid}", body=body).status_code, 200)
        self.assertEqual(self.s3.get("alpha-bucket", "big").content, data)

    def test_cannot_use_foreign_upload_ids(self):
        r = self.s3.request("POST", "beta-bucket", "secret", query="uploads")  # админ начал загрузку в чужом bucket-е
        uid = tree(r).findtext("UploadId")
        self.s3.request("PUT", "beta-bucket", "secret", query=f"partNumber=1&uploadId={uid}", body=b"hidden")
        _, mp = self.key(["ListBucket", "ReadObject", "WriteObject", "MultipartUpload", "DeleteObject"], ["alpha-bucket"])
        # через путь «своего» bucket-а чужая загрузка не находится
        for method, query, body in (("PUT", f"partNumber=2&uploadId={uid}", b"x"), ("DELETE", f"uploadId={uid}", b""),
                                    ("GET", f"uploadId={uid}", b""),
                                    ("POST", f"uploadId={uid}", b"<CompleteMultipartUpload><Part><PartNumber>1</PartNumber><ETag>x</ETag></Part></CompleteMultipartUpload>")):
            resp = mp.request(method, "alpha-bucket", "secret", query=query, body=body)
            self.assertEqual((resp.status_code, code(resp)), (404, "NoSuchUpload"), method)
        # через путь чужого bucket-а — нет прав
        for method, query in (("PUT", f"partNumber=2&uploadId={uid}"), ("DELETE", f"uploadId={uid}"), ("GET", f"uploadId={uid}")):
            self.assertEqual(mp.request(method, "beta-bucket", "secret", query=query).status_code, 403)
        # список загрузок не раскрывает чужие
        listing = tree(mp.get("alpha-bucket", "", query="uploads"))
        self.assertEqual(listing.findall("Upload"), [])
        self.assertEqual(len(tree(self.s3.get("beta-bucket", "", query="uploads")).findall("Upload")), 1)

    def test_abort_and_list_parts_need_multipart_permission(self):
        uid = tree(self.s3.request("POST", "alpha-bucket", "m", query="uploads")).findtext("UploadId")
        _, reader = self.key(["ReadObject", "ListBucket"], ["alpha-bucket"])
        for method in ("GET", "DELETE"):
            self.assertEqual(reader.request(method, "alpha-bucket", "m", query=f"uploadId={uid}").status_code, 403)
        _, mp = self.key(["WriteObject", "MultipartUpload"], ["alpha-bucket"])
        self.assertEqual(mp.get("alpha-bucket", "m", query=f"uploadId={uid}").status_code, 200)
        self.assertEqual(mp.request("DELETE", "alpha-bucket", "m", query=f"uploadId={uid}").status_code, 204)


class PresignedPermissions(KeyedS3TestCase):
    def setUp(self):
        super().setUp()
        self.seed("alpha-bucket")
        self.seed("beta-bucket")

    def test_presigned_urls_obey_the_signing_keys_permissions(self):
        _, reader = self.key(["ReadObject"], ["alpha-bucket"])
        self.assertEqual(requests.get(reader.presign("GET", "alpha-bucket", "dir/obj.txt")).content, b"payload")
        r = requests.get(reader.presign("GET", "beta-bucket", "dir/obj.txt"))  # другой bucket
        self.assertEqual((r.status_code, code(r)), (403, "AccessDenied"))
        r = requests.put(reader.presign("PUT", "alpha-bucket", "up.txt"), data=b"x")  # нет WriteObject
        self.assertEqual((r.status_code, code(r)), (403, "AccessDenied"))
        self.assertEqual(self.s3.get("alpha-bucket", "up.txt").status_code, 404)
        _, writer = self.key(["WriteObject"], ["alpha-bucket"])
        self.assertEqual(requests.put(writer.presign("PUT", "alpha-bucket", "up.txt"), data=b"ok").status_code, 200)
        self.assertEqual(requests.get(writer.presign("GET", "alpha-bucket", "up.txt")).status_code, 403)

    def test_issued_presigned_url_dies_with_the_key(self):
        key, c = self.key(["ReadObject"], ["alpha-bucket"])
        url = c.presign("GET", "alpha-bucket", "dir/obj.txt", expires=3600)
        self.assertEqual(requests.get(url).status_code, 200)
        self.store.revoke(key.id)
        r = requests.get(url)
        self.assertEqual((r.status_code, code(r)), (403, "InvalidAccessKeyId"))


class RevocationAndRotation(KeyedS3TestCase):
    def setUp(self):
        super().setUp()
        self.seed("alpha-bucket")

    def test_revoked_key_stops_immediately(self):
        key, c = self.key(ALL, ["alpha-bucket"])
        self.assertEqual(c.get("alpha-bucket", "dir/obj.txt").status_code, 200)
        self.store.revoke(key.id)
        for r in (c.get("alpha-bucket", "dir/obj.txt"), c.request("GET"), c.put("alpha-bucket", "n", b"x")):
            self.assertEqual((r.status_code, code(r)), (403, "InvalidAccessKeyId"))
        self.assertEqual(self.s3.get("alpha-bucket", "dir/obj.txt").status_code, 200)  # другие ключи не затронуты

    def test_rotation_invalidates_old_secret_only(self):
        key, c = self.key(ALL, ["alpha-bucket"])
        _, new_secret = self.store.rotate(key.id)
        r = c.get("alpha-bucket", "dir/obj.txt")  # старый секрет
        self.assertEqual((r.status_code, code(r)), (403, "SignatureDoesNotMatch"))
        fresh = S3Client(self.server.url, access_key=key.access_key_id, secret=new_secret)
        self.assertEqual(fresh.get("alpha-bucket", "dir/obj.txt").status_code, 200)

    def test_foreign_master_key_makes_keys_unusable_until_rotated(self):
        key, c = self.key(ALL, ["alpha-bucket"])
        self.store = AccessKeyStore(self.db, MasterKey(b"z" * 32))  # мастер-ключ потерян/заменён
        r = c.get("alpha-bucket", "dir/obj.txt")
        self.assertEqual((r.status_code, code(r)), (403, "InvalidAccessKeyId"))
        _, secret = self.store.rotate(key.id)
        self.assertEqual(S3Client(self.server.url, access_key=key.access_key_id, secret=secret).get("alpha-bucket", "dir/obj.txt").status_code, 200)

    def test_last_used_is_recorded_on_success_only(self):
        key, c = self.key(ALL, ["alpha-bucket"])
        bad = S3Client(self.server.url, access_key=key.access_key_id, secret="wrong")
        self.assertEqual(bad.get("alpha-bucket", "dir/obj.txt").status_code, 403)
        self.assertIsNone(self.store.get(key.id).last_used_at)
        c.get("alpha-bucket", "dir/obj.txt")
        self.assertIsNotNone(self.store.get(key.id).last_used_at)

    def test_unknown_key_is_invalid_access_key(self):
        r = S3Client(self.server.url, access_key="PLRSUNKNOWN0000000", secret="x" * 40).request("GET")
        self.assertEqual((r.status_code, code(r)), (403, "InvalidAccessKeyId"))

    def test_access_management_unavailable_gives_503_but_config_keys_work(self):
        _, c = self.key(ALL, ["alpha-bucket"])
        self.store_available = False
        r = c.get("alpha-bucket", "dir/obj.txt")
        self.assertEqual((r.status_code, code(r)), (503, "ServiceUnavailable"))
        self.assertEqual(self.s3.get("alpha-bucket", "dir/obj.txt").status_code, 200)

    def test_event_loop_not_blocked_by_key_lookup(self):
        _, c = self.key(ALL, ["alpha-bucket"])
        import threading
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(6) as pool:
            codes = list(pool.map(lambda _: S3Client(self.server.url, access_key=c.ak, secret=c.sk)
                                  .get("alpha-bucket", "dir/obj.txt").status_code, range(12)))
        self.assertEqual(set(codes), {200})


if __name__ == "__main__":
    unittest.main()
