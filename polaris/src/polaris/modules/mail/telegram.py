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


_RECIPIENT_PROBLEMS = (
    "chat not found", "thread not found", "message thread", "not enough rights", "have no rights",
    "bot was blocked", "bot was kicked", "user is deactivated", "forbidden", "peer_id",
    "chat_write_forbidden", "group chat was upgraded",
)


def _recipient_problem(exc: TelegramError) -> bool:
    """Ошибка не в письме, а в получателе (чат, тема, права) — повтор другим способом не поможет."""
    text = exc.description.lower()
    return any(marker in text for marker in _RECIPIENT_PROBLEMS)


def _rich_unsupported(exc: TelegramError) -> bool:
    """sendRichMessage недоступен или не принял сообщение — пробуем обычный sendMessage.

    Раньше на обычное сообщение переходили только при «понятных» ошибках разметки; любой другой ответ 400
    (например, на нестандартное письмо) означал, что письмо терялось. Теперь запасной вариант включается при любом
    400/404, кроме ошибок получателя (чат не найден, нет прав и т. п.): там он только удвоил бы запросы."""
    return exc.status in (400, 404) and not _recipient_problem(exc)


def _parse_error(exc: TelegramError) -> bool:
    text = exc.description.lower()
    return exc.status == 400 and any(k in text for k in ("parse", "entit", "tag"))


def html_to_plain(text: str) -> str:
    """Запасной текст без разметки: убираем теги, ссылки оставляем в виде «название (адрес)»."""
    import html as _html
    import re as _re

    text = _re.sub(r'<a href="([^"]*)">(.*?)</a>', lambda m: f"{m.group(2)} ({_html.unescape(m.group(1))})", text, flags=_re.S)
    return _html.unescape(_re.sub(r"</?(?:b|i|u|s|code|pre|blockquote)[^>]*>", "", text))[:4000]


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
    try:
        call(token, "sendMessage", fallback)
    except TelegramError as exc:
        if not _parse_error(exc):
            raise
        # последняя страховка: Telegram не принял и HTML — отправляем обычным текстом без разметки
        logger.warning("sendMessage с HTML не прошёл (%s) — отправляю обычным текстом", exc.description)
        plain = _with_thread(
            {"chat_id": _chat_id(dest), "text": html_to_plain(fallback_html),
             "link_preview_options": {"is_disabled": True}},
            dest,
        )
        call(token, "sendMessage", plain)


def send_html(token: str, dest: Destination, html_text: str) -> None:
    """Обычное сообщение (sendMessage) с HTML-разметкой."""
    payload = _with_thread(
        {
            "chat_id": _chat_id(dest),
            "text": html_text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        },
        dest,
    )
    call(token, "sendMessage", payload)


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
