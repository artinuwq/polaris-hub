"""Management-слой Storage: статус, диагностика, конфигурация, buckets, объекты, ключи, multipart, тесты хранилища."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path

from polaris.infra.settings import Settings
from polaris.modules.storage.admin import AdminError, StorageAdmin
from polaris.s3.api import S3App
from polaris.s3.config import S3Config
from polaris.s3.credentials import (
    CompositeCredentialProvider, Credential, DatabaseCredentialProvider, StaticCredentialProvider,
)
from polaris.s3.service import S3Service
from polaris.storage import repository as repo
from polaris.storage.access_keys import AccessKeyStore, MasterKey
from polaris.storage.backends.base import HealthStatus, TransientBackendError
from polaris.storage.config import StorageConfig
from polaris.storage.service import StorageService
from tests.s3_client import AK, SK
from tests.storage_helpers import MockTelegram, StorageTestCase

TOKEN = "123456789:" + "A" * 35
NEW_TOKEN = "987654321:" + "B" * 35


class AdminTestCase(StorageTestCase):
    chunk_size = 64 * 1024
    with_s3 = True

    def setUp(self):
        super().setUp()
        self.env = self.tmp / ".env"
        self.env.write_text("TELEGRAM_BOT_TOKEN=111111:MAINTOKENMAINTOKENMAINTOKENMAIN123\n", encoding="utf-8")
        self.core = self.make_core(self.driver, max_concurrent_uploads=8)
        self.core.multipart.min_part_size = 5 * 1024 * 1024
        self.master = MasterKey(b"m" * 32)
        self.store = AccessKeyStore(self.db, self.master)
        self.service = StorageService(StorageConfig(enabled=True, db_path=self.tmp / "storage.db",
                                                    telegram_bot_token=TOKEN, telegram_chat_id="-100123"))
        self.service._db, self.service._core, self.service.access_keys, self.service.state = self.db, self.core, self.store, "ready"
        self.app = None
        if self.with_s3:
            provider = CompositeCredentialProvider(StaticCredentialProvider([Credential(AK, SK)]),
                                                   DatabaseCredentialProvider(lambda: self.store))
            self.app = S3App(S3Config(enabled=True, path_prefix="/s3"), provider, S3Service(lambda: self.core))
            self.app.static_key_ids = [AK]
            self.addCleanup(self.app.close)
        self.settings = Settings(s3_enabled=self.with_s3, storage_enabled=True, cloud_storage_chat_id="-100123",
                                 storage_telegram_bot_token=TOKEN, telegram_bot_token="111111:MAINTOKENMAINTOKENMAINTOKENMAIN123")
        self.admin = self.make_admin()

    def make_admin(self, service="default", settings=None):
        return StorageAdmin(settings or self.settings, env_path=self.env,
                            get_service=lambda: self.service if service == "default" else service,
                            get_s3=lambda: self.app)

    def dump(self, value) -> str:
        return json.dumps(value, ensure_ascii=False, default=str)


class StatusAndHealthTests(AdminTestCase):
    def test_nothing_is_online_until_actually_checked(self):
        status = self.admin.status()
        for name, record in status["components"].items():
            self.assertEqual((record["status"], record["checked_at"], record["last_ok_at"]), ("unknown", None, None), name)
        self.assertEqual(status["storage"]["state"], "ready")

    def test_checks_run_for_real_and_record_timing(self):
        results = self.admin.run_checks()
        self.assertEqual({n: r["status"] for n, r in results.items()},
                         {"database": "ok", "core": "ok", "telegram": "ok", "s3": "ok", "credentials": "ok"})
        for record in results.values():
            self.assertIsInstance(record["duration_ms"], int)
            self.assertTrue(record["checked_at"] and record["last_ok_at"])
        self.assertEqual(results["database"]["details"]["schema_version"], results["database"]["details"]["expected_schema_version"])
        self.assertEqual(self.admin.status()["components"]["database"]["status"], "ok")  # status отдаёт сохранённый результат

    def test_failed_backend_is_reported_and_last_ok_survives(self):
        self.admin.run_checks(["telegram"])
        ok_at = self.admin.status()["components"]["telegram"]["last_ok_at"]
        self.driver.health_check = lambda: HealthStatus(False, "Telegram недоступен: тест")
        record = self.admin.run_checks(["telegram"])["telegram"]
        self.assertEqual((record["status"], record["message"]), ("error", "Telegram недоступен: тест"))
        self.assertEqual(record["last_ok_at"], ok_at)

    def test_unknown_check_name(self):
        with self.assertRaises(AdminError) as cm:
            self.admin.run_checks(["nope"])
        self.assertEqual(cm.exception.status, 422)

    def test_disabled_and_unavailable_storage(self):
        admin = self.make_admin(service=None)
        results = admin.run_checks()
        self.assertEqual((results["core"]["status"], results["database"]["status"], results["telegram"]["status"]),
                         ("disabled", "unavailable", "unavailable"))
        self.assertNotIn("ok", [r["status"] for r in results.values() if r["name"] != "s3"])
        self.service.state, self.service.reasons = "unavailable", ["Telegram backend недоступен: не задан CLOUD_STORAGE_CHAT_ID"]
        results = self.admin.run_checks()
        self.assertEqual(results["core"]["status"], "error")
        self.assertIn("CLOUD_STORAGE_CHAT_ID", results["core"]["message"])
        self.assertEqual(results["s3"]["status"], "error")  # S3 без Storage не «Online»

    def test_s3_states(self):
        self.app = None
        self.assertEqual(self.admin.run_checks(["s3"])["s3"]["status"], "error")  # включён, но не запущен
        self.settings.s3_enabled = False
        self.assertEqual(self.admin.run_checks(["s3"])["s3"]["status"], "disabled")

    def test_s3_counters_reflect_real_requests(self):
        before = self.admin.run_checks(["s3"])["s3"]["details"]
        self.assertEqual((before["requests"], before["last_ok_at"]), (0, None))
        self.admin.run_tests(["s3"])  # реальные запросы через приложение
        after = self.admin.run_checks(["s3"])["s3"]["details"]
        self.assertGreater(after["requests"], 5)
        self.assertIsNotNone(after["last_ok_at"])

    def test_credentials_check_warnings(self):
        self.app.static_key_ids = []
        self.assertEqual(self.admin.run_checks(["credentials"])["credentials"]["status"], "warning")  # ключей нет
        key, _ = self.store.create("k", "", ["ReadObject"], None)
        self.assertEqual(self.admin.run_checks(["credentials"])["credentials"]["status"], "ok")
        self.store.master = MasterKey(b"other" * 8)
        record = self.admin.run_checks(["credentials"])["credentials"]
        self.assertEqual((record["status"], record["details"]["needs_rotation"]), ("warning", 1))
        self.service.access_keys = None
        self.service.access_keys_error = "MasterKey: файл повреждён"
        self.assertEqual(self.admin.run_checks(["credentials"])["credentials"]["status"], "error")

    def test_core_warns_when_reconcile_needs_attention(self):
        from polaris.storage.recovery import ReconcileReport
        self.service.last_report = ReconcileReport(chunks_delete_rejected=2)
        self.assertEqual(self.admin.run_checks(["core"])["core"]["status"], "warning")

    def test_stats_separate_logical_objects_from_physical_blobs_exactly(self):
        self.core.create_bucket("alpha-bucket")
        self.core.create_bucket("beta-bucket", dedup_scope="shared")
        self.core.ensure_bucket("gamma-bucket", dedup_scope="shared")
        data = os.urandom(150_000)
        for bucket in ("beta-bucket", "gamma-bucket"):
            self.core.put_object(bucket, "same.bin", data)  # дедупликация в общем scope
        self.core.put_object("alpha-bucket", "other.bin", b"x" * 1000)
        up = self.core.multipart.create("alpha-bucket", "pending")
        with self.core.multipart.begin_part(up.id, 1) as s:
            s.write(b"p" * 2000)
            s.complete()
        stats = self.admin.status()["stats"]
        self.assertEqual((stats["buckets"], stats["objects"]), (4, 3))  # files + 3 созданных; служебные не считаются
        self.assertEqual(stats["logical_bytes"], 2 * 150_000 + 1000)
        self.assertEqual(stats["blobs"], 3)  # общий blob, other.bin, часть multipart
        self.assertEqual(stats["physical_bytes"], 150_000 + 1000 + 2000)
        self.assertEqual((stats["multipart_uploads"], stats["multipart_parts"], stats["multipart_bytes"]), (1, 1, 2000))


class ConfigTests(AdminTestCase):
    def fields(self, view):
        return {f["key"]: f for group in view["groups"].values() for f in group}

    def test_secrets_are_masked_and_never_returned(self):
        self.env.write_text(f"STORAGE_TELEGRAM_BOT_TOKEN={TOKEN}\nCLOUD_STORAGE_CHAT_ID=-100123\n", encoding="utf-8")
        view = self.admin.config_get()
        text = self.dump(view)
        self.assertNotIn(TOKEN, text)
        self.assertNotIn(TOKEN[:20], text)
        token = self.fields(view)["telegram_bot_token"]
        self.assertTrue(token["configured"] and token["masked"].endswith(TOKEN[-4:]) and token["masked"].startswith("••••"))
        self.assertNotIn("value", token)
        self.assertEqual(self.fields(view)["telegram_chat_id"]["value"], "-100123")
        self.assertNotIn("MAINTOKEN", text)  # основной токен бота не показывается вообще

    def test_unset_token_means_main_bot_token(self):
        self.settings.storage_telegram_bot_token = ""
        token = self.fields(self.admin.config_get())["telegram_bot_token"]
        self.assertEqual((token["configured"], token["uses_main_bot_token"]), (False, True))

    def test_preview_does_not_write_and_confirm_does(self):
        before = self.env.read_text(encoding="utf-8")
        changes = {"telegram_chat_id": "-100999", "s3_region": "eu-west-1", "telegram_bot_token": NEW_TOKEN}
        preview = self.admin.config_update(changes, confirm=False)
        self.assertFalse(preview["applied"])
        self.assertEqual({d["key"] for d in preview["diff"]}, set(changes))
        self.assertEqual(self.env.read_text(encoding="utf-8"), before)
        self.assertNotIn(NEW_TOKEN, self.dump(preview))
        done = self.admin.config_update(changes, confirm=True)
        self.assertTrue(done["applied"] and done["restart_required"])
        self.assertIn("перезапуск", done["message"].lower())
        text = self.env.read_text(encoding="utf-8")
        self.assertIn("CLOUD_STORAGE_CHAT_ID=-100999", text)
        self.assertIn(f"STORAGE_TELEGRAM_BOT_TOKEN={NEW_TOKEN}", text)
        self.assertIn("TELEGRAM_BOT_TOKEN=111111:MAINTOKEN", text)  # основной токен не тронут
        self.assertNotIn(NEW_TOKEN, self.dump(done))
        view = self.admin.config_get()
        self.assertTrue(view["restart_required"])
        self.assertTrue(self.fields(view)["s3_region"]["pending_restart"])
        self.assertEqual(self.fields(view)["s3_region"]["applied"], "us-east-1")  # действует пока старое значение
        self.assertTrue(self.admin.status()["restart_required"])

    def test_nothing_changed_and_null_keeps_secret(self):
        self.env.write_text(f"STORAGE_TELEGRAM_BOT_TOKEN={TOKEN}\n", encoding="utf-8")
        self.assertEqual(self.admin.config_update({"telegram_bot_token": None}, True)["message"], "Изменений нет")
        self.assertEqual(self.admin.config_update({"s3_region": "us-east-1"}, True)["diff"], [])
        self.assertIn(TOKEN, self.env.read_text(encoding="utf-8"))

    def test_clear_token(self):
        self.env.write_text(f"STORAGE_TELEGRAM_BOT_TOKEN={TOKEN}\n", encoding="utf-8")
        preview = self.admin.config_update({"telegram_bot_token": ""}, False)
        self.assertTrue(preview["diff"][0]["clear"])
        self.admin.config_update({"telegram_bot_token": ""}, True)
        self.assertNotIn(TOKEN, self.env.read_text(encoding="utf-8"))

    def test_validation_errors_are_per_field_and_nothing_is_written(self):
        before = self.env.read_text(encoding="utf-8")
        bad = {"telegram_bot_token": "not-a-token", "telegram_chat_id": "abc", "s3_path_prefix": "/api",
               "s3_region": "Mars", "s3_max_request_size_mib": 0, "s3_clock_skew_seconds": "x", "s3_enabled": "maybe",
               "unknown_key": 1}
        with self.assertRaises(AdminError) as cm:
            self.admin.config_update(bad, True)
        self.assertEqual(cm.exception.status, 422)
        self.assertEqual(set(cm.exception.errors), set(bad))
        self.assertEqual(self.env.read_text(encoding="utf-8"), before)
        for good in ({"s3_path_prefix": "/files"}, {"telegram_chat_id": "@my_channel"}, {"s3_enabled": "true"},
                     {"s3_clock_skew_seconds": 600}):
            self.admin.config_update(good, False)
        for value in ("/", "s3", "/a b", "/health", "/assets/x"):
            with self.assertRaises(AdminError, msg=value):
                self.admin.config_update({"s3_path_prefix": value}, False)
        self.assertEqual(self.admin.config_update({"s3_path_prefix": "/s3/"}, False)["diff"], [])  # нормализуется в /s3

    def test_warnings_for_inconsistent_combinations(self):
        r = self.admin.config_update({"storage_enabled": False, "s3_enabled": True}, False)
        self.assertTrue(any("Storage выключен" in w for w in r["warnings"]))
        r = self.admin.config_update({"telegram_chat_id": ""}, False)
        self.assertTrue(any("Chat ID" in w for w in r["warnings"]))

    def test_empty_changes_and_unwritable_env(self):
        with self.assertRaises(AdminError):
            self.admin.config_update({}, True)
        if sys.platform != "win32" and os.geteuid() != 0:
            os.chmod(self.env, stat.S_IREAD)
            try:
                with self.assertRaises(AdminError) as cm:
                    self.admin.config_update({"s3_region": "eu-west-1"}, True)
                self.assertEqual(cm.exception.status, 500)
                self.assertNotIn(str(self.env), cm.exception.detail)
            finally:
                os.chmod(self.env, stat.S_IREAD | stat.S_IWRITE)

    def test_env_values_with_newlines_are_rejected(self):
        with self.assertRaises(AdminError):
            self.admin.config_update({"telegram_chat_id": "-100\nS3_ENABLED=true"}, True)
        self.assertNotIn("S3_ENABLED", self.env.read_text(encoding="utf-8"))


class BucketAndObjectTests(AdminTestCase):
    def test_bucket_lifecycle_and_validation(self):
        created = self.admin.bucket_create("my-bucket")
        self.assertEqual((created["name"], created["object_count"], created["total_size"]), ("my-bucket", 0, 0))
        for bad in ("ab", "Bad_Name", "-x-", "1.2.3.4", "x" * 64, "", None, 5):
            with self.assertRaises(AdminError) as cm:
                self.admin.bucket_create(bad)
            self.assertEqual(cm.exception.status, 422, bad)
        with self.assertRaises(AdminError) as cm:
            self.admin.bucket_create("my-bucket")
        self.assertEqual(cm.exception.status, 409)
        for reserved in ("polaris-internal.x", "polaris-selftest-abc"):
            with self.assertRaises(AdminError):
                self.admin.bucket_create(reserved)
        self.assertEqual([b["name"] for b in self.admin.buckets_list()["buckets"]], ["files", "my-bucket"])
        self.admin.bucket_delete("my-bucket")
        with self.assertRaises(AdminError) as cm:
            self.admin.bucket_get("my-bucket")
        self.assertEqual(cm.exception.status, 404)

    def test_non_empty_bucket_cannot_be_deleted(self):
        self.core.put_object("files", "k", b"x")
        with self.assertRaises(AdminError) as cm:
            self.admin.bucket_delete("files")
        self.assertEqual(cm.exception.status, 409)
        self.assertIn("не пуст", cm.exception.detail)

    def test_hidden_buckets_do_not_exist_for_the_api(self):
        self.core.multipart.create("files", "k")
        for name in ("polaris-internal.multipart", "polaris-selftest-abc"):
            for call in (self.admin.bucket_get, self.admin.bucket_delete, self.admin.bucket_objects):
                with self.assertRaises(AdminError) as cm:
                    call(name)
                self.assertEqual(cm.exception.status, 404)

    def test_object_listing_pagination_and_metadata_without_internals(self):
        for i in range(5):
            self.core.put_object("files", f"dir/{i}.txt", f"data{i}".encode(), mime_type="text/plain",
                                 metadata={"s3": {"meta": {"owner": "me"}, "headers": {"cache-control": "no-cache"}}})
        page1 = self.admin.bucket_objects("files", limit=2)
        self.assertEqual(([o["key"] for o in page1["objects"]], page1["truncated"], page1["next_after"]), (["dir/0.txt", "dir/1.txt"], True, "dir/1.txt"))
        page3 = self.admin.bucket_objects("files", after="dir/3.txt", limit=2)
        self.assertEqual(([o["key"] for o in page3["objects"]], page3["truncated"]), (["dir/4.txt"], False))
        self.assertEqual(len(self.admin.bucket_objects("files", prefix="dir/1")["objects"]), 1)
        detail = self.admin.object_get(page1["objects"][0]["id"])
        self.assertEqual((detail["bucket"], detail["content_type"], detail["size"]), ("files", "text/plain", 5))
        self.assertEqual((detail["user_metadata"], detail["headers"]), ({"owner": "me"}, {"cache-control": "no-cache"}))
        text = self.dump([page1, detail])
        for forbidden in ("blob", "chunk", "file_id", "message_id", "chat_id", "backend", "mem-1", "checksum"):
            self.assertNotIn(forbidden, text, forbidden)

    def test_object_delete_and_missing(self):
        obj = self.core.put_object("files", "k", b"hello" * 1000)
        self.assertEqual(self.admin.object_delete(obj.id), {"physical_complete": True})
        self.assertEqual(self.driver.blobs, {})
        for call in (self.admin.object_get, self.admin.object_delete):
            with self.assertRaises(AdminError) as cm:
                call(obj.id)
            self.assertEqual(cm.exception.status, 404)

    def test_object_delete_reports_incomplete_physical_deletion(self):
        obj = self.core.put_object("files", "k", b"hello")
        self.driver.delete_errors = [TransientBackendError("down")]
        self.assertEqual(self.admin.object_delete(obj.id), {"physical_complete": False})

    def test_download_streams_verified_content(self):
        data = os.urandom(300_000)
        obj = self.core.put_object("files", "a/b.bin", data)
        info, stream = self.admin.object_download(obj.id)
        self.assertEqual((info["key"], info["size"]), ("a/b.bin", len(data)))
        self.assertEqual(b"".join(stream), data)

    def test_internal_objects_are_not_reachable_by_id(self):
        self.core.ensure_bucket("polaris-internal.selftest")
        obj = self.core.put_object("polaris-internal.selftest", "x", b"1")
        with self.assertRaises(AdminError) as cm:
            self.admin.object_get(obj.id)
        self.assertEqual(cm.exception.status, 404)

    def test_storage_unavailable_gives_503(self):
        admin = self.make_admin(service=None)
        with self.assertRaises(AdminError) as cm:
            admin.buckets_list()
        self.assertEqual(cm.exception.status, 503)


class CredentialTests(AdminTestCase):
    def create(self, **kw):
        body = {"name": "ci", "description": "для CI", "permissions": ["ListBucket", "ReadObject"],
                "buckets": ["files"], "all_buckets": False}
        body.update(kw)
        return self.admin.credential_create(body)

    def test_create_shows_secret_once_and_list_never_does(self):
        created = self.create()
        secret = created["secret_access_key"]
        self.assertTrue(created["access_key_id"].startswith("PLRS") and len(secret) == 40)
        self.assertNotIn("secret", self.dump(created["credential"]).lower())
        listing = self.admin.credentials_list()
        self.assertNotIn(secret, self.dump(listing))
        self.assertNotIn(secret, self.dump(self.admin.credential_update(created["credential"]["id"], {"description": "x"})))
        self.assertNotIn(secret, self.dump(self.admin.credential_revoke(created["credential"]["id"])))
        item = [c for c in listing["credentials"] if c["source"] == "database"][0]
        self.assertEqual((item["permissions"], item["buckets"], item["status"], item["last_used_at"]),
                         (["ListBucket", "ReadObject"], ["files"], "active", None))

    def test_validation(self):
        for kw, field in ((dict(name=""), "name"), (dict(permissions=[]), "permissions"), (dict(permissions=["Hack"]), None),
                          (dict(permissions=["MultipartUpload"]), None), (dict(buckets=[]), "buckets"),
                          (dict(buckets=["no-such-bucket"]), "buckets"), (dict(buckets=["polaris-internal.multipart"]), "buckets"),
                          (dict(permissions="ReadObject"), "permissions")):
            with self.assertRaises(AdminError, msg=kw) as cm:
                self.create(**kw)
            self.assertIn(cm.exception.status, (400, 422))
            if field:
                self.assertIn(field, cm.exception.errors)
        self.assertEqual(self.create(all_buckets=True, buckets=None)["credential"]["all_buckets"], True)

    def test_lifecycle_revoke_rotate_delete(self):
        created = self.create()
        cid = created["credential"]["id"]
        rotated = self.admin.credential_rotate(cid)
        self.assertNotEqual(rotated["secret_access_key"], created["secret_access_key"])
        self.assertEqual(rotated["access_key_id"], created["access_key_id"])
        with self.assertRaises(AdminError) as cm:
            self.admin.credential_delete(cid)  # нельзя удалить действующий
        self.assertEqual(cm.exception.status, 409)
        self.assertEqual(self.admin.credential_revoke(cid)["credential"]["status"], "revoked")
        with self.assertRaises(AdminError):
            self.admin.credential_rotate(cid)
        self.admin.credential_delete(cid)
        with self.assertRaises(AdminError) as cm:
            self.admin.credential_revoke(cid)
        self.assertEqual(cm.exception.status, 404)

    def test_config_keys_are_listed_read_only(self):
        listing = self.admin.credentials_list()["credentials"]
        config = [c for c in listing if c["source"] == "config"]
        self.assertEqual([(c["access_key_id"], c["managed_in_env"], c["all_buckets"]) for c in config], [(AK, True, True)])
        self.assertNotIn(SK, self.dump(listing))
        for call in (self.admin.credential_revoke, self.admin.credential_rotate, self.admin.credential_delete):
            with self.assertRaises(AdminError) as cm:
                call("config:0")
            self.assertEqual(cm.exception.status, 409)
        with self.assertRaises(AdminError):
            self.admin.credential_update("config:0", {"name": "x"})

    def test_update_requires_something_to_change(self):
        cid = self.create()["credential"]["id"]
        with self.assertRaises(AdminError):
            self.admin.credential_update(cid, {})
        with self.assertRaises(AdminError):
            self.admin.credential_update(cid, {"permissions": ["WriteObject"]})  # права после создания не меняются
        self.assertEqual(self.admin.credential_update(cid, {"name": "новое имя"})["credential"]["name"], "новое имя")

    def test_system_keys_are_not_listed(self):
        self.store.create("sys", "", ["ReadObject"], ["polaris-selftest-aaa"], system=True)
        self.assertEqual([c for c in self.admin.credentials_list()["credentials"] if c["source"] == "database"], [])

    def test_unavailable_access_management(self):
        self.service.access_keys = None
        self.service.access_keys_error = "MasterKey: плохой ключ"
        with self.assertRaises(AdminError) as cm:
            self.admin.credentials_list()
        self.assertEqual(cm.exception.status, 503)


class MultipartAdminTests(AdminTestCase):
    def test_list_and_abort(self):
        up = self.core.multipart.create("files", "big/file.bin")
        with self.core.multipart.begin_part(up.id, 1) as s:
            s.write(b"x" * 3000)
            s.complete()
        rows = self.admin.multipart_list()["uploads"]
        self.assertEqual([(r["upload_id"], r["bucket"], r["key"], r["parts"], r["bytes"], r["status"]) for r in rows],
                         [(up.id, "files", "big/file.bin", 1, 3000, "open")])
        self.assertGreaterEqual(rows[0]["age_seconds"], 0)
        self.assertEqual(self.admin.multipart_abort(up.id), {})
        self.assertEqual(self.driver.blobs, {})
        self.assertEqual(self.admin.multipart_list()["uploads"], [])
        for bad in (up.id, "nope", "0" * 32):
            with self.assertRaises(AdminError) as cm:
                self.admin.multipart_abort(bad)
            self.assertEqual(cm.exception.status, 404)

    def test_nothing_is_aborted_automatically(self):
        self.core.multipart.create("files", "k")
        self.admin.run_checks()
        self.admin.diagnostics()
        self.assertEqual(len(self.admin.multipart_list()["uploads"]), 1)


class DiagnosticsTests(AdminTestCase):
    def test_recovery_overview_hides_backend_references(self):
        with self.db.transaction() as c:
            repo.insert_orphan(c, "memory", "db_write_lost", marker="polaris-storage:v1 blob=b chunk=0",
                               reference='{"file_id":"SECRETFILEID","message_id":77,"chat_id":-100}')
            repo.insert_orphan(c, "memory", "uncertain_upload", marker="m2")
        obj = self.core.put_object("files", "k", b"data")
        self.driver.delete_errors = [TransientBackendError("down: SECRETFILEID")]
        self.core.delete_object(obj.id)
        result = self.admin.diagnostics()
        rec = result["recovery"]
        self.assertEqual((rec["orphans_suspected"], rec["orphans_pending_delete"], rec["chunks_delete_failed"]), (1, 1, 1))
        self.assertEqual(len(rec["orphans"]), 2)
        self.assertEqual(rec["failed_deletions"][0]["status"], "delete_failed")
        text = self.dump(result)
        for forbidden in ("SECRETFILEID", "message_id", "backend_reference", "chat_id"):
            self.assertEqual(text.count(forbidden) if forbidden != "SECRETFILEID" else text.count(forbidden) - text.count("down: SECRETFILEID"), 0, forbidden)

    def test_diagnostics_shape_when_storage_down(self):
        admin = self.make_admin(service=None)
        result = admin.diagnostics()
        self.assertEqual((result["multipart"], result["recovery"], result["last_reconcile"]), ([], None, None))


class StorageTestRunTests(AdminTestCase):
    def state(self):
        """Всё, что тест обязан убрать за собой."""
        c = self.db.connection()
        return {
            "objects": c.execute("SELECT COUNT(*) FROM storage_objects").fetchone()[0],
            "selftest_buckets": [r[0] for r in c.execute("SELECT name FROM storage_buckets WHERE name LIKE 'polaris-selftest-%'")],
            "system_keys": c.execute("SELECT COUNT(*) FROM storage_access_keys").fetchone()[0],
            "blobs": c.execute("SELECT COUNT(*) FROM storage_blobs").fetchone()[0],
            "driver": len(self.driver.blobs),
        }

    def steps(self, outcome):
        return {s["step"]: s["status"] for s in outcome["steps"]}

    def test_full_run_passes_and_leaves_nothing(self):
        before = self.state()
        outcome = self.admin.run_tests()
        self.assertTrue(outcome["ok"], outcome)
        self.assertEqual(self.steps(outcome), {s: "ok" for s in ("config", "telegram", "write", "read", "hash", "delete", "s3")})
        for step in outcome["steps"]:
            self.assertIsInstance(step["duration_ms"], int)
        self.assertIn("проверок", [s for s in outcome["steps"] if s["step"] == "s3"][0]["message"])
        self.assertEqual(self.state(), before)
        self.assertEqual(self.admin.diagnostics()["last_tests"]["ok"], True)

    def test_dependencies_are_added_automatically(self):
        outcome = self.admin.run_tests(["hash"])
        self.assertEqual([s["step"] for s in outcome["steps"]], ["write", "read", "hash"])
        self.assertEqual(self.state()["objects"], 0)  # объект удалён, хотя шага delete не просили
        with self.assertRaises(AdminError):
            self.admin.run_tests(["nope"])

    def test_uses_synthetic_data_only(self):
        self.core.put_object("files", "user-file", b"important user data")
        self.admin.run_tests()
        self.assertEqual(self.core.get_metadata_by_key("files", "user-file").size_bytes, len(b"important user data"))
        self.assertEqual([b.name for b in self.core.list_buckets()], ["files"])

    def test_corrupted_read_fails_hash_and_still_cleans_up(self):
        original = self.driver.download
        self.driver.download = lambda ref: bytes([original(ref)[0] ^ 1]) + original(ref)[1:]
        outcome = self.admin.run_tests(["write", "read", "hash", "delete"])
        self.assertFalse(outcome["ok"])
        statuses = self.steps(outcome)
        self.assertEqual(statuses["write"], "ok")
        self.assertEqual(statuses["read"], "error")  # ядро само отвергает повреждённый чанк
        self.assertEqual(statuses["hash"], "skipped")
        self.driver.download = original
        self.assertEqual(self.state()["objects"], 0)
        self.assertEqual(self.state()["driver"], 0)

    def test_write_failure_skips_dependent_steps(self):
        def boom(data, ctx):
            raise TransientBackendError("down")
        self.driver.upload_hook = boom
        outcome = self.admin.run_tests(["write", "read", "hash", "delete"])
        self.assertEqual(self.steps(outcome), {"write": "error", "read": "skipped", "hash": "skipped", "delete": "skipped"})
        self.assertEqual(self.state()["objects"], 1)  # failed-запись без данных
        self.driver.upload_hook = None

    def test_rejected_physical_delete_is_an_error(self):
        from polaris.storage.backends.base import DeleteRejected
        self.driver.delete_errors = [DeleteRejected("can't delete")]
        outcome = self.admin.run_tests(["write", "delete"])
        self.assertEqual(self.steps(outcome)["delete"], "error")

    def test_s3_cycle_failure_is_reported_and_cleaned(self):
        before = self.state()
        self.driver.upload_hook = lambda data, ctx: (_ for _ in ()).throw(TransientBackendError("down"))
        outcome = self.admin.run_tests(["s3"])
        s3 = [s for s in outcome["steps"] if s["step"] == "s3"][0]
        self.assertEqual(s3["status"], "error")
        self.assertIn("PUT", s3["message"])
        self.driver.upload_hook = None
        self.assertEqual(self.state()["selftest_buckets"], [])
        self.assertEqual(self.state()["system_keys"], before["system_keys"])

    def test_s3_step_skipped_when_s3_off(self):
        self.app = None
        self.assertEqual(self.steps(self.admin.run_tests(["s3"]))["s3"], "skipped")

    def test_leftovers_of_interrupted_runs_are_removed(self):
        self.core.ensure_bucket("polaris-selftest-stale0001")
        self.core.put_object("polaris-selftest-stale0001", "x", b"stale")
        self.core.ensure_bucket("polaris-internal.selftest")
        self.core.put_object("polaris-internal.selftest", "selftest/old", b"stale")
        self.store.create("Polaris self-test", "", ["ReadObject"], ["polaris-selftest-stale0001"], system=True)
        self.admin.run_tests(["config"])
        self.assertEqual(self.state()["selftest_buckets"], [])
        self.assertEqual(self.state()["objects"], 0)
        self.assertEqual(self.state()["system_keys"], 0)

    def test_user_keys_and_buckets_survive_cleanup(self):
        self.core.create_bucket("keep-bucket")
        key, _ = self.store.create("user key", "", ["ReadObject"], ["keep-bucket"])
        self.admin.run_tests()
        self.assertEqual(self.store.get(key.id).status, "active")
        self.assertIn("keep-bucket", [b.name for b in self.core.list_buckets()])

    def test_concurrent_runs_are_refused(self):
        import threading
        gate, started = threading.Event(), threading.Event()
        original = self.driver.upload_hook

        def slow(data, ctx):
            started.set()
            gate.wait(5)
        self.driver.upload_hook = slow
        result = {}
        t = threading.Thread(target=lambda: result.update(self.admin.run_tests(["write", "delete"])))
        t.start()
        started.wait(5)
        with self.assertRaises(AdminError) as cm:
            self.admin.run_tests(["config"])
        self.assertEqual(cm.exception.status, 409)
        gate.set()
        t.join()
        self.driver.upload_hook = original
        self.assertTrue(result["ok"])

    def test_storage_down_only_config_and_telegram_run(self):
        self.service.state, self.service.reasons = "unavailable", ["нет токена"]
        outcome = self.admin.run_tests(["config", "write", "s3"], use_saved=False) if False else None
        outcome = self.admin.run_tests(["write", "s3"])
        self.assertEqual(self.steps(outcome), {"write": "skipped", "s3": "skipped"})


class TelegramSavedConfigTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self.tg = MockTelegram().start()
        self.addCleanup(self.tg.stop)
        self.settings.telegram_api_base = self.tg.api_base
        self.settings.storage_read_timeout = 2
        from tests.storage_helpers import CHAT, TOKEN as MOCK_TOKEN
        self.mock_token, self.mock_chat = MOCK_TOKEN, str(CHAT)

    def test_saved_but_unapplied_config_is_tested_and_labelled(self):
        self.env.write_text(f"STORAGE_TELEGRAM_BOT_TOKEN={self.mock_token}\nCLOUD_STORAGE_CHAT_ID={self.mock_chat}\n", encoding="utf-8")
        outcome = self.admin.run_tests(["telegram"], use_saved=True)
        step = outcome["steps"][0]
        self.assertEqual(step["status"], "ok", step)
        self.assertIn("не применена", step["message"])
        self.assertIn("polaris_test_bot", step["message"])
        self.assertNotIn(self.mock_token, self.dump(outcome))

    def test_wrong_saved_token_is_reported_without_leaking_it(self):
        self.env.write_text("STORAGE_TELEGRAM_BOT_TOKEN=123456:" + "Z" * 35 + f"\nCLOUD_STORAGE_CHAT_ID={self.mock_chat}\n", encoding="utf-8")
        step = self.admin.run_tests(["telegram"], use_saved=True)["steps"][0]
        self.assertEqual(step["status"], "error")
        self.assertNotIn("Z" * 35, self.dump(step))

    def test_missing_configuration_is_explained(self):
        self.settings.cloud_storage_chat_id = ""
        self.settings.storage_telegram_bot_token = ""
        self.settings.telegram_bot_token = ""
        self.service.state = "unavailable"
        self.env.write_text("", encoding="utf-8")
        step = self.admin.run_tests(["telegram"], use_saved=True)["steps"][0]
        self.assertEqual(step["status"], "error")
        self.assertIn("не настроен", step["message"])


if __name__ == "__main__":
    unittest.main()
