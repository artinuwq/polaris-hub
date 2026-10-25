"""Отправка писем в Telegram через sendRichMessage (Bot API 10.1+).

Работает на requests: токен бота берётся из Settings, прокси (HTTP или
SOCKS5) — из необязательной переменной TELEGRAM_PROXY_URL.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

from polaris.modules.mail.destinations import Destination

logger = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://api.telegram.org"


class TelegramError(Exception):
    """Ошибка Telegram API или сети. Токен в тексте никогда не содержится."""

    def __init__(self, description: str, status: int = 0, retry_after: int = 0) -> None:
        super().__init__(description)
        self.description = description
        self.status = status
        self.retry_after = retry_after

    @property
    def permanent(self) -> bool:
        """Похоже на ошибку настройки (нет чата/прав/темы), а не на временный сбой."""
        return self.status in (400, 403)


def _proxies() -> dict[str, str] | None:
    url = os.getenv("TELEGRAM_PROXY_URL", "").strip()
    return {"http": url, "https": url} if url else None


def _scrub(text: str, token: str) -> str:
    if token:
        text = text.replace(token, "[hidden]")
    return text[:300]


def call(token: str, method: str, payload: dict[str, Any], timeout: int = 30) -> Any:
    base = os.getenv("TELEGRAM_API_BASE", DEFAULT_API_BASE).rstrip("/")
    url = f"{base}/bot{token}/{method}"
    try:
        response = requests.post(url, json=payload, proxies=_proxies(), timeout=timeout)
    except requests.RequestException as exc:
        # В тексте requests-исключений бывает URL с токеном — вычищаем.
        raise TelegramError("Нет связи с Telegram: " + _scrub(str(exc), token)) from None

    try:
        data = response.json()
    except ValueError:
        raise TelegramError(f"Telegram вернул не JSON (HTTP {response.status_code})", response.status_code) from None

    if not data.get("ok"):
        params = data.get("parameters") or {}
        raise TelegramError(
            _scrub(str(data.get("description", "неизвестная ошибка")), token),
            int(data.get("error_code") or response.status_code),
            int(params.get("retry_after") or 0),
        )
    return data.get("result")


def _chat_id(dest: Destination) -> int | str:
    try:
        return int(dest.chat_id)
    except ValueError:
        return dest.chat_id  # @username


def _with_thread(payload: dict[str, Any], dest: Destination) -> dict[str, Any]:
    if dest.thread_id:
        payload["message_thread_id"] = dest.thread_id
    return payload


def _rich_unsupported(exc: TelegramError) -> bool:
    """sendRichMessage недоступен / не разобрал разметку — можно упасть на обычный sendMessage."""
    text = exc.description.lower()
    if exc.status == 404:
        return True
    return exc.status == 400 and any(k in text for k in ("can't parse", "parse", "unknown method", "rich", "entities"))


def send_rich(token: str, dest: Destination, markdown: str, fallback_html: str | None = None) -> None:
    payload = _with_thread({"chat_id": _chat_id(dest), "rich_message": {"markdown": markdown}}, dest)
    try:
        call(token, "sendRichMessage", payload)
        return
    except TelegramError as exc:
        if fallback_html is None or not _rich_unsupported(exc):
            raise
        logger.warning("sendRichMessage не прошёл (%s) — отправляю обычным сообщением", exc.description)

    fallback = _with_thread(
        {
            "chat_id": _chat_id(dest),
            "text": fallback_html,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        },
        dest,
    )
    call(token, "sendMessage", fallback)


def send_test(token: str, dest: Destination) -> None:
    text = "✅ **Polaris:** тестовое сообщение\n\nПочта будет приходить сюда."
    html_text = "✅ <b>Polaris:</b> тестовое сообщение\n\nПочта будет приходить сюда."
    send_rich(token, dest, text, fallback_html=html_text)


def explain(exc: TelegramError) -> str:
    """Понятное объяснение типичных ошибок доставки."""
    text = exc.description.lower()
    if "can't initiate conversation" in text:
        return "Бот не может писать первым: откройте чат с ботом и нажмите /start."
    if "chat not found" in text:
        return "Чат не найден: проверьте ID и что бот добавлен в группу."
    if "thread not found" in text:
        return "Тема не найдена: проверьте номер темы."
    if "topic_closed" in text or "topic closed" in text:
        return "Тема закрыта."
    if "not enough rights" in text or "have no rights" in text:
        return "У бота нет прав писать в этот чат/тему."
    if "kicked" in text or "blocked" in text or "was removed" in text:
        return "Бот удалён из чата или заблокирован."
    if exc.status == 401:
        return "Telegram не принимает токен бота (401)."
    if exc.status == 429:
        return f"Telegram просит подождать {exc.retry_after} с (лимит отправки)."
    return exc.description
