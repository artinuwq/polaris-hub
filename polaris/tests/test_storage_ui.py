"""UI раздела Storage в настоящем Chromium: реальный frontend, реальные маршруты /api/storage/*, реальный S3 API."""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import shutil
import unittest
from unittest import mock

from tests import fastapi_stub
from tests.asgi_server import AsgiTestServer
from tests.s3_client import S3Client
from tests.storage_helpers import MockTelegram
from tests.test_s3_api import code
from tests.test_storage_admin import AdminTestCase
from tests.ui_support import TOKEN, UiApp, playwright_available

auth, api = fastapi_stub.load_stubbed()
AVAILABLE, WHY = playwright_available()
if AVAILABLE:
    from playwright.sync_api import sync_playwright

XSS = '<img src=x onerror="window.__xss=1">'


@unittest.skipUnless(AVAILABLE, WHY)
class UiTestCase(AdminTestCase):
    playwright = browser = None

    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    viewport = {"width": 1280, "height": 900}

    def setUp(self):
        super().setUp()
        patch = mock.patch.multiple(auth.settings, update_api_token=TOKEN, debug=False)
        patch.start()
        self.addCleanup(patch.stop)
        api._admin = self.admin
        self.addCleanup(setattr, api, "_admin", None)
        self.ui = UiApp(api.router, self.app)
        self.server = AsgiTestServer(self.ui).start()
        self.addCleanup(self.app.close)
        self.addCleanup(self.server.stop)
        self.context = self.browser.new_context(viewport=self.viewport, accept_downloads=True,
                                                permissions=["clipboard-read", "clipboard-write"])
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.js_errors: list[str] = []
        self.page.on("pageerror", lambda e: self.js_errors.append(str(e)))
        self.page.set_default_timeout(8000)
        self.s3 = S3Client(self.server.url)

    def tearDown(self):
        self.assertEqual(self.js_errors, [], "необработанные JS-исключения")

    # ─── помощники ───
    def open_storage(self, section=None):
        page = self.page
        page.goto(f"{self.server.url}/?token={TOKEN}")
        page.wait_for_selector("#drawer-toggle")
        page.click("#drawer-toggle")
        page.click('button.drawer-item[data-view="storage"]')
        page.wait_for_selector("#storage-container .st-tab")
        if section:
            self.tab(section)

    def tab(self, section):
        self.page.click(f'.sg-tabs .st-tab[data-section="{section}"]')

    def text(self, selector="#storage-container"):
        return self.page.inner_text(selector)

    def modal_text(self):
        return self.page.inner_text(".tk-modal")

    def click_modal(self, action):
        self.page.click(f'.tk-modal [data-sm="{action}"]')

    def wait_modal_closed(self):
        self.page.wait_for_selector(".tk-modal", state="detached")

    def notice(self):
        return self.page.inner_text("#sg-msg")

    def create_key_via_ui(self, name="svc", desc="", buckets=("files",), perms=("ListBucket", "ReadObject", "WriteObject"),
                          all_buckets=False):
        page = self.page
        self.tab("keys")
        page.click('[data-sg="new-key"] >> nth=0')
        page.fill("#sm-kname", name)
        page.fill("#sm-kdesc", desc)
        if all_buckets:
            page.check('input[name="sm-scope"][value="all"]')
        for b in buckets if not all_buckets else ():
            page.check(f'[data-bucket][value="{b}"]')
        for p in perms:
            page.check(f'[data-perm][value="{p}"]')
        page.click("#sm-kcreate")
        page.wait_for_selector("#sm-secret")
        return page.inner_text("#sm-akid").strip(), page.inner_text("#sm-secret").strip()


