"""Polaris Meme в Inline Mode: выбор вида результата, пагинация, доступ, получение file_id у Telegram.

Если aiogram не установлен, подставляется минимальная заглушка его типов: проверяется НАША логика обработчика,
а не aiogram. Реальный Telegram здесь не участвует."""
from __future__ import annotations

import asyncio
import sys
import types
import unittest
from unittest import mock

try:
    import aiogram  # noqa: F401
except ImportError:
    def _fake_aiogram():
        class _Obj:
            def __init__(self, *args, **kw):
                self.data = args[0] if args else None  # BufferedInputFile(data, filename=...)
                self.__dict__.update(kw)

        root, exc, tps = types.ModuleType("aiogram"), types.ModuleType("aiogram.exceptions"), types.ModuleType("aiogram.types")
        root.Bot = type("Bot", (), {})
        exc.TelegramAPIError = type("TelegramAPIError", (Exception,), {})
        exc.TelegramBadRequest = type("TelegramBadRequest", (exc.TelegramAPIError,), {})
        for name in ("BufferedInputFile", "InlineQuery", "InlineQueryResultCachedGif", "InlineQueryResultCachedPhoto",
                     "InlineQueryResultPhoto"):
            setattr(tps, name, type(name, (_Obj,), {}))
        root.exceptions, root.types = exc, tps
        sys.modules.update({"aiogram": root, "aiogram.exceptions": exc, "aiogram.types": tps})
    _fake_aiogram()

from aiogram.exceptions import TelegramBadRequest  # noqa: E402

from polaris.bot.meme_inline import MemeInline  # noqa: E402
from polaris.modules.meme import inline, repository as repo  # noqa: E402
from polaris.modules.meme.service import telegram_cache_key  # noqa: E402
from tests.meme_helpers import BOT_TOKEN, MemeTestCase, gif, jpeg, png  # noqa: E402


class FakeBot:
    def __init__(self):
        self.sent, self.deleted, self.fail = [], [], False

    async def send_photo(self, chat_id, photo, **kw):
        if self.fail:
            raise RuntimeError("PHOTO_INVALID_DIMENSIONS")
        self.sent.append(("photo", chat_id, photo.data if hasattr(photo, "data") else None))
        return types.SimpleNamespace(photo=[types.SimpleNamespace(file_id="small"), types.SimpleNamespace(file_id=f"PHOTO{len(self.sent)}")],
                                     message_id=100 + len(self.sent))

    async def send_animation(self, chat_id, animation, **kw):
        self.sent.append(("animation", chat_id, None))
        return types.SimpleNamespace(animation=types.SimpleNamespace(file_id=f"ANIM{len(self.sent)}"), message_id=100 + len(self.sent))

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))


class FakeQuery:
    def __init__(self, text="", offset="", user_id=1):
        self.query, self.offset, self.from_user = text, offset, types.SimpleNamespace(id=user_id)
        self.answers, self.fail_first = [], False

    async def answer(self, **kw):
        if self.fail_first:
            self.fail_first = False
            raise TelegramBadRequest("Bad Request: wrong file identifier/HTTP URL specified")
        self.answers.append(kw)


class InlineBase(MemeTestCase):
    def setUp(self):
        super().setUp()
        self.bot = FakeBot()
        self.inline = MemeInline(self.bot, self.settings, self.service)
        # «веб-процесс»: бот получает оригинал так же, как по подписанной ссылке, — из Storage через сервис
        self.inline._fetch_original = lambda row: self.service.read_original(row["id"])[1]  # type: ignore[method-assign]

    def add(self, data, title="Спокойной ночи с котиком", tags=("котик",)):
        return self.service.create(data, filename="a", title=title, tags=list(tags))

    def run_query(self, **kw):
        query = FakeQuery(**kw)
        asyncio.run(self.inline.handle(query))
        return query

    @staticmethod
    def kinds(query):
        return [type(r).__name__ for r in query.answers[-1]["results"]]


class DescribeTests(InlineBase):
    def row(self, meme):
        return repo.get(meme["id"])

    def test_uncached_jpeg_uses_signed_https_url_and_hides_storage_ids(self):
        meme = self.add(jpeg(seed=1))
        item = inline.describe(self.row(meme), self.service)
        self.assertEqual(item.kind, "photo")
        self.assertTrue(item.url.startswith("https://hub.example.com/api/meme/media/"))
        self.assertNotIn(self.row(meme)["storage_object_id"], item.url)
        self.assertNotIn(".part", item.url)

    def test_uncached_png_gif_cannot_use_url_path(self):
        for data in (png(seed=2), gif(seed=2)):
            self.assertIsNone(inline.describe(self.row(self.add(data, title=f"t{len(data)}")), self.service))

    def test_http_or_missing_webapp_url_disables_url_path(self):
        meme = self.add(jpeg(seed=3))
        for url in ("", "http://hub.example.com"):
            self.service.settings.webapp_url = url
            self.assertIsNone(inline.describe(self.row(meme), self.service))

    def test_cached_result_requires_matching_bot_token(self):
        meme = self.add(png(seed=4))
        repo.set_telegram_cache(meme["id"], "FID", "photo", telegram_cache_key(BOT_TOKEN))
        self.assertEqual(inline.describe(self.row(meme), self.service).kind, "cached_photo")
        repo.set_telegram_cache(meme["id"], "FID", "photo", telegram_cache_key("999:OTHER"))
        self.assertIsNone(inline.describe(self.row(meme), self.service))

    def test_offset_parsing_and_access_rule(self):
        self.assertEqual([inline.parse_offset(x) for x in (None, "", "abc", "-5", "40")], [0, 0, 0, 0, 40])
        self.assertFalse(inline.is_allowed(1, []))      # админы не заданы -> закрыто
        self.assertFalse(inline.is_allowed(2, [1]))
        self.assertTrue(inline.is_allowed(1, [1]))


