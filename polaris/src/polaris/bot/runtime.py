"""Polaris bot entry point — now using aiogram 3."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from polaris.infra.settings import Settings
from polaris.shared.exceptions import ConfigurationError
from polaris.bot.aiogram_bot import run_bot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = Settings.from_env()
    if not settings.telegram_bot_token:
        raise ConfigurationError("TELEGRAM_BOT_TOKEN не задан")
    logger.info("Starting Polaris bot (aiogram 3)…")
    await run_bot()


if __name__ == "__main__":
    asyncio.run(main())