class NavigationAndOverviewTests(UiTestCase):
    def test_storage_tab_sits_directly_above_settings(self):
        page = self.page
        page.goto(f"{self.server.url}/?token={TOKEN}")
        page.wait_for_selector("#drawer-toggle")
        page.click("#drawer-toggle")
        order = page.eval_on_selector_all("#drawer-nav .drawer-item", "els => els.map(e => e.dataset.view)")
        self.assertEqual(order.index("storage") + 1, order.index("settings"))
        self.assertEqual(order[-2:], ["storage", "settings"])
        footer_children = page.eval_on_selector_all("#drawer-nav > *", "els => els.map(e => e.className.split(' ')[0] + ':' + (e.dataset.view || ''))")
        self.assertEqual(footer_children[-3:], ["drawer-divider:", "drawer-item:storage", "drawer-item:settings"])
        self.assertIn("Storage", page.inner_text('button.drawer-item[data-view="storage"]'))
        page.click('button.drawer-item[data-view="storage"]')
        page.wait_for_selector(".sg-tabs")
        labels = page.eval_on_selector_all(".sg-tabs .st-tab", "els => els.map(e => e.textContent.trim())")
        self.assertEqual(labels, ["Обзор", "Бакеты", "Ключи доступа", "Диагностика", "Конфигурация"])
        # существующие разделы продолжают работать
        page.click("#drawer-toggle")
        page.click('button.drawer-item[data-view="settings"]')
        page.wait_for_selector("#settings-container")
        self.assertEqual(page.locator("#storage-container").count(), 0)

    def test_tabs_show_icons_instead_of_labels_when_narrow(self):
        page = self.page
        page.set_viewport_size({"width": 1280, "height": 800})
        self.open_storage()
        page.wait_for_selector(".sg-tabs")
        self.assertTrue(page.locator(".sg-tabs .st-tab:not(.is-active) .sg-tab-label").first.is_visible())
        self.assertTrue(page.locator(".sg-tabs .sg-tab-icon").first.is_hidden())  # на широком экране — только подписи
        page.set_viewport_size({"width": 360, "height": 800})
        page.wait_for_function("getComputedStyle(document.querySelector('.sg-tabs .st-tab:not(.is-active) .sg-tab-label')).display === 'none'")
        self.assertTrue(page.locator(".sg-tabs .st-tab:not(.is-active) .sg-tab-icon").first.is_visible())
        self.assertTrue(page.locator(".sg-tabs .st-tab.is-active .sg-tab-label").is_visible())  # у активной подпись остаётся
        names = page.eval_on_selector_all(".sg-tabs .st-tab", "els => els.map(e => e.getAttribute('aria-label'))")
        self.assertEqual(names, ["Обзор", "Бакеты", "Ключи доступа", "Диагностика", "Конфигурация"])  # иконки подписаны для скринридеров

    def test_overview_shows_real_checked_statuses_and_exact_stats(self):
        self.core.put_object("files", "a.bin", b"x" * 1000)
        self.open_storage()
        page = self.page
        page.wait_for_selector('.sg-comp[data-component="telegram"] .st-badge[data-status="ok"]')
        for component in ("core", "telegram", "s3", "database"):
            badge = page.locator(f'.sg-comp[data-component="{component}"] .st-badge')
            self.assertEqual(badge.get_attribute("data-status"), "ok", component)
            comp_text = page.inner_text(f'.sg-comp[data-component="{component}"]')
            self.assertNotIn("Последнее успешное обращение", comp_text)
            self.assertIn("мс", comp_text)
        self.assertEqual(page.locator(".sg-comp").count(), 4)
        tiles = page.inner_text(".sg-stats")
        self.assertIn("Buckets", tiles)
        self.assertRegex(tiles, r"Объекты \(логические\)")
        self.assertIn("Blobs (физические)", tiles)
        self.assertIn("Незавершённые multipart", tiles)
        self.assertIn("1000 Б", tiles.replace("\xa0", " "))
        self.assertNotIn("Быстрые действия", self.text())
        self.assertEqual(page.locator(".sg-quick").count(), 0)
        pop = page.locator(".sg-stats-head .sg-tip-pop")
        self.assertTrue(pop.is_hidden())  # сводка скрыта по умолчанию
        page.click(".sg-stats-head .sg-info")
        self.assertIn("точные", pop.inner_text())
        self.assertEqual(page.get_attribute(".sg-stats-head .sg-info", "aria-expanded"), "true")
        page.click("h3.st-subtitle >> text=Компоненты")  # клик вне подсказки закрывает её
        self.assertTrue(pop.is_hidden())

    def test_nothing_is_online_when_checks_did_not_run(self):
        self.page.route("**/api/storage/diagnostics/run", lambda route: route.abort())
        self.open_storage()
        self.page.wait_for_selector(".sg-comp")
        for component in ("core", "telegram", "s3", "database"):
            badge = self.page.locator(f'.sg-comp[data-component="{component}"] .st-badge')
            self.assertEqual(badge.get_attribute("data-status"), "unknown", component)
            self.assertEqual(badge.inner_text(), "Не проверялось")
        self.assertIn("Нет связи", self.text())

    def test_failure_is_visible_after_recheck(self):
        from polaris.storage.backends.base import HealthStatus
        self.open_storage()
        self.page.wait_for_selector('.sg-comp[data-component="telegram"] .st-badge[data-status="ok"]')
        self.driver.health_check = lambda: HealthStatus(False, "Telegram отклонил токен")
        self.page.click('[data-sg="recheck"]')
        self.page.wait_for_selector('.sg-comp[data-component="telegram"] .st-badge[data-status="error"]')
        self.assertIn("Telegram отклонил токен", self.text('.sg-comp[data-component="telegram"]'))

    def test_disabled_storage_is_explained_not_faked(self):
        self.service.state = "disabled"
        self.admin = self.make_admin(service=None)
        api._admin = self.admin
        self.open_storage()
        self.page.wait_for_selector(".sg-banner")
        self.assertIn("Storage", self.text(".sg-banner"))
        for component in ("core", "database", "telegram"):
            self.assertNotEqual(self.page.locator(f'.sg-comp[data-component="{component}"] .st-badge').get_attribute("data-status"), "ok")
        self.assertIn("недоступна", self.text())


