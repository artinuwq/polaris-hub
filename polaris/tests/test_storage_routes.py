"""Маршруты /api/storage/*: все только для админа, S3-ключи доступа не дают, ошибки и секреты."""
from __future__ import annotations

import json
import types
import unittest
from unittest import mock

from tests import fastapi_stub

# роутер и require_admin импортируются поверх заглушки FastAPI (в т.ч. если настоящий установлен):
# тестируется логика маршрутов и защиты, а не фреймворк
auth, api = fastapi_stub.load_stubbed()

from polaris.modules.storage.admin import AdminError  # noqa: E402
from polaris.storage.errors import StorageError  # noqa: E402
from tests.test_storage_admin import AdminTestCase  # noqa: E402

TOKEN = "admin-token-123"
ADMIN = {"X-Polaris-Token": TOKEN}
PUBLIC_PREFIX = "/api/storage/download/"
EXPECTED = {
    ("GET", "/api/storage/status"), ("GET", "/api/storage/config"), ("PUT", "/api/storage/config"),
    ("POST", "/api/storage/test"), ("GET", "/api/storage/buckets"), ("POST", "/api/storage/buckets"),
    ("GET", "/api/storage/buckets/{bucket}"), ("GET", "/api/storage/buckets/{bucket}/objects"),
    ("DELETE", "/api/storage/buckets/{bucket}"), ("GET", "/api/storage/objects/{object_id}"),
    ("DELETE", "/api/storage/objects/{object_id}"), ("GET", "/api/storage/objects/{object_id}/download"),
    ("GET", "/api/storage/credentials"), ("POST", "/api/storage/credentials"),
    ("POST", "/api/storage/credentials/{credential_id}/revoke"), ("POST", "/api/storage/credentials/{credential_id}/rotate"),
    ("PATCH", "/api/storage/credentials/{credential_id}"), ("DELETE", "/api/storage/credentials/{credential_id}"),
    ("GET", "/api/storage/diagnostics"), ("POST", "/api/storage/diagnostics/run"), ("GET", "/api/storage/multipart"),
    ("POST", "/api/storage/multipart/{upload_id}/abort"),
    ("POST", "/api/storage/buckets/{bucket}/multipart"), ("PUT", "/api/storage/multipart/{upload_id}/parts/{part_number}"),
    ("POST", "/api/storage/multipart/{upload_id}/complete"),
    ("POST", "/api/storage/objects/{object_id}/download-link"), ("GET", "/api/storage/download/{token}"),
    ("GET", "/api/storage/buckets/{bucket}/browse"), ("POST", "/api/storage/buckets/{bucket}/folders"),
    ("DELETE", "/api/storage/buckets/{bucket}/folders"), ("PUT", "/api/storage/buckets/{bucket}/upload"),
}


def concrete(path: str) -> str:
    return path.replace("{bucket}", "files").replace("{object_id}", "oid").replace("{credential_id}", "cid").replace("{upload_id}", "u" * 32).replace("{token}", "tok").replace("{part_number}", "1")


class RecordingAdmin:
    def __getattr__(self, name):
        def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if name == "object_download":
                return {"key": 'dir/ünï"c\r\node.txt', "content_type": "text/plain", "size": 3}, iter([b"abc"])
            return {"echo": name}
        return method

    def __init__(self):
        self.calls = []


