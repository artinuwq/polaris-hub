"""Polaris Meme для Telegram Inline Mode — чистая часть (без aiogram): поиск страницы и выбор вида результата.

Как изображение попадает в чат как обычное фото:
 1. «cached» — у записи есть file_id, который бот получил при первой отправке файла (InlineQueryResultCachedPhoto /
    CachedGif). Быстрее всего, публичный URL не нужен, Telegram сам хранит файл. Основной путь.
 2. «photo» — запасной путь, пока file_id ещё нет: подписанный HTTPS-URL на оригинал (InlineQueryResultPhoto).
    Bot API принимает по URL только JPEG до 5 МБ и только с публичным https WEBAPP_URL, иначе путь недоступен.
Внутренние id объектов и чанков Storage в результатах не участвуют.
"""

from __future__ import annotations

from dataclasses import dataclass

from polaris.modules.meme import repository as repo
from polaris.modules.meme.service import MemeService, telegram_cache_key

PAGE = 20                          # Bot API разрешает до 50 результатов за ответ
URL_PHOTO_LIMIT = 5 * 1024 * 1024  # лимит Bot API для InlineQueryResultPhoto по URL
URL_TTL = 3600                     # результат может быть выбран через время после показа: ссылка живёт час


@dataclass(frozen=True)
class InlineItem:
    id: str
    kind: str                      # cached_photo | cached_gif | photo
    title: str
    description: str
    file_id: str | None = None
    url: str | None = None
    width: int = 0
    height: int = 0


def parse_offset(raw: str | None) -> int:
    try:
        return max(0, int(raw or 0))
    except (TypeError, ValueError):
        return 0


def is_allowed(user_id: int | None, admin_ids: list[int]) -> bool:
    """Библиотека личная: inline открыт только админам и только если они явно заданы (в отличие от команд бота,
    где пустой список означает «разрешено всем» — для приватных картинок это слишком мягко)."""
    return bool(admin_ids) and user_id in admin_ids


def search_page(query: str, offset: int = 0) -> tuple[list[dict], str]:
    """Страница результатов и next_offset для Telegram ('' — больше нет)."""
    rows, total = repo.search(query, "", PAGE, offset)
    nxt = offset + len(rows)
    return rows, (str(nxt) if nxt < total else "")


def public_base(webapp_url: str) -> str | None:
    base = (webapp_url or "").strip().rstrip("/")
    return base if base.startswith("https://") else None


def has_cache(row: dict, cache_key: str) -> bool:
    return bool(row.get("tg_file_id") and cache_key and row.get("tg_cache_key") == cache_key)


def _description(row: dict) -> str:
    text = " ".join("#" + t.replace(" ", "_") for t in row["tags"])
    return (text or row["description"])[:120]


def describe(row: dict, service: MemeService, *, use_cache: bool = True) -> InlineItem | None:
    """Вид результата для записи или None, если отправить её сейчас нечем (нет file_id и нет подходящего URL)."""
    cache_key = telegram_cache_key(service.settings.telegram_bot_token)
    common = {"id": row["id"], "title": row["title"][:100], "description": _description(row)}
    if use_cache and has_cache(row, cache_key):
        kind = "cached_gif" if row.get("tg_file_kind") == "animation" else "cached_photo"
        return InlineItem(kind=kind, file_id=row["tg_file_id"], **common)
    base = public_base(service.settings.webapp_url)
    if base and row["mime_type"] == "image/jpeg" and row["size_bytes"] <= URL_PHOTO_LIMIT:
        return InlineItem(kind="photo", url=base + service.image_path(row["id"], ttl=URL_TTL),
                          width=row["width"], height=row["height"], **common)
    return None