class BucketUiTests(UiTestCase):
    def test_create_validate_duplicate_and_list(self):
        self.open_storage("buckets")
        page = self.page
        page.wait_for_selector("[data-bucket-row]")
        page.click('[data-sg="new-bucket"]')
        for bad, expect in (("Bad_Name", "строчные"), ("ab", "3–63"), ("", "3–63")):
            page.fill("#sm-name", bad)
            page.click("#sm-create")
            page.wait_for_function(
                "([sel, text]) => { const n = document.querySelector(sel); return n && !n.hidden && n.textContent.includes(text); }",
                arg=['[data-field-error="name"]', expect])
            self.assertIn(expect, page.inner_text('[data-field-error="name"]'))
        page.fill("#sm-name", "files")
        page.click("#sm-create")
        page.wait_for_function(
            "([sel, text]) => { const n = document.querySelector(sel); return n && !n.hidden && n.textContent.includes(text); }",
            arg=['[data-field-error="name"]', "занято"])
        page.fill("#sm-name", "ui-bucket")
        page.click("#sm-create")
        self.wait_modal_closed()
        self.assertIn("создан", self.notice())
        page.wait_for_selector('[data-bucket-row="ui-bucket"]')
        row = self.text('[data-bucket-row="ui-bucket"]')
        self.assertRegex(row, r"ui-bucket\s+\S+.*0\s+0 Б")
        self.assertEqual([b.name for b in self.core.list_buckets()], ["files", "ui-bucket"])
        headers = page.eval_on_selector_all(".sg-table th", "els => els.map(e => e.textContent.trim())")
        self.assertEqual(headers, ["Bucket Name", "Created At", "Object Count", "Total Size", "Actions"])

    def test_empty_state_and_error_states(self):
        self.core.delete_bucket("files")
        self.open_storage("buckets")
        self.page.wait_for_selector('[data-empty="buckets"]')
        self.assertIn("пока нет", self.text('[data-empty="buckets"]'))
        self.page.unroute_all()
        self.page.route("**/api/storage/buckets", lambda r: r.fulfill(status=500, json={"detail": "хранилище сломалось"}))
        self.tab("overview")
        self.tab("buckets")
        self.page.wait_for_selector(".st-error")
        self.assertIn("хранилище сломалось", self.text(".st-error"))

    def test_object_listing_metadata_download_copy_and_delete(self):
        data = os.urandom(5000)
        self.core.put_object("files", "docs/report.bin", data, mime_type="application/x-report",
                             metadata={"s3": {"meta": {"owner": "alice"}, "headers": {"cache-control": "no-cache"}}})
        self.core.put_object("files", f"weird/{XSS}.txt", b"x", mime_type="text/plain")
        self.open_storage("buckets")
        page = self.page
        page.click('[data-sg="open-bucket"][data-name="files"]')
        page.wait_for_selector("[data-object-row]")
        self.assertEqual(page.inner_text("[data-bucket-title]"), "files")
        self.assertEqual(page.inner_text("[data-bucket-count]"), "2")
        row_text = self.text('[data-object-row] >> nth=0')
        self.assertIn("docs/report.bin", row_text)
        self.assertIn("application/x-report", row_text)
        self.assertIn(hashlib.md5(data).hexdigest(), row_text)
        self.assertIn("4.88 КиБ", row_text.replace("\xa0", " "))
        self.assertEqual(page.evaluate("window.__xss"), None)  # HTML в ключе не исполняется
        self.assertIn(XSS, page.inner_text(".sg-table"))
        page.click('[data-sg="object-info"] >> nth=0')
        modal = self.modal_text()
        self.assertIn("alice", modal)
        self.assertIn("no-cache", modal)
        for forbidden in ("blob", "chunk", "file_id", "message_id", "chat_id", "mem-"):
            self.assertNotIn(forbidden, modal.lower())
        self.click_modal("copy")
        self.assertEqual(page.evaluate("navigator.clipboard.readText()"), "docs/report.bin")
        self.click_modal("close")
        self.wait_modal_closed()
        page.click('[data-sg="object-copy-key"] >> nth=0')
        self.assertEqual(page.evaluate("navigator.clipboard.readText()"), "docs/report.bin")
        with page.expect_download() as info:
            page.click('[data-sg="object-download"] >> nth=0')
        path = info.value.path()
        self.assertEqual(info.value.suggested_filename, "report.bin")
        self.assertEqual(open(path, "rb").read(), data)
        # удаление требует подтверждения
        page.click('[data-sg="object-delete"] >> nth=0')
        self.assertIn("docs/report.bin", self.modal_text())
        self.click_modal("close")
        self.wait_modal_closed()
        self.assertEqual(len(self.core.list_objects("files")), 2)
        page.click('[data-sg="object-delete"] >> nth=0')
        self.click_modal("confirm")
        self.wait_modal_closed()
        page.wait_for_function("document.querySelector('[data-bucket-count]').textContent === '1'")
        self.assertEqual([o.object_key for o in self.core.list_objects("files")], [f"weird/{XSS}.txt"])
        page.click('[data-sg="object-delete"] >> nth=0')
        self.click_modal("confirm")
        page.wait_for_selector('[data-empty="objects"]')
        self.assertEqual(self.driver.blobs, {})

    def test_pagination_and_prefix_filter(self):
        for i in range(120):
            self.core.put_object("files", f"k/{i:03d}", b"x")
        self.open_storage("buckets")
        page = self.page
        page.click('[data-sg="open-bucket"][data-name="files"]')
        page.wait_for_selector("[data-object-row]")
        self.assertEqual(page.locator("[data-object-row]").count(), 100)
        page.click('[data-sg="more-objects"]')
        page.wait_for_function("document.querySelectorAll('[data-object-row]').length === 120")
        page.fill("#sg-prefix", "k/01")
        page.click('[data-sg="apply-prefix"]')
        page.wait_for_function("document.querySelectorAll('[data-object-row]').length === 10")

    def test_delete_bucket_only_when_empty(self):
        self.core.put_object("files", "k", b"x")
        self.open_storage("buckets")
        page = self.page
        page.click('[data-sg="delete-bucket"][data-name="files"]')
        self.assertIn("files", self.modal_text())
        self.click_modal("confirm")
        page.wait_for_selector("#sm-error:not([hidden])")
        self.assertIn("не пуст", self.modal_text())
        self.click_modal("close")
        self.wait_modal_closed()
        self.assertIn("files", [b.name for b in self.core.list_buckets()])
        self.core.delete_object(self.core.get_metadata_by_key("files", "k").id)
        page.click('[data-sg="delete-bucket"][data-name="files"]')
        self.click_modal("confirm")
        self.wait_modal_closed()
        page.wait_for_selector('[data-empty="buckets"]')
        self.assertEqual(self.core.list_buckets(), [])


