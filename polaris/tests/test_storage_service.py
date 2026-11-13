"""Тесты конфигурации и запуска Storage: валидация, недоступность без падения, статус без секретов."""
from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from polaris.infra.settings import Settings
from polaris.shared.exceptions import ConfigurationError
from polaris.storage import StorageUnavailable, get_storage_service, init_storage, shutdown_storage
from polaris.storage.config import MAX_CHUNK_SIZE, MIB, PROJECT_ROOT, StorageConfig
from polaris.storage.service import StorageService
from tests.storage_helpers import CHAT, TOKEN, MockTelegram


def settings(**kw) -> Settings:
    base = dict(storage_enabled=True, telegram_bot_token=TOKEN, cloud_storage_chat_id=str(CHAT))
    base.update(kw)
    return Settings(**base)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="polaris-storage-svc-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(shutdown_storage)

    def svc(self, **kw) -> StorageService:
        kw.setdefault("storage_db_path", str(self.tmp / "s.db"))
        return StorageService(StorageConfig.from_settings(settings(**kw)))

    def test_disabled_by_default_touches_nothing(self):
        s = StorageService(StorageConfig.from_settings(Settings(storage_db_path=str(self.tmp / "s.db"))))
        s.start()
        self.assertEqual(s.state, "disabled")
        self.assertFalse((self.tmp / "s.db").exists())
        with self.assertRaises(StorageUnavailable):
            s.get_core()

    def test_enabled_without_token_is_unavailable_but_does_not_crash(self):
        s = self.svc(telegram_bot_token="")
        s.start()
        self.assertEqual(s.state, "unavailable")
        self.assertIn("TELEGRAM_BOT_TOKEN", s.status()["reasons"][0])
        with self.assertRaises(StorageUnavailable):
            s.get_core()

    def test_enabled_without_chat_id(self):
        s = self.svc(cloud_storage_chat_id="")
        s.start()
        self.assertIn("CLOUD_STORAGE_CHAT_ID", s.status()["reasons"][0])

    def test_invalid_chunk_size_is_rejected(self):
        for mib in (0, 20, -1):
            s = self.svc(storage_chunk_size_mib=mib)
            s.start()
            self.assertEqual(s.state, "unavailable", mib)
        cfg = StorageConfig.from_settings(settings(storage_chunk_size_mib=19))
        self.assertEqual(cfg.validate(), [])
        self.assertEqual(cfg.chunk_size, MAX_CHUNK_SIZE)

    def test_default_chunk_size_is_15_mib(self):
        self.assertEqual(StorageConfig.from_settings(settings()).chunk_size, 15 * MIB)

    def test_other_invalid_values(self):
        cfg = StorageConfig.from_settings(settings(storage_max_retries=-1, storage_max_concurrent_uploads=0,
                                                   storage_connect_timeout=0))
        self.assertEqual(len(cfg.validate()), 3)

    def test_starts_ready_and_reports_status_without_secrets(self):
        tg = MockTelegram().start()
        self.addCleanup(tg.stop)
        s = self.svc(telegram_api_base=tg.api_base, storage_maintenance_interval=0)
        s.start()
        self.addCleanup(s.stop)
        self.assertEqual(s.state, "ready")
        status = s.status()
        self.assertEqual(status["schema_version"], 1)
        self.assertNotIn(TOKEN, repr(status))
        self.assertNotIn(str(CHAT), repr(status))
        core = s.get_core()
        core.ensure_bucket("files")
        obj = core.put_object("files", "hello.txt", b"hello")
        self.assertEqual(b"".join(core.stream(obj.id)), b"hello")
        self.assertTrue(core.backend_health().ok)
        for _ in range(100):  # разовая сверка при старте выполняется в фоне
            if s.last_report is not None:
                break
            time.sleep(0.02)
        self.assertIsNotNone(s.last_report)
        self.assertFalse(s.last_report.needs_attention)

    def test_database_failure_marks_unavailable(self):
        blocker = self.tmp / "file"
        blocker.write_text("x")
        s = self.svc(storage_db_path=str(blocker / "sub" / "s.db"))  # родитель — файл
        s.start()
        self.assertEqual(s.state, "unavailable")

    def test_init_storage_never_raises_and_registers_singleton(self):
        with mock.patch.object(StorageService, "start", side_effect=RuntimeError("boom")):
            service = init_storage(settings())
        self.assertEqual(service.state, "unavailable")
        self.assertIs(get_storage_service(), service)
        shutdown_storage()
        self.assertIsNone(get_storage_service())

    def test_relative_db_path_is_resolved_against_project_root(self):
        cfg = StorageConfig.from_settings(settings(storage_db_path="database/data/x.db"))
        self.assertEqual(cfg.db_path, PROJECT_ROOT / "database" / "data" / "x.db")
        self.assertEqual(StorageConfig.from_settings(settings()).db_path.name, "storage.db")

    def test_config_repr_hides_token(self):
        self.assertNotIn(TOKEN, repr(StorageConfig.from_settings(settings())))

    def test_settings_from_env_survive_garbage(self):
        env = {"STORAGE_ENABLED": "true", "STORAGE_CHUNK_SIZE_MIB": "abc", "STORAGE_MAX_RETRIES": "",
               "CLOUD_STORAGE_CHAT_ID": " -100777 "}
        with mock.patch.dict(os.environ, env):
            s = Settings.from_env()
        self.assertTrue(s.storage_enabled)
        self.assertEqual((s.storage_chunk_size_mib, s.storage_max_retries, s.cloud_storage_chat_id), (15, 3, "-100777"))


if __name__ == "__main__":
    unittest.main()
