"""Бизнес-логика модуля Mail: ящики, получатели, список писем."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from polaris.modules.mail import imap, repository, telegram
from polaris.modules.mail.destinations import DestinationError, parse_destination
from polaris.shared.exceptions import PolarisError

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class MailError(PolarisError):
    """Ошибка операции (текст безопасен для показа пользователю)."""


class NotFoundError(MailError):
    """Объект не найден."""


def providers() -> list[dict[str, str]]:
    return [{"id": key, "label": cfg["label"]} for key, cfg in imap.PROVIDERS.items()]


def overview() -> dict[str, Any]:
    return {
        "providers": providers(),
        "accounts": repository.list_accounts(),
        "destinations": repository.list_destinations(),
    }


# ───────────────────────────── accounts ─────────────────────────────

def _clean_password(provider: str, password: str) -> str:
    password = (password or "").strip()
    if provider == "gmail":  # Google показывает пароль приложения группами через пробел
        password = "".join(password.split())
    return password


def _verify(provider: str, address: str, password: str) -> None:
    try:
        imap.verify_login(provider, address, password)
    except imap.MailConnectionError as exc:
        raise MailError(str(exc)) from None


def add_account(provider: str, address: str, password: str, label: str = "", verify: bool = True) -> dict[str, Any]:
    provider = (provider or "").strip().lower()
    if provider not in imap.PROVIDERS:
        raise MailError("Неизвестный провайдер. Доступны: " + ", ".join(imap.PROVIDERS))
    address = (address or "").strip()
    if not _EMAIL_RE.match(address) or len(address) > 254:
        raise MailError("Укажите корректный адрес почты.")
    password = _clean_password(provider, password)
    if not password:
        raise MailError("Укажите пароль приложения.")
    if verify:
        _verify(provider, address, password)
    try:
        return repository.create_account(provider, address, (label or "").strip()[:80], password)
    except sqlite3.IntegrityError:
        raise MailError("Этот ящик уже добавлен.") from None


def change_account(
    account_id: int,
    label: str | None = None,
    password: str | None = None,
    enabled: bool | None = None,
    verify: bool = True,
) -> dict[str, Any]:
    current = repository.get_account(account_id, include_secret=True)
    if not current:
        raise NotFoundError("Ящик не найден.")
    fields: dict[str, Any] = {}
    if label is not None:
        fields["label"] = label.strip()[:80]
    if enabled is not None:
        fields["enabled"] = bool(enabled)
    if password is not None:
        cleaned = _clean_password(current["provider"], password)
        if not cleaned:
            raise MailError("Пароль не может быть пустым.")
        if verify:
            _verify(current["provider"], current["email"], cleaned)
        fields["password"] = cleaned
    return repository.update_account(account_id, fields) or {}


def remove_account(account_id: int) -> None:
    if not repository.delete_account(account_id):
        raise NotFoundError("Ящик не найден.")


# ───────────────────────────── destinations ─────────────────────────────

def add_destination(target: str, label: str = "") -> dict[str, Any]:
    try:
        parsed = parse_destination(target)
    except DestinationError as exc:
        raise MailError(str(exc)) from None
    label = (label or "").strip()[:80] or parsed.describe()
    return repository.create_destination(label, target.strip()[:200], parsed.chat_id, parsed.thread_id, parsed.kind)


def change_destination(destination_id: int, label: str | None = None, enabled: bool | None = None) -> dict[str, Any]:
    if not repository.get_destination(destination_id):
        raise NotFoundError("Получатель не найден.")
    fields: dict[str, Any] = {}
    if label is not None:
        fields["label"] = label.strip()[:80]
    if enabled is not None:
        fields["enabled"] = bool(enabled)
    return repository.update_destination(destination_id, fields) or {}


def remove_destination(destination_id: int) -> None:
    if not repository.delete_destination(destination_id):
        raise NotFoundError("Получатель не найден.")


def test_destination(destination_id: int, bot_token: str) -> None:
    row = repository.get_destination(destination_id)
    if not row:
        raise NotFoundError("Получатель не найден.")
    if not bot_token:
        raise MailError("TELEGRAM_BOT_TOKEN не задан — отправить тест нельзя.")
    from polaris.modules.mail.destinations import Destination

    dest = Destination(row["chat_id"], row["thread_id"], row["kind"])
    try:
        telegram.send_test(bot_token, dest)
    except telegram.TelegramError as exc:
        message = telegram.explain(exc)
        repository.set_destination_status(destination_id, "error", message)
        raise MailError(message) from None
    repository.set_destination_status(destination_id, "ok")


# ───────────────────────────── messages ─────────────────────────────

def list_messages(
    account_ids: list[int] | None = None,
    limit: int = 30,
    before_id: int | None = None,
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 100))
    rows = repository.list_messages(account_ids, limit + 1, before_id)
    return {"messages": rows[:limit], "has_more": len(rows) > limit}