class AccessKeyUiTests(UiTestCase):
    def test_secret_is_shown_once_and_never_again(self):
        self.open_storage()
        akid, secret = self.create_key_via_ui("backup", "ночные бэкапы")
        page = self.page
        self.assertRegex(akid, r"^PLRS[A-Z2-7]{16}$")
        self.assertEqual(len(secret), 40)
        self.assertIn("один раз", page.inner_text(".sg-warn"))
        page.click('[data-sm="copy-id"]')
        page.wait_for_function("document.querySelector('#sm-error') && document.querySelector('#sm-error').textContent.includes('скопирован')")
        self.assertEqual(page.evaluate("navigator.clipboard.readText()"), akid)
        page.click('[data-sm="copy-secret"]')
        page.wait_for_function("document.querySelector('#sm-error').textContent.includes('Secret')")
        self.assertEqual(page.evaluate("navigator.clipboard.readText()"), secret)
        page.keyboard.press("Escape")
        page.mouse.click(5, 5)  # клик по оверлею не закрывает окно с секретом
        self.assertEqual(page.locator("#sm-secret").count(), 1)
        page.click("#sm-done")
        self.wait_modal_closed()
        self.assertNotIn(secret, page.content())
        self.assertNotIn(secret, page.inner_text("body"))
        card = self.text(f'.sg-key-card[data-key-id="{akid}"]')
        self.assertIn(akid, card)
        for expected in ("backup", "ночные бэкапы", "Active", "ListBucket", "ReadObject", "WriteObject", "files", "ещё не использовался"):
            self.assertIn(expected, card)
        page.reload()
        page.wait_for_selector("#drawer-toggle")
        page.click("#drawer-toggle")
        page.click('button.drawer-item[data-view="storage"]')
        self.tab("keys")
        page.wait_for_selector(".sg-key-card")
        self.assertNotIn(secret, page.content())
        self.assertNotIn(secret, str(self.page.evaluate("JSON.stringify(Object.entries(localStorage))")))

    def test_no_api_list_response_ever_contains_the_secret(self):
        seen: list[str] = []
        self.page.on("response", lambda r: seen.append(r.url + " " + (r.text() if "/api/storage/" in r.url and "credentials" in r.url else "")))
        self.open_storage()
        akid, secret = self.create_key_via_ui()
        self.page.click("#sm-done")
        self.wait_modal_closed()
        self.page.reload()
        self.page.wait_for_selector("#drawer-toggle")
        self.page.click("#drawer-toggle")
        self.page.click('button.drawer-item[data-view="storage"]')
        self.tab("keys")
        self.page.wait_for_selector(".sg-key-card")
        self.assertTrue(self.page.locator(".sg-keys-block .sg-key-card").count())  # ключи лежат внутри блока
        with_secret = [u for u in seen if secret in u]
        self.assertEqual(len(with_secret), 1)  # только ответ на создание
        self.assertTrue(with_secret[0].startswith(f"{self.server.url}/api/storage/credentials "))

    def test_form_validation_and_permission_dependencies(self):
        self.open_storage("keys")
        page = self.page
        page.click('[data-sg="new-key"] >> nth=0')
        page.click("#sm-kcreate")
        page.wait_for_selector('[data-field-error="name"]:not([hidden])')
        self.assertIn("обязательное", page.inner_text('[data-field-error="name"]'))
        for field in ("buckets", "permissions"):  # все ошибки формы показываются сразу
            self.assertFalse(page.is_hidden(f'[data-field-error="{field}"]'))
        page.fill("#sm-kname", "x")
        page.click("#sm-kcreate")
        page.wait_for_selector('[data-field-error="buckets"]:not([hidden])')
        page.check('[data-bucket][value="files"]')
        page.click("#sm-kcreate")
        page.wait_for_selector('[data-field-error="permissions"]:not([hidden])')
        page.check('[data-perm][value="MultipartUpload"]')
        self.assertTrue(page.is_checked('[data-perm][value="WriteObject"]'))  # зависимость выставлена автоматически
        page.uncheck('[data-perm][value="WriteObject"]')
        self.assertFalse(page.is_checked('[data-perm][value="MultipartUpload"]'))
        self.assertEqual(self.store.list(), [])
        for perm in ("ListBucket", "ReadObject", "WriteObject", "DeleteObject", "MultipartUpload"):
            self.assertEqual(page.locator(f'[data-perm][value="{perm}"]').count(), 1)

    def test_key_created_in_ui_works_with_s3_with_least_privilege_then_revoke(self):
        self.core.create_bucket("other-bucket")
        self.open_storage()
        akid, secret = self.create_key_via_ui("svc", buckets=("files",), perms=("ListBucket", "ReadObject", "WriteObject"))
        self.page.click("#sm-done")
        self.wait_modal_closed()
        client = S3Client(self.server.url, access_key=akid, secret=secret)
        self.assertEqual(client.put("files", "from-ui-key.txt", b"hello").status_code, 200)
        self.assertEqual(client.get("files", "from-ui-key.txt").content, b"hello")
        self.assertEqual(client.get("files", "", query="list-type=2").status_code, 200)
        for denied in (client.request("DELETE", "files", "from-ui-key.txt"), client.get("other-bucket", "", query="list-type=2"),
                       client.put("other-bucket", "k", b"x"), client.request("PUT", "new-bucket-x")):
            self.assertEqual(denied.status_code, 403)
        # независимый клиент: curl --aws-sigv4 (если поддерживается)
        if shutil.which("curl") and "aws-sigv4" in subprocess.run(["curl", "--help", "all"], capture_output=True, text=True).stdout:
            def curl_status():
                out = subprocess.run(["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "--aws-sigv4", "aws:amz:us-east-1:s3",
                                      "--user", f"{akid}:{secret}", "-H", "x-amz-content-sha256: UNSIGNED-PAYLOAD",
                                      f"{self.server.url}/s3/files/from-ui-key.txt"], capture_output=True, text=True)
                return out.stdout
            self.assertEqual(curl_status(), "200")
        self.page.click(f'.sg-key-card[data-key-id="{akid}"] [data-sg="key-revoke"]')
        self.assertIn("немедленно", self.modal_text())
        self.click_modal("confirm")
        self.wait_modal_closed()
        self.page.wait_for_selector(f'.sg-key-card.is-revoked[data-key-id="{akid}"]')
        self.assertIn("Revoked", self.text(f'.sg-key-card[data-key-id="{akid}"]'))
        self.assertEqual(self.page.locator(f'.sg-key-card[data-key-id="{akid}"] [data-sg="key-rotate"]').count(), 0)
        for r in (client.get("files", "from-ui-key.txt"), client.request("GET"), client.put("files", "again", b"x")):
            self.assertEqual((r.status_code, code(r)), (403, "InvalidAccessKeyId"))
        if shutil.which("curl"):
            self.assertEqual(curl_status(), "403")
        # presigned URL, выданный до отзыва, тоже перестаёт работать
        self.assertEqual(self.s3.get("files", "from-ui-key.txt").content, b"hello")  # ключ из .env не затронут

    def test_rotate_shows_new_secret_once_and_kills_old_one(self):
        self.open_storage()
        akid, old_secret = self.create_key_via_ui("rot")
        self.page.click("#sm-done")
        self.wait_modal_closed()
        self.page.click(f'.sg-key-card[data-key-id="{akid}"] [data-sg="key-rotate"]')
        self.assertIn("прежний перестанет работать немедленно", self.modal_text())
        self.assertNotIn(old_secret, self.modal_text())  # старый секрет не раскрывается
        self.click_modal("confirm")
        self.page.wait_for_selector("#sm-secret")
        new_secret = self.page.inner_text("#sm-secret").strip()
        self.assertNotEqual(new_secret, old_secret)
        self.assertEqual(self.page.inner_text("#sm-akid").strip(), akid)
        self.page.click("#sm-done")
        self.wait_modal_closed()
        self.assertNotIn(new_secret, self.page.content())
        old = S3Client(self.server.url, access_key=akid, secret=old_secret).get("files", "", query="list-type=2")
        self.assertEqual((old.status_code, code(old)), (403, "SignatureDoesNotMatch"))
        fresh = S3Client(self.server.url, access_key=akid, secret=new_secret).get("files", "", query="list-type=2")
        self.assertEqual(fresh.status_code, 200)
        self.assertIn("ротация", self.text(f'.sg-key-card[data-key-id="{akid}"]'))

    def test_edit_description_delete_and_config_keys(self):
        self.open_storage()
        akid, _ = self.create_key_via_ui("edit-me", "было")
        self.page.click("#sm-done")
        self.wait_modal_closed()
        card = f'.sg-key-card[data-key-id="{akid}"]'
        self.page.click(f"{card} [data-sg='key-edit']")
        self.page.fill("#sm-edesc", "стало")
        self.click_modal("save")
        self.wait_modal_closed()
        self.page.wait_for_function(f"document.querySelector('{card}').textContent.includes('стало')")
        self.assertEqual(self.page.locator(f"{card} [data-sg='key-delete']").count(), 0)  # действующий удалить нельзя
        self.page.click(f"{card} [data-sg='key-revoke']")
        self.click_modal("confirm")
        self.page.wait_for_selector(f"{card}.is-revoked")
        self.page.click(f"{card} [data-sg='key-delete']")
        self.assertIn("безвозвратно", self.modal_text())
        self.click_modal("confirm")
        self.page.wait_for_selector(card, state="detached")
        self.assertEqual(self.store.list(), [])
        config_cards = self.page.locator(".sg-key-card").all_inner_texts()
        self.assertEqual(len(config_cards), 1)
        self.assertIn(".env", config_cards[0])
        self.assertIn("управляется через конфигурацию", config_cards[0])
        self.assertEqual(self.page.locator(".sg-key-card [data-sg='key-revoke']").count(), 0)

    def test_dangerous_text_is_escaped(self):
        self.open_storage()
        akid, _ = self.create_key_via_ui(name=XSS, desc=XSS)
        self.page.click("#sm-done")
        self.wait_modal_closed()
        card = f'.sg-key-card[data-key-id="{akid}"]'
        self.assertIn(XSS, self.text(card))
        self.assertIsNone(self.page.evaluate("window.__xss"))
        self.assertEqual(self.page.locator(".sg-key-card img").count(), 0)

    def test_empty_state(self):
        self.app.static_key_ids = []
        self.open_storage("keys")
        self.page.wait_for_selector('[data-empty="keys"]')
        self.assertIn("Ключей пока нет", self.text('[data-empty="keys"]'))
        self.assertIn("не хранится", self.page.text_content(".sg-keys-head .sg-tip-pop"))  # пояснение — в плашке
        self.assertEqual(self.page.locator(".sg-keys-block").count(), 1)


