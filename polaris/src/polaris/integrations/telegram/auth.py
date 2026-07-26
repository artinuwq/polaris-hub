"""Проверка Telegram Mini App initData."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

from polaris.shared.exceptions import AuthorizationError


@dataclass(frozen=True)
class TelegramWebAppUser:
    id: int
    first_name: str = ""
    last_name: str = ""
    username: str = ""
    language_code: str = ""
    is_premium: bool = False

    @property
    def display_name(self) -> str:
        name = " ".join(part for part in (self.first_name, self.last_name) if part).strip()
        if name:
            return name
        if self.username:
            return f"@{self.username}"
        return str(self.id)


def parse_webapp_user(raw: str | None) -> TelegramWebAppUser | None:
    if not raw:
        return None
    data = json.loads(raw)
    return TelegramWebAppUser(
        id=int(data["id"]),
        first_name=str(data.get("first_name") or ""),
        last_name=str(data.get("last_name") or ""),
        username=str(data.get("username") or ""),
        language_code=str(data.get("language_code") or ""),
        is_premium=bool(data.get("is_premium")),
    )


def validate_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age_seconds: int = 86400,
) -> dict[str, str]:
    """Проверить подпись initData по документации Telegram Web Apps.

    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    if not init_data or not init_data.strip():
        raise AuthorizationError("Нет initData от Telegram")
    if not bot_token:
        raise AuthorizationError("TELEGRAM_BOT_TOKEN не задан на сервере")

    pairs = parse_qsl(init_data, keep_blank_values=True)
    data = dict(pairs)
    received_hash = data.pop("hash", None)
    if not received_hash:
        raise AuthorizationError("В initData нет hash")

    # data-check-string: ключи по алфавиту, без hash
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(data.items()))

    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    calculated = hmac.new(secret_key, check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(calculated, received_hash):
        raise AuthorizationError("Неверная подпись initData")

    auth_date_raw = data.get("auth_date")
    if not auth_date_raw:
        raise AuthorizationError("В initData нет auth_date")
    try:
        auth_date = int(auth_date_raw)
    except ValueError as exc:
        raise AuthorizationError("Некорректный auth_date") from exc

    age = int(time.time()) - auth_date
    if age < 0:
        raise AuthorizationError("auth_date из будущего")
    if max_age_seconds > 0 and age > max_age_seconds:
        raise AuthorizationError("initData устарел, переоткройте Mini App")

    return data


def authenticate_webapp(
    init_data: str,
    bot_token: str,
    *,
    admin_ids: list[int] | None = None,
    require_admin: bool = True,
    max_age_seconds: int = 86400,
) -> TelegramWebAppUser:
    data = validate_init_data(init_data, bot_token, max_age_seconds=max_age_seconds)
    user = parse_webapp_user(data.get("user"))
    if user is None:
        raise AuthorizationError("В initData нет user")

    if require_admin:
        allowed = admin_ids or []
        if not allowed:
            raise AuthorizationError("TELEGRAM_ADMIN_IDS не задан — доступ закрыт")
        if user.id not in allowed:
            raise AuthorizationError("Нет доступа: вы не администратор")

    return user
