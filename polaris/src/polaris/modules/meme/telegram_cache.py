"""Кэш Telegram для Polaris Meme: получить file_id, чтобы inline отдавал картинку как обычное фото без URL.

Storage остаётся источником правды (байты оригинала); здесь только копия «для доставки»: Telegram пережимает фото,
поэтому в чат уходит его версия. Потеряли file_id — его можно получить заново из Storage (бот делает это сам при поиске).

Куда отправляется копия:
- MEME_CACHE_CHAT_ID задан — в этот закрытый чат/канал (бот должен быть в нём), сообщения там остаются;
- не задан — в личку админа, а служебное сообщение сразу удаляется (так «не мусорим» в чате).
Используется токен ОСНОВНОГО бота (file_id действует только для бота, который его получил), а не бота Storage.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import requests

from polaris.modules.meme import repository as repo
from polaris.storage.backends.telegram import _IPv4Adapter

logger = logging.getLogger(__name__)

TIMEOUT = (5, 60)


class CacheError(Exception):
    """Не удалось получить file_id; текст безопасен для логов (токен вычищен)."""


CHAT_SETTING = "cache_chat_id"
_CHAT_RE = re.compile(r"-?\d{5,20}|@[A-Za-z][A-Za-z0-9_]{4,31}")


def normalize_chat(value: str) -> str:
    """Числовой id (-100…) или @username; пустая строка — сбросить. Ссылки t.me и прочее — понятная ошибка."""
    text = (value or "").strip()
    if text and not _CHAT_RE.fullmatch(text):
        raise ValueError("Укажите числовой id чата или канала (например -1001234567890) либо @username публичного канала")
    return text


def configured_chat(settings) -> tuple[str, str]:
    """(чат, откуда): значение из меню Meme важнее MEME_CACHE_CHAT_ID из .env; ('', '') — не задан."""
    chat = repo.get_setting(CHAT_SETTING).strip()
    if chat:
        return chat, "settings"
    env = (getattr(settings, "meme_cache_chat_id", "") or "").strip()
    return (env, "env") if env else ("", "")


def cache_target(settings, user_id: int | None = None) -> tuple[int | str, bool] | None:
    """(chat_id, удалять ли сообщение после получения file_id) или None, если отправлять некуда."""
    chat = configured_chat(settings)[0]
    if chat:
        return (int(chat) if re.fullmatch(r"-?\d+", chat) else chat), False
    fallback = user_id if user_id is not None else (settings.telegram_admin_ids[0] if settings.telegram_admin_ids else None)
    return (fallback, True) if fallback is not None else None


def _session(settings) -> requests.Session:
    session = requests.Session()
    if settings.telegram_proxy_url:
        session.proxies = {"http": settings.telegram_proxy_url, "https": settings.telegram_proxy_url}
    elif settings.telegram_force_ipv4:
        adapter = _IPv4Adapter()
        session.mount("http://", adapter)
        session.mount("https://", adapter)
    return session


def _call(session: requests.Session, settings, method: str, **kwargs) -> dict[str, Any]:
    url = f"{settings.telegram_api_base.rstrip('/')}/bot{settings.telegram_bot_token}/{method}"
    try:
        response = session.post(url, timeout=TIMEOUT, **kwargs)
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        raise CacheError(f"{method}: {type(exc).__name__}") from None  # текст исключения может содержать URL с токеном
    if not payload.get("ok"):
        raise CacheError(f"{method}: {str(payload.get('description', 'ошибка'))[:200]}")
    return payload["result"]


def push(settings, row: dict[str, Any], data: bytes, user_id: int | None = None) -> tuple[str, str] | None:
    """Отправить изображение в Telegram и вернуть (file_id, kind). None — отправлять некуда или нет токена бота.
    Бросает CacheError при сбое."""
    if not settings.telegram_bot_token:
        return None
    target = cache_target(settings, user_id)
    if target is None:
        return None
    chat_id, delete_after = target
    is_gif = row["mime_type"] == "image/gif"
    method, field = ("sendAnimation", "animation") if is_gif else ("sendPhoto", "photo")
    ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}.get(row["mime_type"], "")
    with _session(settings) as session:
        message = _call(session, settings, method, data={"chat_id": chat_id, "disable_notification": "true"},
                        files={field: (f"meme{ext}", data, row["mime_type"])})
        try:
            if is_gif:
                file_id = message["animation"]["file_id"]
            else:
                file_id = message["photo"][-1]["file_id"]
        except (KeyError, IndexError, TypeError):
            raise CacheError(f"{method}: в ответе нет file_id") from None
        if delete_after:
            try:
                _call(session, settings, "deleteMessage", data={"chat_id": chat_id, "message_id": message["message_id"]})
            except CacheError:
                logger.warning("Meme: не удалось удалить служебное сообщение в чате кэша")
    return file_id, "animation" if is_gif else "photo"


_HINTS = (
    ("chat not found", "Чат не найден: добавьте бота в чат или канал (в канале — администратором с правом публикации) и проверьте id"),
    ("bot is not a member", "Бота нет в этом чате или канале — добавьте его"),
    ("not enough rights", "У бота нет права писать в этот чат — в канале сделайте его администратором"),
    ("have no rights", "У бота нет права писать в этот чат — в канале сделайте его администратором"),
    ("forbidden", "Telegram не пускает бота в этот чат — добавьте бота и дайте право писать"),
)


def check_chat(settings, chat: str) -> dict[str, str]:
    """Проверка чата кэша: бот видит чат и может писать (пишет сообщение и сразу удаляет). Бросает CacheError с подсказкой."""
    if not settings.telegram_bot_token:
        raise CacheError("Не задан TELEGRAM_BOT_TOKEN")
    chat_id: int | str = int(chat) if re.fullmatch(r"-?\d+", chat) else chat
    try:
        with _session(settings) as session:
            info = _call(session, settings, "getChat", data={"chat_id": chat_id})
            sent = _call(session, settings, "sendMessage", data={"chat_id": chat_id, "disable_notification": "true",
                                                                 "text": "Polaris Meme: проверка чата кэша"})
            try:
                _call(session, settings, "deleteMessage", data={"chat_id": chat_id, "message_id": sent["message_id"]})
            except CacheError:
                pass  # писать можно — для кэша этого достаточно
    except CacheError as exc:
        lowered = str(exc).lower()
        hint = next((text for key, text in _HINTS if key in lowered), None)
        raise CacheError(hint or str(exc)) from None
    return {"title": str(info.get("title") or info.get("first_name") or chat), "type": str(info.get("type", ""))}