try:
    import boto3  # noqa: F401
except ImportError:
    boto3 = None


@unittest.skipIf(boto3 is None, "boto3 не установлен: pip install boto3")
class Boto3KeyFlowTests(UiTestCase):
    """Критерий приёмки: ключ, созданный в интерфейсе, работает с настоящим boto3, а после Revoke — нет."""

    def test_key_created_in_ui_with_boto3_then_revoked(self):
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location("s3_key_check", Path(__file__).resolve().parents[1] / "scripts" / "s3_key_check.py")
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)
        self.core.create_bucket("other-bucket")
        self.open_storage()
        akid, secret = self.create_key_via_ui("boto3-key", buckets=("files",),
                                              perms=("ListBucket", "ReadObject", "WriteObject", "DeleteObject", "MultipartUpload"))
        self.page.click("#sm-done")
        self.wait_modal_closed()
        endpoint = f"{self.server.url}/s3"
        for legacy in (False, True):
            try:
                results = checker.check_key(endpoint, akid, secret, "us-east-1", "files", legacy_checksums=legacy,
                                            other_bucket="other-bucket", log=lambda *_: None)
            except checker._base().SkipVariant:
                continue
            failed = [f"{n}: {d}" for n, ok, d in results if not ok]
            self.assertEqual(failed, [], "\n".join(failed))
        self.page.click(f'.sg-key-card[data-key-id="{akid}"] [data-sg="key-revoke"]')
        self.click_modal("confirm")
        self.page.wait_for_selector(f'.sg-key-card.is-revoked[data-key-id="{akid}"]')
        failed = [f"{n}: {d}" for n, ok, d in checker.check_revoked(endpoint, akid, secret, "us-east-1", "files", log=lambda *_: None) if not ok]
        self.assertEqual(failed, [], "\n".join(failed))