class AdminProtectionTests(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.multiple(auth.settings, update_api_token=TOKEN, telegram_bot_token="123456:ABCDEF", debug=False)
        patch.start()
        self.addCleanup(patch.stop)
        self.fake = RecordingAdmin()
        api._admin = self.fake
        self.addCleanup(setattr, api, "_admin", None)

    def test_all_specified_routes_exist(self):
        have = {(m, p) for m, p, _ in api.router.routes}
        self.assertEqual(have, EXPECTED)

    def test_every_route_requires_admin(self):
        s3_like = {"Authorization": "AWS4-HMAC-SHA256 Credential=PLRSABC/20261115/us-east-1/s3/aws4_request, "
                                    "SignedHeaders=host, Signature=" + "0" * 64,
                   "x-amz-date": "20261115T000000Z", "x-amz-content-sha256": "UNSIGNED-PAYLOAD"}
        for method, path, _ in api.router.routes:
            if path.startswith(PUBLIC_PREFIX):  # единственный маршрут без admin-заголовков: защищён подписью, см. PublicDownloadTests
                continue
            url = concrete(path)
            for label, headers in (("без учётных данных", {}), ("S3-подпись", s3_like), ("неверный токен", {"X-Polaris-Token": "x"}),
                                   ("S3-секрет в роли токена", {"X-Polaris-Token": "S" * 40}),
                                   ("битый initData", {"X-Telegram-Init-Data": "query_id=1&hash=00"})):
                status, _, _ = fastapi_stub.call(api.router, method, url, headers=headers, body={})
                self.assertIn(status, (401, 403), f"{method} {url}: {label}")
            self.assertEqual(self.fake.calls, [], f"{method} {url}: обработчик вызван без авторизации")

    def test_admin_token_passes_through_to_handlers(self):
        for method, path, _ in api.router.routes:
            if path.endswith(("/download", "/upload")) or "/parts/" in path or path.startswith(PUBLIC_PREFIX):  # ответ-поток / тело запроса: свои тесты ниже
                continue
            status, payload, _ = fastapi_stub.call(api.router, method, concrete(path), headers=ADMIN, body={})
            self.assertEqual(status, 200, f"{method} {path}")
            self.assertTrue(payload["success"])

    def test_unconfigured_server_does_not_open_admin_api(self):
        with mock.patch.multiple(auth.settings, update_api_token="", telegram_bot_token="123:abc", debug=False):
            self.assertEqual(fastapi_stub.call(api.router, "GET", "/api/storage/status", headers={"X-Polaris-Token": ""})[0], 401)

    def test_arguments_reach_the_admin_layer_unchanged(self):
        call = lambda m, p, **kw: fastapi_stub.call(api.router, m, p, headers=ADMIN, **kw)  # noqa: E731
        call("PUT", "/api/storage/config", body={"changes": {"s3_region": "eu-west-1"}, "confirm": True})
        call("PUT", "/api/storage/config", body={"changes": {"s3_region": "eu-west-1"}, "confirm": "true"})
        call("PUT", "/api/storage/config", body={"changes": {"s3_region": "eu-west-1"}})
        confirms = [kw if False else args[1] for name, args, _ in self.fake.calls if name == "config_update"]
        self.assertEqual(confirms, [True, False, False])  # подтверждением считается только булево true
        self.fake.calls.clear()
        call("POST", "/api/storage/buckets", body={"name": "my-bucket"})
        call("GET", "/api/storage/buckets/files/objects", query={"prefix": "a/", "after": "a/b", "limit": "7"})
        call("POST", "/api/storage/test", body={"steps": ["config"], "use_saved": False})
        call("POST", "/api/storage/diagnostics/run", body={"checks": ["s3"]})
        names = [(n, a) for n, a, _ in self.fake.calls]
        self.assertEqual(names, [("bucket_create", ("my-bucket",)), ("bucket_objects", ("files", "a/", "a/b", 7)),
                                 ("run_tests", (["config"], False)), ("run_checks", (["s3"],))])
        self.assertEqual(call("GET", "/api/storage/buckets/files/objects", query={"limit": "9999"})[0], 422)

    def test_secret_responses_are_not_cacheable(self):
        _, _, headers = fastapi_stub.call(api.router, "POST", "/api/storage/credentials", headers=ADMIN, body={})
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        _, _, headers = fastapi_stub.call(api.router, "POST", "/api/storage/credentials/cid/rotate", headers=ADMIN)
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        _, _, headers = fastapi_stub.call(api.router, "GET", "/api/storage/credentials", headers=ADMIN)
        self.assertNotIn("Cache-Control", headers)

    def test_file_manager_arguments_reach_the_admin_layer(self):
        call = lambda m, p, **kw: fastapi_stub.call(api.router, m, p, headers=ADMIN, **kw)  # noqa: E731
        call("GET", "/api/storage/buckets/files/browse", query={"prefix": "a/", "cursor": "a/b", "limit": "7"})
        call("POST", "/api/storage/buckets/files/folders", body={"prefix": "docs/"})
        call("DELETE", "/api/storage/buckets/files/folders", query={"prefix": "docs/"})
        self.assertEqual([(n, a) for n, a, _ in self.fake.calls],
                         [("bucket_browse", ("files", "a/", "a/b", 7)), ("folder_create", ("files", "docs/")),
                          ("folder_delete", ("files", "docs/"))])
        self.assertEqual(call("GET", "/api/storage/buckets/files/browse", query={"limit": "9999"})[0], 422)

    def test_download_headers_cannot_be_injected(self):
        status, response, _ = fastapi_stub.call(api.router, "GET", "/api/storage/objects/oid/download", headers=ADMIN)
        disposition = response.headers["Content-Disposition"]
        self.assertNotIn("\r", disposition)
        self.assertNotIn("\n", disposition)
        self.assertIn("filename*=UTF-8''", disposition)
        self.assertEqual((response.media_type, response.headers["Content-Length"], response.headers["Cache-Control"]),
                         ("text/plain", "3", "no-store"))
        self.assertEqual(b"".join(response.body_iterator), b"abc")


class UploadAdmin:
    settings = types.SimpleNamespace(s3_max_request_size_mib=1)

    def __init__(self):
        self.seen = None

    def object_upload(self, bucket, key, source, mime_type=None):
        data = source.read()
        self.seen = (bucket, key, mime_type, data)
        return {"key": key, "size": len(data)}


class UploadRouteTests(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.multiple(auth.settings, update_api_token=TOKEN, debug=False)
        patch.start()
        self.addCleanup(patch.stop)
        self.admin = UploadAdmin()
        api._admin = self.admin
        self.addCleanup(setattr, api, "_admin", None)

    def put(self, body: bytes, key="dir/a.bin", **headers):
        return fastapi_stub.call(api.router, "PUT", "/api/storage/buckets/files/upload", headers={**ADMIN, **headers},
                                 query={"key": key}, raw=body)

    def test_body_is_streamed_to_the_admin_layer(self):
        body = bytes(range(256)) * 1000  # несколько кусков потока
        status, payload, _ = self.put(body, **{"Content-Type": "text/plain; charset=utf-8"})
        self.assertEqual((status, payload["data"]["size"]), (200, len(body)))
        self.assertEqual(self.admin.seen, ("files", "dir/a.bin", "text/plain", body))

    def test_generic_content_type_is_left_for_the_server_to_guess(self):
        self.put(b"x", **{"Content-Type": "application/octet-stream"})
        self.assertIsNone(self.admin.seen[2])

    def test_empty_file_is_allowed(self):
        self.assertEqual(self.put(b"")[0], 200)
        self.assertEqual(self.admin.seen[3], b"")

    def test_size_limit_is_enforced_both_by_header_and_by_actual_bytes(self):
        self.assertEqual(self.put(b"x" * (1024 * 1024 + 1))[0], 413)
        self.assertEqual(self.put(b"x", **{"Content-Length": str(10 * 1024 * 1024)})[0], 413)
        self.assertIsNone(self.admin.seen)
        self.assertEqual(self.put(b"x" * 1024 * 1024)[0], 200)


class PartAdmin:
    def __init__(self):
        self.seen = None

    def multipart_part(self, upload_id, part_number, data):
        self.seen = (upload_id, part_number, bytes(data))
        return {"part_number": part_number, "etag": "e" * 32, "size": len(data)}


class PartRouteTests(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.multiple(auth.settings, update_api_token=TOKEN, debug=False)
        patch.start()
        self.addCleanup(patch.stop)
        self.admin = PartAdmin()
        api._admin = self.admin
        self.addCleanup(setattr, api, "_admin", None)

    def put(self, body: bytes, number="3", **headers):
        return fastapi_stub.call(api.router, "PUT", f"/api/storage/multipart/{'u' * 32}/parts/{number}",
                                 headers={**ADMIN, **headers}, raw=body)

    def test_part_bytes_reach_the_admin_layer_with_int_number(self):
        status, payload, _ = self.put(bytes(range(256)) * 600)
        self.assertEqual((status, payload["data"]["part_number"]), (200, 3))
        self.assertEqual(self.admin.seen[:2], ("u" * 32, 3))
        self.assertEqual(len(self.admin.seen[2]), 256 * 600)

    def test_empty_and_oversized_parts_are_rejected(self):
        self.assertEqual(self.put(b"")[0], 422)
        with mock.patch.object(api, "_PART_LIMIT", 1024):
            self.assertEqual(self.put(b"x" * 1025)[0], 413)
            self.assertEqual(self.put(b"x", **{"Content-Length": "5000"})[0], 413)
            self.assertEqual(self.put(b"x" * 1024)[0], 200)


class PublicDownloadTests(unittest.TestCase):
    class Admin:
        def download_by_token(self, token):
            if token != "good":
                raise AdminError(403, "Ссылка недействительна или истекла")
            return {"key": "dir/photo.jpg", "content_type": "image/jpeg", "size": 3}, iter([b"abc"])

    def setUp(self):
        api._admin = self.Admin()
        self.addCleanup(setattr, api, "_admin", None)

    def test_valid_link_streams_the_file_without_any_auth_headers(self):
        status, response, _ = fastapi_stub.call(api.router, "GET", "/api/storage/download/good")
        self.assertEqual(status, 200)
        self.assertEqual(b"".join(response.body_iterator), b"abc")
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_bad_link_is_rejected(self):
        status, detail, _ = fastapi_stub.call(api.router, "GET", "/api/storage/download/bad")
        self.assertEqual((status, detail), (403, "Ссылка недействительна или истекла"))

    def test_link_creation_itself_requires_admin(self):
        with mock.patch.multiple(auth.settings, update_api_token=TOKEN, debug=False):
            status, _, _ = fastapi_stub.call(api.router, "POST", "/api/storage/objects/oid/download-link")
        self.assertIn(status, (401, 403))


class ErrorMappingTests(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.multiple(auth.settings, update_api_token=TOKEN, debug=False)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(setattr, api, "_admin", None)

    def route(self, error):
        class Failing:
            def bucket_create(self, name):
                raise error
        api._admin = Failing()
        return fastapi_stub.call(api.router, "POST", "/api/storage/buckets", headers=ADMIN, body={"name": "x"})

    def test_admin_error_becomes_http_error(self):
        self.assertEqual(self.route(AdminError(409, "занято"))[:2], (409, "занято"))
        status, detail, _ = self.route(AdminError(422, "Проверьте значения", {"name": "плохое имя"}))
        self.assertEqual((status, detail), (422, {"message": "Проверьте значения", "errors": {"name": "плохое имя"}}))

    def test_storage_error_does_not_leak_internals(self):
        status, detail, _ = self.route(StorageError("sqlite: disk I/O error at /opt/polaris/storage.db token=123:ABC"))
        self.assertEqual(status, 503)
        self.assertNotIn("sqlite", str(detail))
        self.assertNotIn("123:ABC", str(detail))


class RouterWithRealAdminTests(AdminTestCase):
    """Маршруты + настоящий StorageAdmin: сценарии, которыми пользуется интерфейс."""

    def setUp(self):
        super().setUp()
        patch = mock.patch.multiple(auth.settings, update_api_token=TOKEN, debug=False)
        patch.start()
        self.addCleanup(patch.stop)
        api._admin = self.admin
        self.addCleanup(setattr, api, "_admin", None)

    def call(self, method, path, **kw):
        return fastapi_stub.call(api.router, method, path, headers=ADMIN, **kw)

    def test_bucket_key_and_revocation_flow(self):
        status, payload, _ = self.call("POST", "/api/storage/buckets", body={"name": "ui-bucket"})
        self.assertEqual((status, payload["data"]["name"]), (200, "ui-bucket"))
        status, payload, headers = self.call("POST", "/api/storage/credentials", body={
            "name": "ui key", "description": "", "permissions": ["ListBucket", "ReadObject", "WriteObject"],
            "buckets": ["ui-bucket"]})
        self.assertEqual((status, headers["Cache-Control"]), (200, "no-store"))
        secret, cid = payload["data"]["secret_access_key"], payload["data"]["credential"]["id"]
        listing = json.dumps(self.call("GET", "/api/storage/credentials")[1])
        self.assertNotIn(secret, listing)
        self.assertEqual(self.call("POST", f"/api/storage/credentials/{cid}/revoke")[1]["data"]["credential"]["status"], "revoked")
        status, detail, _ = self.call("POST", f"/api/storage/credentials/{cid}/rotate")
        self.assertEqual(status, 409)
        self.assertEqual(self.call("DELETE", f"/api/storage/credentials/{cid}")[0], 200)

    def test_validation_errors_are_422_with_field_details(self):
        status, detail, _ = self.call("POST", "/api/storage/credentials", body={"name": "", "permissions": [], "buckets": ["files"]})
        self.assertEqual(status, 422)
        self.assertIn("name", detail["errors"])
        status, detail, _ = self.call("POST", "/api/storage/buckets", body={"name": "Bad"})
        self.assertEqual((status, "name" in detail["errors"]), (422, True))

    def test_status_and_diagnostics_are_json_serialisable(self):
        for path in ("/api/storage/status", "/api/storage/diagnostics", "/api/storage/config", "/api/storage/buckets",
                     "/api/storage/credentials", "/api/storage/multipart"):
            status, payload, _ = self.call("GET", path)
            self.assertEqual(status, 200, path)
            json.dumps(payload)
        self.assertEqual(self.call("POST", "/api/storage/diagnostics/run", body={})[1]["data"]["checks"]["database"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
