"""Маршруты /api/meme/*: всё только для админа, кроме подписанной выдачи оригинала; ошибки и ответы без внутренностей Storage."""
from __future__ import annotations

from unittest import mock

from tests import fastapi_stub
from tests.meme_helpers import MemeTestCase, load_meme_api, png

auth, api = load_meme_api()

TOKEN = "admin-token-123"
ADMIN = {"X-Polaris-Token": TOKEN}
EXPECTED = {
    ("GET", "/api/meme"), ("POST", "/api/meme"), ("GET", "/api/meme/media/{token}"),
    ("GET", "/api/meme/settings"), ("PUT", "/api/meme/settings"), ("POST", "/api/meme/settings/test"),
    ("POST", "/api/meme/settings/sync"), ("GET", "/api/meme/{meme_id}"), ("PATCH", "/api/meme/{meme_id}"), ("DELETE", "/api/meme/{meme_id}"),
}


class MemeRouteTests(MemeTestCase):
    def setUp(self):
        super().setUp()
        patch = mock.patch.multiple(auth.settings, update_api_token=TOKEN, telegram_bot_token="123456:ABCDEF", debug=False)
        patch.start()
        self.addCleanup(patch.stop)
        api.get_meme_service = lambda: self.service
        self.call = lambda m, p, **kw: fastapi_stub.call(api.router, m, p, headers=kw.pop("headers", ADMIN), **kw)

    def upload(self, data=None, **query):
        return self.call("POST", "/api/meme", raw=data if data is not None else png(seed=1),
                         query={"title": "Котик", "tags": "кот,сон", "filename": "cat.png", **query})

    def test_routes_are_exactly_the_specified_ones(self):
        self.assertEqual({(m, p) for m, p, _ in api.router.routes}, EXPECTED)

    def test_everything_but_media_requires_admin(self):
        created = self.upload()[1]["data"]
        for method, path, _ in api.router.routes:
            if "/media/" in path:
                continue
            url = path.replace("{meme_id}", created["id"])
            for headers in ({}, {"X-Polaris-Token": "wrong"}, {"X-Telegram-Init-Data": "query_id=1&hash=00"}):
                status, _, _ = fastapi_stub.call(api.router, method, url, headers=headers, body={}, raw=png(seed=2))
                self.assertIn(status, (401, 403), f"{method} {url}")
        self.assertEqual(self.service.get(created["id"])["title"], "Котик")  # ничего не изменилось и не удалилось

    def test_upload_list_get_update_delete_cycle(self):
        status, payload, _ = self.upload()
        self.assertEqual(status, 200)
        meme = payload["data"]
        self.assertEqual((meme["title"], meme["tags"], meme["mime_type"]), ("Котик", ["кот", "сон"], "image/png"))
        status, payload, _ = self.call("GET", "/api/meme", query={"q": "КОТ"})
        self.assertEqual([i["id"] for i in payload["data"]["items"]], [meme["id"]])
        status, payload, _ = self.call("PATCH", f"/api/meme/{meme['id']}", body={"title": "Другое"})
        self.assertEqual(payload["data"]["title"], "Другое")
        self.assertEqual(self.call("GET", f"/api/meme/{meme['id']}")[1]["data"]["title"], "Другое")
        self.assertEqual(self.call("DELETE", f"/api/meme/{meme['id']}")[0], 200)
        self.assertEqual(self.call("GET", f"/api/meme/{meme['id']}")[0], 404)

    def test_media_is_public_with_token_and_serves_the_real_file(self):
        meme = self.upload(png(seed=4))[1]["data"]
        status, response, _ = fastapi_stub.call(api.router, "GET", meme["image_url"], headers={})
        self.assertEqual(status, 200)
        body = b"".join(response.body_iterator)
        self.assertEqual(body, png(seed=4))
        self.assertEqual(response.media_type, "image/png")
        self.assertEqual(response.headers["Content-Length"], str(len(body)))
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

    def test_media_rejects_bad_tokens_and_hides_reason(self):
        for token in ("x.y", "abc"):
            status, detail, _ = fastapi_stub.call(api.router, "GET", f"/api/meme/media/{token}", headers={})
            self.assertEqual(status, 403)
            self.assertNotIn("подпис", str(detail).lower())

    def test_errors_map_to_http_statuses(self):
        self.assertEqual(self.upload(b"not an image")[0], 422)
        self.upload(png(seed=7))
        status, detail, _ = self.upload(png(seed=7), title="Копия")
        self.assertEqual(status, 409)
        self.assertIn("existing_id", detail)
        self.assertEqual(self.call("PATCH", "/api/meme/нет", body={"title": "x"})[0], 404)
        self.assertEqual(self.call("DELETE", "/api/meme/нет")[0], 404)

    def test_oversized_body_is_rejected_before_reading_everything(self):
        with mock.patch("polaris.modules.meme.media.MAX_BYTES", 100):
            status, _, _ = fastapi_stub.call(api.router, "POST", "/api/meme", headers=ADMIN, raw=png(), query={})
        self.assertEqual(status, 413)

    def test_storage_down_is_503(self):
        self.service._get_service = lambda: None
        self.assertEqual(self.upload()[0], 503)

    def test_settings_routes_do_not_collide_with_meme_ids(self):
        status, payload, _ = self.call("GET", "/api/meme/settings")
        self.assertEqual(status, 200)
        self.assertIn("mode", payload["data"])  # это настройки, а не «запись с id settings»

    def test_settings_save_test_sync_through_http(self):
        from tests.test_meme_telegram_cache import FakeSession
        status, payload, _ = self.call("PUT", "/api/meme/settings", body={"cache_chat_id": "-100500500"})
        self.assertEqual((status, payload["data"]["effective_chat"]), (200, "-100500500"))
        self.assertEqual(self.call("PUT", "/api/meme/settings", body={"cache_chat_id": "t.me/x"})[0], 422)
        with mock.patch("polaris.modules.meme.telegram_cache._session", return_value=FakeSession()):
            status, payload, _ = self.call("POST", "/api/meme/settings/test", body={})
        self.assertEqual((status, payload["data"]["title"]), (200, "Кэш мемов"))
        self.upload(png(seed=3))
        self.push_result = ("FID", "photo")
        status, payload, _ = self.call("POST", "/api/meme/settings/sync", query={"limit": "5", "skip": ""})
        self.assertEqual((status, payload["data"]["done"], payload["data"]["remaining"]), (200, 1, 0))
