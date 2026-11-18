"""Polaris Meme: file_id получается сразу при добавлении; чат кэша; сбои не отменяют добавление."""
from __future__ import annotations

import unittest
from unittest import mock

from polaris.infra.settings import Settings
from polaris.modules.meme import repository as repo, telegram_cache
from polaris.modules.meme.service import telegram_cache_key
from tests.meme_helpers import BOT_TOKEN, MemeTestCase, gif, jpeg, png


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FakeSession:
    """Вместо requests.Session: запоминает вызовы Bot API и отвечает как Telegram."""

    def __init__(self, fail=None, raise_exc=None):
        self.calls, self.fail, self.raise_exc = [], fail, raise_exc

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, timeout=None, data=None, files=None):
        method = url.rsplit("/", 1)[-1]
        self.calls.append((method, url, data, files))
        if self.raise_exc:
            raise self.raise_exc
        if self.fail == method:
            return FakeResponse({"ok": False, "description": "Bad Request: chat not found"})
        if method == "getChat":
            return FakeResponse({"ok": True, "result": {"title": "Кэш мемов", "type": "channel"}})
        if method == "sendMessage":
            return FakeResponse({"ok": True, "result": {"message_id": 9}})
        if method == "sendPhoto":
            return FakeResponse({"ok": True, "result": {"message_id": 7, "photo": [{"file_id": "small"}, {"file_id": "BIG"}]}})
        if method == "sendAnimation":
            return FakeResponse({"ok": True, "result": {"message_id": 8, "animation": {"file_id": "ANIM"}}})
        return FakeResponse({"ok": True, "result": True})


def settings(**kw):
    return Settings(telegram_bot_token=BOT_TOKEN, telegram_admin_ids=[42], **kw)


class PushTests(MemeTestCase):
    def push(self, cfg, row, session):
        with mock.patch.object(telegram_cache, "_session", return_value=session):
            return telegram_cache.push(cfg, row, b"bytes")

    def test_cache_chat_keeps_the_message(self):
        session = FakeSession()
        result = self.push(settings(meme_cache_chat_id="-100500"), {"mime_type": "image/png"}, session)
        self.assertEqual(result, ("BIG", "photo"))
        self.assertEqual([c[0] for c in session.calls], ["sendPhoto"])
        self.assertEqual(session.calls[0][2]["chat_id"], -100500)

    def test_admin_dm_fallback_deletes_the_service_message(self):
        session = FakeSession()
        self.push(settings(), {"mime_type": "image/jpeg"}, session)
        self.assertEqual([c[0] for c in session.calls], ["sendPhoto", "deleteMessage"])
        self.assertEqual(session.calls[1][2], {"chat_id": 42, "message_id": 7})

    def test_gif_goes_as_animation(self):
        session = FakeSession()
        self.assertEqual(self.push(settings(), {"mime_type": "image/gif"}, session), ("ANIM", "animation"))
        self.assertEqual(session.calls[0][0], "sendAnimation")

    def test_nothing_to_send_to_or_no_token_returns_none(self):
        row = {"mime_type": "image/png"}
        self.assertIsNone(telegram_cache.push(Settings(telegram_bot_token=BOT_TOKEN), row, b"x"))
        self.assertIsNone(telegram_cache.push(Settings(telegram_admin_ids=[1]), row, b"x"))

    def test_errors_are_clean_and_never_contain_the_token(self):
        row = {"mime_type": "image/png"}
        with self.assertRaises(telegram_cache.CacheError) as ctx:
            self.push(settings(), row, FakeSession(fail="sendPhoto"))
        self.assertIn("chat not found", str(ctx.exception))
        boom = RuntimeError(f"HTTPSConnectionPool: /bot{BOT_TOKEN}/sendPhoto")
        with self.assertRaises(telegram_cache.CacheError) as ctx:
            self.push(settings(), row, FakeSession(raise_exc=boom))
        self.assertNotIn(BOT_TOKEN, str(ctx.exception))

    def test_failed_delete_does_not_fail_the_push(self):
        self.assertEqual(self.push(settings(), {"mime_type": "image/png"}, FakeSession(fail="deleteMessage")), ("BIG", "photo"))


