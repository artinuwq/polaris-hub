"""Тесты Telegram Driver против локального mock Bot API (реальный HTTP, реальный токен не нужен)."""
from __future__ import annotations

import json
import logging
import os
import unittest

from polaris.storage.backends.base import (
    BackendNotFound, DeleteRejected, PermanentBackendError, TransientBackendError, UploadContext,
)
from polaris.storage.backends.telegram import TelegramConfig, TelegramDriver
from tests.storage_helpers import CHAT, TOKEN, MockTelegram, StorageTestCase, sha


class TelegramTestBase(unittest.TestCase):
    def setUp(self):
        self.tg = MockTelegram().start()
        self.addCleanup(self.tg.stop)
        self.sleeps: list[float] = []
        self.driver = self.make_driver()

    def make_driver(self, **kw) -> TelegramDriver:
        params = dict(token=TOKEN, chat_id=str(CHAT), api_base=self.tg.api_base, connect_timeout=2,
                      read_timeout=0.3, max_retries=3, retry_delay=1.0, max_retry_after=10)
        params.update(kw)
        return TelegramDriver(TelegramConfig(**params), sleep=self.sleeps.append)

    @staticmethod
    def ctx(data: bytes, idx=0) -> UploadContext:
        return UploadContext("blob-1", idx, sha(data), len(data))