class HandlerTests(InlineBase):
    def test_first_query_fetches_original_gets_file_id_and_removes_service_message(self):
        source = png(seed=5)
        meme = self.add(source)
        query = self.run_query(text="спокойной ночи")
        self.assertEqual(self.bot.sent[0][:2], ("photo", 1))
        self.assertEqual(self.bot.sent[0][2], source, "в Telegram уходит полноценный оригинал, а не часть чанков")
        self.assertEqual(len(self.bot.deleted), 1)
        self.assertEqual(self.kinds(query), ["InlineQueryResultCachedPhoto"])
        result = query.answers[0]["results"][0]
        self.assertEqual((result.id, result.photo_file_id), (meme["id"], "PHOTO1"))
        self.assertTrue(query.answers[0]["is_personal"])
        # второй запрос — из кэша, без новой отправки
        self.run_query(text="спокойной ночи")
        self.assertEqual(len(self.bot.sent), 1)

    def test_cache_chat_receives_the_copy_and_message_is_kept(self):
        self.settings.meme_cache_chat_id = "-100777"
        self.add(png(seed=40), title="Кот")
        self.run_query(text="кот")
        self.assertEqual(self.bot.sent[0][1], -100777)
        self.assertEqual(self.bot.deleted, [])

    def test_bot_reads_the_chat_chosen_in_the_menu(self):
        repo.set_setting("cache_chat_id", "-100888")
        self.add(png(seed=41), title="Кот")
        self.run_query(text="кот")
        self.assertEqual((self.bot.sent[0][1], self.bot.deleted), (-100888, []))

    def test_gif_goes_through_animation(self):
        self.add(gif(seed=6), title="Гифка")
        query = self.run_query(text="гифка")
        self.assertEqual(self.bot.sent[0][0], "animation")
        self.assertEqual(self.kinds(query), ["InlineQueryResultCachedGif"])

    def test_several_matches_and_title_tag_description_search(self):
        self.add(png(seed=7), title="Кот спит", tags=["ночь"])
        self.add(png(seed=8), title="Собака", tags=["кот"])
        self.assertEqual(len(self.run_query(text="кот").answers[-1]["results"]), 2)
        self.assertEqual(len(self.run_query(text="ночь").answers[-1]["results"]), 1)

    def test_empty_query_lists_newest_and_next_offset_paginates(self):
        for i in range(inline.PAGE + 3):
            self.service.create(jpeg(seed=i), filename="a", title=f"Мем {i}")  # JPEG: отправляем по URL, без «прогрева»
        first = self.run_query(text="")
        self.assertEqual(len(first.answers[-1]["results"]), inline.PAGE)
        self.assertEqual(first.answers[-1]["next_offset"], str(inline.PAGE))
        second = self.run_query(text="", offset=first.answers[-1]["next_offset"])
        self.assertEqual(len(second.answers[-1]["results"]), 3)
        self.assertEqual(second.answers[-1]["next_offset"], "")
        ids = [r.id for q in (first, second) for r in q.answers[-1]["results"]]
        self.assertEqual(len(set(ids)), inline.PAGE + 3)

    def test_strangers_get_nothing_and_library_is_not_searched(self):
        self.add(png(seed=9))
        query = self.run_query(text="кот", user_id=999)
        self.assertEqual(query.answers[-1]["results"], [])
        self.assertEqual(self.bot.sent, [])

    def test_telegram_rejecting_file_does_not_break_search_and_is_not_retried(self):
        self.add(png(seed=10))
        self.add(jpeg(seed=10), title="Спокойной ночи jpeg")
        self.bot.fail = True
        query = self.run_query(text="ночи")
        self.assertEqual(self.kinds(query), ["InlineQueryResultPhoto"])  # png пропущен, jpeg ушёл по URL
        self.bot.fail = False
        self.run_query(text="ночи")
        self.assertEqual(self.bot.sent, [], "плохая картинка не должна долбить Telegram при каждом запросе")

    def test_warm_budget_limits_work_per_query(self):
        for i in range(5):
            self.add(png(seed=30 + i), title=f"Кот {i}")
        query = self.run_query(text="кот")
        self.assertEqual(len(self.bot.sent), 3)
        self.assertEqual(len(query.answers[-1]["results"]), 3)
        self.run_query(text="кот")
        self.assertEqual(len(self.bot.sent), 5)

    def test_stale_file_id_clears_cache_and_answers_without_it(self):
        meme = self.add(jpeg(seed=11))
        repo.set_telegram_cache(meme["id"], "STALE", "photo", telegram_cache_key(BOT_TOKEN))
        query = FakeQuery(text="")
        query.fail_first = True
        asyncio.run(self.inline.handle(query))
        self.assertIsNone(repo.get(meme["id"])["tg_file_id"])
        self.assertEqual(self.kinds(query), ["InlineQueryResultPhoto"])

    def test_search_errors_still_answer_the_query(self):
        with mock.patch.object(inline, "search_page", side_effect=RuntimeError("db locked")), \
                mock.patch("polaris.bot.meme_inline.search_page", side_effect=RuntimeError("db locked")):
            query = self.run_query(text="кот")
        self.assertEqual(query.answers[-1]["results"], [])

    def test_local_fetch_goes_to_loopback_even_if_web_binds_everywhere(self):
        self.settings.web_host, self.settings.web_port = "0.0.0.0", 8123
        self.assertEqual(MemeInline(self.bot, self.settings, self.service)._local_base(), "http://127.0.0.1:8123")


if __name__ == "__main__":
    unittest.main()