class AddViaMemeTabTests(MemeTestCase):
    def test_file_id_is_saved_immediately_and_record_is_ready(self):
        self.push_result = ("FID1", "photo")
        created = self.service.create(png(seed=1), filename="a.png", title="A")
        self.assertTrue(created["telegram_ready"])
        row = repo.get(created["id"])
        self.assertEqual((row["tg_file_id"], row["tg_file_kind"], row["tg_cache_key"]), ("FID1", "photo", telegram_cache_key(BOT_TOKEN)))
        self.assertEqual(self.push_calls, [(created["id"], png(seed=1))], "в Telegram уходят исходные байты")
        self.assertNotIn("FID1", repr(created))  # file_id наружу API не отдаётся

    def test_telegram_failure_does_not_cancel_the_upload(self):
        self.push_result = telegram_cache.CacheError("sendPhoto: chat not found")
        created = self.service.create(png(seed=2), filename="a.png", title="A")
        self.assertFalse(created["telegram_ready"])
        self.assertEqual(len(self.stored_objects()), 1)
        self.assertEqual(repo.count(), 1)

    def test_no_target_means_not_ready_but_saved(self):
        self.push_result = None
        self.assertFalse(self.service.create(png(seed=3), filename="a.png", title="A")["telegram_ready"])

    def test_rejected_files_and_duplicates_are_never_sent_to_telegram(self):
        self.push_result = ("F", "photo")
        self.service.create(png(seed=4), filename="a.png", title="A")
        for data in (b"text", png(seed=4)):
            with self.assertRaises(Exception):
                self.service.create(data, filename="b.png", title="B")
        self.assertEqual(len(self.push_calls), 1)

    def test_bot_side_service_never_pushes(self):
        from polaris.modules.meme.service import MemeService
        self.assertIsNone(MemeService(self.settings, get_service=lambda: None, telegram_push=None)._telegram_push)

    def test_inline_uses_cached_id_right_after_upload_for_every_format(self):
        from polaris.modules.meme import inline
        for n, (data, kind) in enumerate(((png(seed=5), "photo"), (jpeg(seed=5), "photo"), (gif(seed=5), "animation"))):
            self.push_result = (f"F{n}", kind)
            created = self.service.create(data, filename="x", title=f"t{n}")
            item = inline.describe(repo.get(created["id"]), self.service)
            self.assertEqual(item.kind, "cached_gif" if kind == "animation" else "cached_photo")
            self.assertEqual(item.file_id, f"F{n}")


class ChatSettingTests(MemeTestCase):
    def test_normalize_chat(self):
        ok = {"": "", "  ": "", "-1001234567890": "-1001234567890", " -100123456 ": "-100123456", "@my_cache_channel": "@my_cache_channel"}
        for raw, expected in ok.items():
            self.assertEqual(telegram_cache.normalize_chat(raw), expected, raw)
        for bad in ("https://t.me/c/123/4", "t.me/chan", "кэш", "@ab", "12", "-100 123", "@1abcdef"):
            with self.assertRaises(ValueError, msg=bad):
                telegram_cache.normalize_chat(bad)

    def test_menu_setting_beats_env_and_clearing_falls_back(self):
        cfg = settings(meme_cache_chat_id="-100111111")
        self.assertEqual(telegram_cache.configured_chat(cfg), ("-100111111", "env"))
        repo.set_setting(telegram_cache.CHAT_SETTING, "-100222222")
        self.assertEqual(telegram_cache.configured_chat(cfg), ("-100222222", "settings"))
        self.assertEqual(telegram_cache.cache_target(cfg), (-100222222, False))
        repo.set_setting(telegram_cache.CHAT_SETTING, "")
        self.assertEqual(telegram_cache.configured_chat(cfg), ("-100111111", "env"))
        self.assertEqual(telegram_cache.configured_chat(settings()), ("", ""))

    def test_push_goes_to_the_chat_from_the_menu(self):
        repo.set_setting(telegram_cache.CHAT_SETTING, "@my_cache_channel")
        session = FakeSession()
        with mock.patch.object(telegram_cache, "_session", return_value=session):
            telegram_cache.push(settings(), {"mime_type": "image/png"}, b"x")
        self.assertEqual(session.calls[0][2]["chat_id"], "@my_cache_channel")
        self.assertEqual([c[0] for c in session.calls], ["sendPhoto"])  # в чате кэша сообщение остаётся

    def test_missing_settings_table_does_not_break_lookup(self):
        with mock.patch.object(repo, "get_db", side_effect=__import__("sqlite3").OperationalError("no such table")):
            self.assertEqual(repo.get_setting("x"), "")


