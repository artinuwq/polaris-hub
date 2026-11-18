"""Telegram Inline Mode для Polaris Meme: `@бот спокойной ночи` -> выбранная картинка уходит в чат обычным фото.

Бот и веб — разные процессы, а Storage живёт только в веб-процессе, поэтому:
- поиск идёт прямо по общей БД (modules.meme.inline), Storage боту не нужен;
- пока у записи нет file_id, бот забирает ОРИГИНАЛ у веб-процесса по короткоживущей подписанной ссылке на
  127.0.0.1 (публичный URL и «хайрпин» не нужны), проверяет SHA-256, отправляет файл как обычное фото (или анимацию для GIF)
  в чат кэша (MEME_CACHE_CHAT_ID) либо админу в личку, запоминает file_id и в личке сразу удаляет служебное сообщение.
  (Обычно file_id уже получен при добавлении через Hub — см. MemeService._cache_in_telegram; это запасной путь.)
Дальше результаты отдаются по file_id — без URL и без повторной загрузки.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time

import requests
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import (
    BufferedInputFile,
    InlineQuery,
    InlineQueryResultCachedGif,
    InlineQueryResultCachedPhoto,
    InlineQueryResultPhoto,
)

from polaris.infra.settings import Settings
from polaris.modules.meme import repository as repo
from polaris.modules.meme.inline import InlineItem, describe, has_cache, is_allowed, parse_offset, search_page
from polaris.modules.meme.service import MemeService, telegram_cache_key
from polaris.modules.meme.telegram_cache import cache_target

logger = logging.getLogger(__name__)

WARM_BUDGET = 6.0         # сколько секунд inline-ответ может ждать получения file_id
WARM_MAX_PER_QUERY = 3    # и для скольких картинок максимум: остальные подтянутся следующими запросами
FAILED_RETRY_SECONDS = 600


def to_result(item: InlineItem):
    if item.kind == "cached_gif":
        return InlineQueryResultCachedGif(id=item.id, gif_file_id=item.file_id, title=item.title)
    if item.kind == "cached_photo":
        return InlineQueryResultCachedPhoto(id=item.id, photo_file_id=item.file_id, title=item.title,
                                            description=item.description)
    return InlineQueryResultPhoto(id=item.id, photo_url=item.url, thumbnail_url=item.url, photo_width=item.width or None,
                                  photo_height=item.height or None, title=item.title, description=item.description)


class MemeInline:
    def __init__(self, bot: Bot, settings: Settings, service: MemeService | None = None) -> None:
        self.bot = bot
        self.settings = settings
        # Storage в этом процессе не поднимается: сервису нужны только БД и подпись ссылок
        self.service = service or MemeService(settings, get_service=lambda: None, telegram_push=None)
        self._failed: dict[str, float] = {}   # id записи -> когда Telegram отверг файл (не долбим повторно)
        self._warm_lock = asyncio.Lock()

    # ─── получение file_id ───

    def _local_base(self) -> str:
        host = self.settings.web_host
        if host in ("", "0.0.0.0", "::"):
            host = "127.0.0.1"
        return f"http://{host}:{self.settings.web_port}"

    def _fetch_original(self, row: dict) -> bytes:
        url = self._local_base() + self.service.image_path(row["id"], ttl=120)
        with requests.Session() as session:
            session.trust_env = False  # системные HTTP(S)_PROXY не должны перехватывать запрос к localhost
            response = session.get(url, timeout=(3, 15))
        if response.status_code != 200:
            raise RuntimeError(f"веб-процесс вернул HTTP {response.status_code}")
        if hashlib.sha256(response.content).hexdigest() != row["sha256"]:
            raise RuntimeError("SHA-256 полученного файла не совпал с записью")
        return response.content

    async def _warm(self, row: dict, chat_id: int) -> bool:
        """Получить и сохранить file_id для записи. Сообщение в чате удаляется сразу. Не бросает исключений."""
        async with self._warm_lock:
            fresh = await asyncio.to_thread(repo.get, row["id"])
            if fresh is None:
                return False
            if has_cache(fresh, telegram_cache_key(self.settings.telegram_bot_token)):
                return True  # пока ждали очередь, другой запрос уже справился
            target = cache_target(self.settings, chat_id)
            if target is None:
                return False
            chat_id, delete_after = target  # MEME_CACHE_CHAT_ID — туда, сообщение остаётся; иначе в личку и удалить
            try:
                data = await asyncio.to_thread(self._fetch_original, fresh)
                is_gif = fresh["mime_type"] == "image/gif"
                upload = BufferedInputFile(data, filename=fresh["filename"] or f"meme{fresh['id'][:8]}")
                if is_gif:
                    sent = await self.bot.send_animation(chat_id, upload, disable_notification=True)
                    file_id, kind = sent.animation.file_id, "animation"
                else:
                    sent = await self.bot.send_photo(chat_id, upload, disable_notification=True)
                    file_id, kind = sent.photo[-1].file_id, "photo"
                if delete_after:
                    try:
                        await self.bot.delete_message(chat_id, sent.message_id)
                    except TelegramAPIError:
                        logger.warning("Meme: не удалось удалить служебное сообщение (id %s)", sent.message_id)
                await asyncio.to_thread(repo.set_telegram_cache, fresh["id"], file_id, kind,
                                        telegram_cache_key(self.settings.telegram_bot_token))
                return True
            except Exception as exc:  # noqa: BLE001 — одна плохая картинка не должна ломать поиск
                self._failed[row["id"]] = time.monotonic()
                logger.warning("Meme: не удалось получить file_id для %s: %s", row["id"], type(exc).__name__)
                return False

    # ─── inline-запрос ───

    async def _items(self, user_id: int, rows: list[dict], *, use_cache: bool = True) -> list[InlineItem]:
        cache_key = telegram_cache_key(self.settings.telegram_bot_token)
        started, warmed = time.monotonic(), 0
        items: list[InlineItem] = []
        for row in rows:
            cached = use_cache and has_cache(row, cache_key)
            recently_failed = time.monotonic() - self._failed.get(row["id"], -1e9) < FAILED_RETRY_SECONDS
            if (use_cache and not cached and not recently_failed and warmed < WARM_MAX_PER_QUERY
                    and time.monotonic() - started < WARM_BUDGET):
                warmed += 1
                if await self._warm(row, user_id):
                    row = await asyncio.to_thread(repo.get, row["id"]) or row
            item = describe(row, self.service, use_cache=use_cache)
            if item is not None:
                items.append(item)
            else:
                logger.info("Meme: запись %s пока нечем отправить (нет file_id и подходящего URL)", row["id"])
        return items

    async def _answer(self, query: InlineQuery, items: list[InlineItem], next_offset: str) -> None:
        # is_personal: результаты свои у каждого пользователя; короткий cache_time — правки видны почти сразу
        await query.answer(results=[to_result(i) for i in items], cache_time=5, is_personal=True,
                           next_offset=next_offset)

    async def handle(self, query: InlineQuery) -> None:
        user_id = query.from_user.id if query.from_user else None
        if not is_allowed(user_id, self.settings.telegram_admin_ids):
            await query.answer(results=[], cache_time=1, is_personal=True)
            return
        try:
            rows, next_offset = await asyncio.to_thread(search_page, query.query or "", parse_offset(query.offset))
            items = await self._items(user_id, rows)  # type: ignore[arg-type]
            try:
                await self._answer(query, items, next_offset)
            except TelegramBadRequest as exc:
                text = str(exc).lower()
                if "identifier" not in text and "file" not in text:
                    raise
                # file_id перестал приниматься (например, сменили токен): сбрасываем кэш и отвечаем без него
                logger.warning("Meme: Telegram отверг file_id, кэш сброшен")
                for item in items:
                    if item.file_id:
                        await asyncio.to_thread(repo.clear_telegram_cache, item.id)
                await self._answer(query, await self._items(user_id, rows, use_cache=False), next_offset)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001 — на любой сбой пользователь должен получить ответ, а не вечный спиннер
            logger.warning("Meme: inline-запрос не обработан: %s", type(exc).__name__)
            try:
                await query.answer(results=[], cache_time=1, is_personal=True)
            except TelegramAPIError:
                pass