class ConfigurationUiTests(UiTestCase):
    def setUp(self):
        super().setUp()
        self.env.write_text(f"STORAGE_TELEGRAM_BOT_TOKEN=123456789:{'T' * 35}\nCLOUD_STORAGE_CHAT_ID=-100123\n"
                            "TELEGRAM_BOT_TOKEN=111111:MAINTOKENMAINTOKENMAINTOKENMAIN123\n", encoding="utf-8")
        self.token = "T" * 35

    def test_token_is_masked_and_never_in_page_or_api(self):
        bodies: list[str] = []
        self.page.on("response", lambda r: bodies.append(r.text()) if "/api/storage/config" in r.url else None)
        self.open_storage("config")
        self.page.wait_for_selector('[data-field-key="telegram_bot_token"]')
        state = self.text('[data-secret-state="telegram_bot_token"]')
        self.assertIn("••••••••TTTT", state)
        self.assertNotIn(self.token, self.page.content())
        self.assertNotIn(self.token, self.page.inner_text("body"))
        self.assertTrue(all(self.token not in b and "MAINTOKEN" not in b for b in bodies))
        self.assertEqual(self.page.locator('input[type="password"]').count(), 0)  # поле токена скрыто, пока не нажали «Заменить»
        self.assertEqual(self.page.input_value("#cfg-telegram_chat_id"), "-100123")
        endpoint = self.page.inner_text("#sg-endpoint")
        self.assertEqual(endpoint, f"{self.server.url}/s3")
        self.assertIn("path-style", self.text("[data-endpoint]"))
        self.assertIn("Host", self.page.text_content("[data-endpoint]"))  # пояснение лежит в скрытой плашке

    def test_preview_cancel_apply_and_restart_notice(self):
        before = self.env.read_text(encoding="utf-8")
        self.open_storage("config")
        page = self.page
        page.wait_for_selector("#cfg-telegram_chat_id")
        page.fill("#cfg-telegram_chat_id", "-100999")
        page.select_option("#cfg-s3_enabled", "false")
        page.fill("#cfg-s3_region", "eu-west-1")
        page.click('[data-sg="secret-mode"][data-mode="replace"]')
        new_token = "987654321:" + "N" * 35
        page.fill("#cfg-telegram_bot_token", new_token)
        page.click('[data-sg="cfg-preview"]')
        page.wait_for_selector(".sg-diff")
        diff = self.modal_text()
        for expected in ("-100123", "-100999", "us-east-1", "eu-west-1", "••••••••TTTT", "••••••••NNNN", "S3 API включён"):
            self.assertIn(expected, diff)
        self.assertNotIn(new_token, diff)
        self.assertIn("перезапуска", diff)
        self.click_modal("close")
        self.wait_modal_closed()
        self.assertEqual(self.env.read_text(encoding="utf-8"), before)  # отмена ничего не записала
        page.click('[data-sg="cfg-preview"]')
        page.wait_for_selector(".sg-diff")
        self.click_modal("apply")
        self.wait_modal_closed()
        page.wait_for_selector('[data-banner="restart"]')
        written = self.env.read_text(encoding="utf-8")
        self.assertIn("CLOUD_STORAGE_CHAT_ID=-100999", written)
        self.assertIn("S3_REGION=eu-west-1", written)
        self.assertIn("S3_ENABLED=false", written)
        self.assertIn(f"STORAGE_TELEGRAM_BOT_TOKEN={new_token}", written)
        self.assertIn("TELEGRAM_BOT_TOKEN=111111:MAINTOKEN", written)
        self.assertNotIn(new_token, page.content())
        self.assertEqual(page.locator("[data-applied]").count(), 1)
        self.assertIn("не перезапускался", self.text("[data-applied]"))
        self.assertEqual(self.ui.restart_calls, 0)  # скрытого перезапуска нет
        self.assertTrue(page.locator('[data-field-key="s3_region"] .st-badge.is-warn').count())
        page.click('[data-banner="restart"] [data-sg="restart"]')
        self.assertIn("перезапущен", self.modal_text())
        self.click_modal("close")
        self.wait_modal_closed()
        self.assertEqual(self.ui.restart_calls, 0)  # без подтверждения перезапуска нет
        page.click('[data-banner="restart"] [data-sg="restart"]')
        self.click_modal("confirm")
        self.wait_modal_closed()
        self.assertEqual(self.ui.restart_calls, 1)  # только по явному подтверждению

    def test_validation_errors_are_shown_inline_and_nothing_is_saved(self):
        before = self.env.read_text(encoding="utf-8")
        self.open_storage("config")
        page = self.page
        page.wait_for_selector("#cfg-telegram_chat_id")
        page.fill("#cfg-telegram_chat_id", "not-a-chat")
        page.fill("#cfg-s3_path_prefix", "/api")
        page.fill("#cfg-s3_region", "Mars")
        page.click('[data-sg="cfg-preview"]')
        page.wait_for_selector('[data-cfg-error="telegram_chat_id"]:not([hidden])')
        for key, fragment in (("telegram_chat_id", "chat id"), ("s3_path_prefix", "занят"), ("s3_region", "us-east-1")):
            self.assertIn(fragment, page.inner_text(f'[data-cfg-error="{key}"]'))
        self.assertEqual(page.locator(".tk-modal").count(), 0)
        self.assertEqual(self.env.read_text(encoding="utf-8"), before)

    def test_no_changes_and_clear_token_flow(self):
        self.open_storage("config")
        page = self.page
        page.wait_for_selector("#cfg-telegram_chat_id")
        page.click('[data-sg="cfg-preview"]')
        page.wait_for_function("document.querySelector('#sg-msg') && document.querySelector('#sg-msg').textContent.includes('Изменений нет')")
        page.click('[data-sg="secret-mode"][data-mode="clear"]')
        page.click('[data-sg="cfg-preview"]')
        page.wait_for_selector(".sg-diff")
        self.assertIn("основной токен", self.modal_text())
        self.click_modal("apply")
        self.wait_modal_closed()
        self.assertNotIn("T" * 35, self.env.read_text(encoding="utf-8"))

    def test_layout_save_bar_and_chat_id_in_telegram_group(self):
        self.open_storage("config")
        page = self.page
        page.wait_for_selector("#cfg-telegram_chat_id")
        self.assertEqual(page.inner_text('[data-field-key="telegram_chat_id"] .st-item-title'), "ID чата хранения")
        self.assertEqual(page.evaluate("document.querySelector('[data-field-key=telegram_chat_id]').previousElementSibling.dataset.fieldKey"),
                         "telegram_bot_token")
        self.assertEqual(page.inner_text('[data-sg="cfg-preview"]'), "Сохранить")
        self.assertEqual(page.evaluate("getComputedStyle(document.querySelector('.sg-save')).position"), "sticky")
        self.assertEqual(page.locator('[data-sg="copy-endpoint"]').inner_text().strip(), "")  # только значок
        self.assertEqual(page.locator('[data-sg="test-saved-telegram"]').count(), 0)  # без правок Telegram кнопки проверки нет

    def test_telegram_check_appears_after_saving_telegram_settings(self):
        tg = MockTelegram().start()
        self.addCleanup(tg.stop)
        self.settings.telegram_api_base = tg.api_base
        self.settings.storage_read_timeout = 2
        from tests.storage_helpers import CHAT, TOKEN as MOCK_TOKEN
        self.env.write_text(f"STORAGE_TELEGRAM_BOT_TOKEN={MOCK_TOKEN}\nCLOUD_STORAGE_CHAT_ID=-100777\n", encoding="utf-8")
        self.open_storage("config")
        page = self.page
        page.wait_for_selector("#cfg-telegram_chat_id")
        page.fill("#cfg-s3_region", "eu-west-1")  # правка не про Telegram: кнопки проверки не будет
        page.click('[data-sg="cfg-preview"]')
        page.wait_for_selector(".sg-diff")
        self.click_modal("apply")
        self.wait_modal_closed()
        page.wait_for_selector("[data-applied]")
        self.assertEqual(page.locator('[data-sg="test-saved-telegram"]').count(), 0)
        page.fill("#cfg-telegram_chat_id", str(CHAT))
        page.click('[data-sg="cfg-preview"]')
        page.wait_for_selector(".sg-diff")
        self.click_modal("apply")
        self.wait_modal_closed()
        page.wait_for_selector('[data-sg="test-saved-telegram"]')
        page.click('[data-sg="test-saved-telegram"] >> nth=0')
        page.wait_for_selector("[data-test-result]")
        result = self.text("[data-test-result]")
        self.assertIn("polaris_test_bot", result)
        self.assertIn("не применена", result)
        self.assertNotIn(MOCK_TOKEN, page.content())
        self.env.write_text(f"STORAGE_TELEGRAM_BOT_TOKEN=123456:{'Z' * 35}\nCLOUD_STORAGE_CHAT_ID={CHAT}\n", encoding="utf-8")
        page.click('[data-sg="test-saved-telegram"] >> nth=0')
        page.wait_for_function("!document.querySelector('[data-test-result]').classList.contains('is-ok')")
        self.assertIn("is-error", page.get_attribute("[data-test-result]", "class"))
        self.assertNotIn("Z" * 35, page.content())