class UploadDownloadTests(TelegramTestBase):
    def test_upload_and_download_roundtrip(self):
        data = os.urandom(50_000)
        res = self.driver.upload(data, self.ctx(data))
        self.assertEqual(res.uncertain_attempts, 0)
        ref = json.loads(res.reference)
        self.assertEqual((ref["chat_id"], ref["size"]), (CHAT, len(data)))
        self.assertEqual(bytes(self.driver.download(res.reference)), data)
        self.assertEqual(self.driver.get_metadata(res.reference).size_bytes, len(data))
        msg = next(iter(self.tg.messages.values()))
        self.assertIn("polaris-storage:v1 blob=blob-1 chunk=0", msg["caption"])

    def test_http_429_honours_retry_after(self):
        self.tg.faults["sendDocument"] = ["429:3"]
        res = self.driver.upload(b"abc", self.ctx(b"abc"))
        self.assertEqual(self.sleeps, [4])  # retry_after + 1 с запаса
        self.assertEqual(res.uncertain_attempts, 0)  # 429: сообщение точно не сохранено
        self.assertEqual(len(self.tg.messages), 1)

    def test_retry_after_longer_than_cap_gives_up_without_sleeping(self):
        self.tg.faults["sendDocument"] = ["429:3600"]
        with self.assertRaises(TransientBackendError):
            self.driver.upload(b"abc", self.ctx(b"abc"))
        self.assertEqual(self.sleeps, [])
        self.assertEqual(self.tg.count("sendDocument"), 1)

    def test_http_5xx_is_retried_with_backoff(self):
        self.tg.faults["sendDocument"] = ["500", "500"]
        res = self.driver.upload(b"abc", self.ctx(b"abc"))
        self.assertEqual(self.sleeps, [1.0, 2.0])
        self.assertEqual(res.uncertain_attempts, 2)  # 5xx: сообщение могло сохраниться
        self.assertEqual(len(self.tg.messages), 1)

    def test_retries_are_bounded(self):
        self.tg.faults["sendDocument"] = ["500"] * 10
        with self.assertRaises(TransientBackendError) as ctx:
            self.driver.upload(b"abc", self.ctx(b"abc"))
        self.assertEqual(self.tg.count("sendDocument"), 4)  # 1 + max_retries
        self.assertEqual(ctx.exception.uncertain_attempts, 4)

    def test_timeout_after_store_is_reported_as_uncertain_and_may_duplicate(self):
        self.tg.faults["sendDocument"] = ["timeout_after_store"]
        res = self.driver.upload(b"abc", self.ctx(b"abc"))
        self.assertEqual(res.uncertain_attempts, 1)
        self.assertEqual(len(self.tg.messages), 2)  # исходное сообщение осталось «сиротой»: честное ограничение Bot API

    def test_timeout_before_store(self):
        self.tg.faults["sendDocument"] = ["timeout_before"]
        res = self.driver.upload(b"abc", self.ctx(b"abc"))
        self.assertEqual(len(self.tg.messages), 1)
        self.assertEqual(res.uncertain_attempts, 1)  # снаружи не отличить: считаем неопределённым

    def test_connection_refused_is_certain_failure(self):
        driver = self.make_driver(api_base="http://127.0.0.1:1", max_retries=1)
        with self.assertRaises(TransientBackendError) as ctx:
            driver.upload(b"abc", self.ctx(b"abc"))
        self.assertEqual(ctx.exception.uncertain_attempts, 0)  # до Telegram запрос не дошёл
        self.assertNotIn(TOKEN, str(ctx.exception))

    def test_connection_dropped_after_store_is_uncertain(self):
        self.tg.faults["sendDocument"] = ["drop_after_store"]
        res = self.driver.upload(b"abc", self.ctx(b"abc"))
        self.assertEqual(res.uncertain_attempts, 1)

    def test_invalid_json_response_is_retried(self):
        self.tg.faults["sendDocument"] = ["bad_json"]
        res = self.driver.upload(b"abc", self.ctx(b"abc"))
        self.assertEqual(res.uncertain_attempts, 1)

    def test_malformed_ok_response_cleans_up_message_and_retries(self):
        self.tg.faults["sendDocument"] = ["malformed_ok"]
        res = self.driver.upload(b"abc", self.ctx(b"abc"))
        self.assertEqual(len(self.tg.messages), 1)  # сообщение без документа удалено
        self.assertEqual(res.uncertain_attempts, 0)
        self.assertEqual(self.tg.count("deleteMessage"), 1)

    def test_size_mismatch_in_response_is_not_accepted(self):
        self.tg.faults["sendDocument"] = ["wrong_size"]
        res = self.driver.upload(b"abc", self.ctx(b"abc"))
        self.assertEqual(len(self.tg.messages), 1)
        self.assertEqual(json.loads(res.reference)["size"], 3)

    def test_permanent_error_is_not_retried(self):
        self.tg.faults["sendDocument"] = ["perm400"]
        with self.assertRaises(PermanentBackendError):
            self.driver.upload(b"abc", self.ctx(b"abc"))
        self.assertEqual(self.tg.count("sendDocument"), 1)
        self.assertEqual(self.sleeps, [])

    def test_oversized_chunk_rejected_before_network(self):
        with self.assertRaises(PermanentBackendError):
            self.driver.upload(b"x" * (19 * 1024 * 1024 + 1), self.ctx(b"x"))
        self.assertEqual(self.tg.requests, [])

    def test_download_missing_file_id(self):
        ref = json.dumps({"v": 1, "chat_id": CHAT, "message_id": 1, "file_id": "NOPE", "size": 3})
        with self.assertRaises(BackendNotFound):
            self.driver.download(ref)
        self.assertEqual(self.tg.count("getFile"), 1)  # без повторов

    def test_download_file_removed_from_file_server(self):
        res = self.driver.upload(b"abc", self.ctx(b"abc"))
        self.tg.files.clear()
        with self.assertRaises(BackendNotFound):
            self.driver.download(res.reference)

    def test_interrupted_download_is_retried(self):
        data = os.urandom(200_000)
        res = self.driver.upload(data, self.ctx(data))
        self.tg.faults["download"] = ["truncate"]
        self.assertEqual(bytes(self.driver.download(res.reference)), data)
        self.assertEqual(self.tg.count("download"), 2)

    def test_download_that_keeps_breaking_fails_after_bounded_retries(self):
        data = os.urandom(200_000)
        res = self.driver.upload(data, self.ctx(data))
        self.tg.faults["download"] = ["truncate"] * 10
        with self.assertRaises(TransientBackendError):
            self.driver.download(res.reference)
        self.assertEqual(self.tg.count("download"), 4)

    def test_download_5xx_retried(self):
        res = self.driver.upload(b"abc", self.ctx(b"abc"))
        self.tg.faults["download"] = ["500"]
        self.assertEqual(bytes(self.driver.download(res.reference)), b"abc")

    def test_malformed_reference(self):
        with self.assertRaises(PermanentBackendError):
            self.driver.download("not json")


