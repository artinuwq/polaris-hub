"""Shared authentication dependency for FastAPI routes."""

from __future__ import annotations

from fastapi import Header, HTTPException

from polaris.infra.settings import Settings
from polaris.integrations.telegram.auth import TelegramWebAppUser, authenticate_webapp
from polaris.shared.exceptions import AuthorizationError

settings = Settings.from_env()


def require_admin(
    x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"),
    x_polaris_token: str | None = Header(default=None, alias="X-Polaris-Token"),
) -> TelegramWebAppUser | None:
    """Доступ: валидный Telegram initData админа ИЛИ UPDATE_API_TOKEN."""
    if x_telegram_init_data:
        try:
            return authenticate_webapp(
                x_telegram_init_data,
                settings.telegram_bot_token,
                admin_ids=settings.telegram_admin_ids,
                require_admin=True,
                max_age_seconds=settings.telegram_init_data_max_age,
            )
        except AuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    expected = settings.update_api_token
    if expected and x_polaris_token == expected:
        return None

    if settings.debug and not expected and not settings.telegram_bot_token:
        return None

    raise HTTPException(
        status_code=401,
        detail="Откройте Mini App из Telegram или передайте X-Polaris-Token",
    )