class DiagnosticsUiTests(UiTestCase):
    def test_health_checks_with_timing(self):
        self.open_storage("diagnostics")
        page = self.page
        page.wait_for_selector("tr[data-check='credentials']")
        page.click('[data-sg="run-all-checks"]')
        page.wait_for_function("document.querySelectorAll(\"tr[data-check] .st-badge[data-status='ok']\").length === 5")
        labels = page.eval_on_selector_all("tr[data-check] td:first-child", "els => els.map(e => e.textContent.trim())")
        self.assertEqual(labels, ["SQLite Storage Database", "Storage Core", "Telegram Backend", "S3 API", "CredentialProvider"])
        for name in ("database", "core", "telegram", "s3", "credentials"):
            row = self.text(f"tr[data-check='{name}']")
            self.assertRegex(row, r"\d+ мс")
            self.assertRegex(row, r"\d{2}\.\d{2}\.\d{4}")

    def test_storage_tests_run_and_clean_up(self):
        self.open_storage("diagnostics")
        page = self.page
        page.click('[data-sg="run-tests"]')
        page.wait_for_selector("#sg-tests [data-test-table]")
        steps = page.eval_on_selector_all("[data-step-row]", "els => els.map(e => [e.dataset.stepRow, e.querySelector('.st-badge').dataset.status])")
        self.assertEqual(steps, [[s, "ok"] for s in ("config", "telegram", "write", "read", "hash", "delete", "s3")])
        self.assertIn("Все шаги пройдены", self.text(".sg-tests-head"))  # статус — в шапке блока
        self.assertRegex(self.text(".sg-tests-head"), r"завершено \d{2}\.\d{2}\.\d{4}")
        self.assertNotIn("Все шаги пройдены", self.text("#sg-tests"))
        page.click(".sg-tests-head .sg-info")
        self.assertIn("синтетические данные", page.inner_text(".sg-tests-head .sg-tip-pop"))
        self.assertEqual(self.db.connection().execute("SELECT COUNT(*) FROM storage_objects").fetchone()[0], 0)
        self.assertEqual(self.driver.blobs, {})
        page.click('[data-sg="run-step"][data-step="hash"]')
        page.wait_for_function("document.querySelectorAll('[data-step-row]').length === 3")
        self.assertEqual(page.eval_on_selector_all("[data-step-row]", "els => els.map(e => e.dataset.stepRow)"), ["write", "read", "hash"])

    def test_failed_test_is_reported_and_cleaned(self):
        original = self.driver.download
        self.driver.download = lambda ref: b"corrupted" + original(ref)[9:]
        self.open_storage("diagnostics")
        self.page.click('[data-sg="run-tests"]')
        self.page.wait_for_selector("[data-test-table]")
        statuses = self.page.eval_on_selector_all("[data-step-row]", "els => Object.fromEntries(els.map(e => [e.dataset.stepRow, e.querySelector('.st-badge').dataset.status]))")
        self.assertEqual(statuses["read"], "error")
        self.assertIn("Есть ошибки", self.text(".sg-tests-head"))
        self.driver.download = original
        self.assertEqual(self.db.connection().execute("SELECT COUNT(*) FROM storage_objects").fetchone()[0], 0)

    def test_multipart_listing_and_abort_with_confirmation(self):
        up = self.core.multipart.create("files", "stuck/upload.bin")
        with self.core.multipart.begin_part(up.id, 1) as s:
            s.write(b"p" * 4000)
            s.complete()
        self.open_storage("diagnostics")
        page = self.page
        page.wait_for_selector(f'tr[data-upload="{up.id}"]')
        row = self.text(f'tr[data-upload="{up.id}"]')
        for expected in ("files", "stuck/upload.bin", up.id[:8], "1 · 3.91", "open"):
            self.assertIn(expected, row.replace("\xa0", " "))
        self.assertIn("не отменяются автоматически", self.text())
        page.click(f'tr[data-upload="{up.id}"] [data-sg="abort-upload"]')
        self.click_modal("close")
        self.wait_modal_closed()
        self.assertEqual(len(self.core.multipart.list_all()), 1)  # без подтверждения ничего не удалено
        page.click(f'tr[data-upload="{up.id}"] [data-sg="abort-upload"]')
        self.click_modal("confirm")
        self.wait_modal_closed()
        page.wait_for_function("!document.querySelector('[data-upload]')")
        self.assertNotIn("Multipart Uploads", self.text())  # когда загрузок нет, блока нет вообще
        self.assertNotIn("Незавершённых загрузок нет", self.page.text_content("#storage-container"))
        self.assertEqual(self.core.multipart.list_all(), [])
        self.assertEqual(self.driver.blobs, {})

    def test_recovery_is_read_only_and_hides_references(self):
        from polaris.storage import repository as repo
        with self.db.transaction() as c:
            repo.insert_orphan(c, "memory", "db_write_lost", marker="polaris-storage:v1 blob=abc chunk=0",
                               reference='{"file_id":"SECRETFILEID","message_id":99,"chat_id":-100}')
        self.open_storage("diagnostics")
        self.page.wait_for_selector("[data-empty='orphans'], .sg-table")
        body = self.page.content()
        for forbidden in ("SECRETFILEID", "message_id", "chat_id"):
            self.assertNotIn(forbidden, body)
        text = self.text()
        self.assertIn("Recovery", text)
        self.assertIn("db_write_lost", text)
        self.assertIn("Только просмотр", self.page.text_content("#storage-container"))  # пояснение лежит в скрытой плашке
        self.assertNotIn("Только просмотр", text)
        self.assertEqual(self.page.locator("button", has_text="Delete message").count(), 0)