class DeleteAndHealthTests(TelegramTestBase):
    def test_delete_removes_message(self):
        res = self.driver.upload(b"abc", self.ctx(b"abc"))
        self.assertFalse(self.driver.delete(res.reference).already_absent)
        self.assertEqual(self.tg.messages, {})

    def test_delete_of_already_missing_message_is_reported(self):
        res = self.driver.upload(b"abc", self.ctx(b"abc"))
        self.driver.delete(res.reference)
        self.assertTrue(self.driver.delete(res.reference).already_absent)

    def test_rejected_delete_is_not_success(self):
        res = self.driver.upload(b"abc", self.ctx(b"abc"))
        self.tg.faults["deleteMessage"] = ["cant_delete"]
        with self.assertRaises(DeleteRejected):
            self.driver.delete(res.reference)
        self.assertEqual(len(self.tg.messages), 1)  # сообщение на месте

    def test_delete_transient_failure_retried(self):
        res = self.driver.upload(b"abc", self.ctx(b"abc"))
        self.tg.faults["deleteMessage"] = ["500"]
        self.driver.delete(res.reference)
        self.assertEqual(self.tg.messages, {})

    def test_delete_uses_chat_from_reference(self):
        res = self.driver.upload(b"abc", self.ctx(b"abc"))
        other = self.make_driver(chat_id="-100999")  # чат в конфигурации сменили
        other.delete(res.reference)
        self.assertEqual(self.tg.messages, {})

    def test_health_check(self):
        h = self.driver.health_check()
        self.assertTrue(h.ok)
        self.assertTrue(h.details["can_delete_messages"])

    def test_health_check_reports_failure_without_raising_or_leaking_token(self):
        h = self.make_driver(api_base="http://127.0.0.1:1", max_retries=0).health_check()
        self.assertFalse(h.ok)
        self.assertNotIn(TOKEN, h.message)

    def test_unconfigured_driver_refuses_to_start(self):
        with self.assertRaises(PermanentBackendError):
            TelegramDriver(TelegramConfig(token="", chat_id="1"))

    def test_token_never_in_repr_or_logs(self):
        self.assertNotIn(TOKEN, repr(self.driver))
        self.assertNotIn(TOKEN, repr(self.driver.cfg))
        self.tg.faults["sendDocument"] = ["500"]
        with self.assertLogs("polaris.storage.backends.telegram", level="WARNING") as logs:
            self.driver.upload(b"abc", self.ctx(b"abc"))
        self.assertNotIn(TOKEN, "\n".join(logs.output))


class CoreOverTelegramTests(TelegramTestBase, StorageTestCase):
    """Ядро + настоящий Telegram Driver: end-to-end на mock API."""
    chunk_size = 100

    def setUp(self):
        StorageTestCase.setUp(self)  # создаёт MemoryDriver — ниже заменяем на Telegram
        TelegramTestBase.setUp(self)
        self.core = self.make_core(self.driver)
        self.core.ensure_bucket("files")

    def test_end_to_end_put_stream_delete(self):
        data = os.urandom(450)
        obj = self.core.put_object("files", "big.bin", data)
        self.assertEqual(len(self.tg.messages), 5)  # 100*4 + 50
        self.assertEqual(b"".join(self.core.stream(obj.id)), data)
        self.assertTrue(self.core.verify_object(obj.id, deep=True).ok)
        self.assertTrue(self.core.delete_object(obj.id))
        self.assertEqual(self.tg.messages, {})

    def test_dedup_does_not_touch_telegram(self):
        data = os.urandom(450)
        self.core.put_object("files", "a", data)
        sent = self.tg.count("sendDocument")
        self.core.put_object("files", "b", data, expected_sha256=sha(data))
        self.assertEqual(self.tg.count("sendDocument"), sent)

    def test_telegram_refuses_deletion_state_is_recorded(self):
        obj = self.core.put_object("files", "a", os.urandom(150))
        self.tg.faults["deleteMessage"] = ["cant_delete"]
        self.assertFalse(self.core.delete_object(obj.id))
        rows = self.q("SELECT status, delete_attempts, last_error FROM storage_chunks")
        self.assertEqual([(r["status"], r["delete_attempts"]) for r in rows], [("delete_rejected", 1)])
        self.assertIn("can't be deleted", rows[0]["last_error"])
        self.assertEqual(len(self.tg.messages), 1)  # отклонённое сообщение физически осталось
        self.assertEqual(self.q("SELECT status FROM storage_blobs")[0]["status"], "deleting")

    def test_chunk_size_above_driver_limit_is_refused(self):
        from polaris.shared.exceptions import ConfigurationError
        with self.assertRaises(ConfigurationError):
            self.make_core(self.driver, chunk_size=20 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