class SettingsMenuTests(MemeTestCase):
    def fake_session(self, **kw):
        session = FakeSession(**kw)
        return session, mock.patch.object(telegram_cache, "_session", return_value=session)

    def test_view_reports_mode_and_cache_state(self):
        self.service.create(png(seed=1), filename="a", title="A")
        view = self.service.settings_view()
        self.assertEqual((view["mode"], view["total"], view["ready"], view["pending"]), ("admin_dm", 1, 0, 1))
        self.assertTrue(view["fallback_url"])
        self.service.settings_update({"cache_chat_id": "-100500500"})
        view = self.service.settings_view()
        self.assertEqual((view["mode"], view["source"], view["effective_chat"]), ("chat", "settings", "-100500500"))
        self.service.settings_update({"cache_chat_id": ""})
        self.assertEqual(self.service.settings_view()["mode"], "admin_dm")

    def test_view_without_admins_and_chat_says_nowhere_to_send(self):
        self.service.settings.telegram_admin_ids = []
        self.assertEqual(self.service.settings_view()["mode"], "none")

    def test_update_validation(self):
        for bad in (None, {}, {"cache_chat_id": "https://t.me/c/1/2"}):
            with self.assertRaises(Exception) as ctx:
                self.service.settings_update(bad)
            self.assertEqual(ctx.exception.status, 422)
        self.assertEqual(self.service.settings_view()["cache_chat_id"], "")

    def test_check_chat_reports_title_and_cleans_up(self):
        session, patch = self.fake_session()
        with patch:
            result = self.service.settings_test({"cache_chat_id": "-100500500"})
        self.assertEqual((result["title"], result["type"], result["chat"]), ("Кэш мемов", "channel", "-100500500"))
        self.assertEqual([c[0] for c in session.calls], ["getChat", "sendMessage", "deleteMessage"])

    def test_check_chat_uses_saved_chat_then_admin_dm(self):
        session, patch = self.fake_session()
        with patch:
            self.assertEqual(self.service.settings_test()["chat"], str(self.settings.telegram_admin_ids[0]))
            self.service.settings_update({"cache_chat_id": "-100777777"})
            self.assertEqual(self.service.settings_test({})["chat"], "-100777777")

    def test_check_chat_errors_are_human_and_have_no_token(self):
        for description, expected in (("chat not found", "добавьте бота"), ("not enough rights to send text messages", "администратором")):
            class Failing(FakeSession):
                def post(self, url, **kw):
                    return FakeResponse({"ok": False, "description": "Bad Request: " + description})
            with mock.patch.object(telegram_cache, "_session", return_value=Failing()):
                with self.assertRaises(Exception) as ctx:
                    self.service.settings_test({"cache_chat_id": "-100500500"})
            self.assertEqual(ctx.exception.status, 422)
            self.assertIn(expected, ctx.exception.detail)
            self.assertNotIn(BOT_TOKEN, ctx.exception.detail)

    def test_check_chat_without_any_target_is_422(self):
        self.service.settings.telegram_admin_ids = []
        with self.assertRaises(Exception) as ctx:
            self.service.settings_test()
        self.assertEqual(ctx.exception.status, 422)


class SyncTests(MemeTestCase):
    def add_uncached(self, n):
        return [self.service.create(png(seed=50 + i), filename="a", title=f"M{i}")["id"] for i in range(n)]

    def test_sync_fills_missing_file_ids_from_storage_originals(self):
        ids = self.add_uncached(3)
        self.push_calls.clear()
        self.push_result = ("FID", "photo")
        result = self.service.sync_pending(limit=10)
        self.assertEqual((result["done"], result["failed"], result["remaining"]), (3, 0, 0))
        self.assertEqual(sorted(self.push_calls), sorted((i, png(seed=50 + n)) for n, i in enumerate(ids)))
        self.assertEqual(self.service.settings_view()["pending"], 0)

    def test_sync_is_batched_and_skip_prevents_getting_stuck(self):
        ids = self.add_uncached(4)
        self.push_result = telegram_cache.CacheError("sendPhoto: chat not found")
        first = self.service.sync_pending(limit=2)
        self.assertEqual((first["done"], first["failed"], first["remaining"]), (0, 2, 2))
        self.assertIn("chat not found", first["error"])
        self.push_result = ("F", "photo")
        second = self.service.sync_pending(limit=2, skip=tuple(first["failed_ids"]))
        self.assertEqual((second["done"], second["remaining"]), (2, 0))
        self.assertEqual(sorted(first["failed_ids"] + [r for r in ids if r not in first["failed_ids"]]), sorted(ids))

    def test_one_broken_record_does_not_stop_the_rest(self):
        ids = self.add_uncached(3)
        self.core.delete_object(repo.get(ids[0])["storage_object_id"])  # файл пропал из Storage
        self.push_result = ("F", "photo")
        result = self.service.sync_pending(limit=10)
        self.assertEqual((result["done"], result["failed"]), (2, 1))
        self.assertEqual(result["failed_ids"], [ids[0]])

    def test_cache_from_another_bot_counts_as_pending(self):
        (meme_id,) = self.add_uncached(1)
        repo.set_telegram_cache(meme_id, "OLD", "photo", telegram_cache_key("999:OTHER-BOT"))
        self.assertEqual(self.service.settings_view()["pending"], 1)
        self.push_result = ("NEW", "photo")
        self.service.sync_pending()
        self.assertEqual(repo.get(meme_id)["tg_file_id"], "NEW")

    def test_sync_without_target_is_422(self):
        self.add_uncached(1)
        self.service.settings.telegram_admin_ids = []
        with self.assertRaises(Exception) as ctx:
            self.service.sync_pending()
        self.assertEqual(ctx.exception.status, 422)
