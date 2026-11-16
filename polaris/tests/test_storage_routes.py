"""Маршруты /api/storage/*: все только для админа, S3-ключи доступа не дают, ошибки и секреты."""
from __future__ import annotations

import json
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
}


def concrete(path: str) -> str:
    return path.replace("{bucket}", "files").replace("{object_id}", "oid").replace("{credential_id}", "cid").replace("{upload_id}", "u" * 32)


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
            url = concrete(path)
            for label, headers in (("без учётных данных", {}), ("S3-подпись", s3_like), ("неверный токен", {"X-Polaris-Token": "x"}),
                                   ("S3-секрет в роли токена", {"X-Polaris-Token": "S" * 40}),
                                   ("битый initData", {"X-Telegram-Init-Data": "query_id=1&hash=00"})):
                status, _, _ = fastapi_stub.call(api.router, method, url, headers=headers, body={})
                self.assertIn(status, (401, 403), f"{method} {url}: {label}")
            self.assertEqual(self.fake.calls, [], f"{method} {url}: обработчик вызван без авторизации")

    def test_admin_token_passes_through_to_handlers(self):
        for method, path, _ in api.router.routes:
            status, payload, _ = fastapi_stub.call(api.router, method, concrete(path), headers=ADMIN, body={})
            if path.endswith("/download"):
                continue
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

    def test_download_headers_cannot_be_injected(self):
        status, response, _ = fastapi_stub.call(api.router, "GET", "/api/storage/objects/oid/download", headers=ADMIN)
        disposition = response.headers["Content-Disposition"]
        self.assertNotIn("\r", disposition)
        self.assertNotIn("\n", disposition)
        self.assertIn("filename*=UTF-8''", disposition)
        self.assertEqual((response.media_type, response.headers["Content-Length"], response.headers["Cache-Control"]),
                         ("text/plain", "3", "no-store"))
        self.assertEqual(b"".join(response.body_iterator), b"abc")


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