class RobustnessUiTests(UiTestCase):
    def test_api_failure_is_shown(self):
        self.page.route("**/api/storage/status", lambda r: r.fulfill(status=503, json={"detail": "Storage недоступен: тест"}))
        self.open_storage()
        self.page.wait_for_selector(".st-error")
        self.assertIn("Storage недоступен: тест", self.text(".st-error"))

    def test_admin_api_rejects_requests_without_credentials_in_the_browser(self):
        self.page.goto(f"{self.server.url}/?token={TOKEN}")
        result = self.page.evaluate("""async () => {
          const out = {};
          for (const [name, headers] of [['none', {}], ['wrong', {'X-Polaris-Token': 'nope'}],
               ['s3', {'Authorization': 'AWS4-HMAC-SHA256 Credential=PLRSX/20261115/us-east-1/s3/aws4_request, SignedHeaders=host, Signature=00'}]]) {
            const r = await fetch('/api/storage/credentials', {headers}); out[name] = r.status;
          }
          return out;
        }""")
        self.assertEqual(result, {"none": 401, "wrong": 401, "s3": 401})

    def test_mobile_layout_has_no_horizontal_overflow(self):
        self.context.close()
        self.context = self.browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page.on("pageerror", lambda e: self.js_errors.append(str(e)))
        self.page.set_default_timeout(8000)
        self.core.put_object("files", "a/very/long/key/" + "x" * 80 + ".bin", b"1")
        self.store.create("k", "d", ["ReadObject"], None)
        self.page.goto(f"{self.server.url}/?token={TOKEN}")
        # на мобильных боковая панель скрыта: разделы открываются через поиск (капсула вверху)
        self.page.wait_for_selector("#search-capsule")
        self.page.click("#search-capsule")
        self.page.fill("#search-input", "Storage")
        self.page.click('.search-item[data-result-id="view:storage"]')
        self.page.wait_for_selector(".sg-tabs")
        for section in ("overview", "config", "buckets", "keys", "diagnostics"):
            self.page.click(f'.sg-tabs .st-tab[data-section="{section}"]')
            self.page.wait_for_timeout(400)
            overflow = self.page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
            self.assertLessEqual(overflow, 1, f"{section}: горизонтальная прокрутка страницы")
        self.tab("buckets")
        self.page.click('[data-sg="open-bucket"]')
        self.page.wait_for_selector("[data-object-row]")
        self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth - window.innerWidth"), 1)

    def test_leaving_the_section_closes_modals_and_discards_secrets(self):
        self.open_storage()
        akid, secret = self.create_key_via_ui()
        self.page.evaluate("document.getElementById('drawer-toggle').click()")
        self.page.click('button.drawer-item[data-view="settings"]')
        self.page.wait_for_selector("#settings-container")
        self.assertNotIn(secret, self.page.content())


if __name__ == "__main__":
    unittest.main()
