"""Telegram integration."""

from polaris.integrations.telegram.auth import (
    TelegramWebAppUser,
    authenticate_webapp,
    validate_init_data,
)

__all__ = [
    "TelegramWebAppUser",
    "authenticate_webapp",
    "validate_init_data",
]
