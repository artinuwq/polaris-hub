"""Polaris bot entry point — aiogram 3.

`run_bot()` — синхронная обёртка, её используют scripts/run_bot.py
и runtime/bot.py. Асинхронная версия — `run_bot_async()`.
"""

from __future__ import annotations

import asyncio
import logging

from polaris.infra.settings import Settings
from polaris.shared.exceptions import ConfigurationError
from polaris.bot.aiogram_bot import run_bot as run_bot_async

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
    await run_bot_async()


def run_bot() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run_bot()
