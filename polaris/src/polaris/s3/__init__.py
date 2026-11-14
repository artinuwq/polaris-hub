"""S3-совместимый API поверх Polaris Storage (ограниченная реализация, см. docs/s3-api.md)."""

from __future__ import annotations

import logging

from polaris.s3.api import S3App
from polaris.s3.config import S3Config, credential_provider
from polaris.s3.service import S3Service

logger = logging.getLogger(__name__)

__all__ = ["S3App", "S3Config", "S3Service", "create_s3_app"]


def create_s3_app(settings, get_core=None) -> S3App | None:
    """Построить ASGI-приложение S3 или вернуть None (выключено/неверно настроено — Polaris при этом стартует)."""
    config = S3Config.from_settings(settings)
    if not config.enabled:
        return None
    problems = config.validate()
    provider = credential_provider(settings)
    if not provider:
        problems.append("не задан ни один ключ: S3_ACCESS_KEY_ID + S3_SECRET_ACCESS_KEY или S3_CREDENTIALS")
    if problems:
        logger.error("S3 API не запущен: %s", "; ".join(problems))
        return None
    if get_core is None:
        from polaris.storage import get_storage_service
        from polaris.storage.errors import StorageUnavailable

        def get_core():
            service = get_storage_service()
            if service is None:
                raise StorageUnavailable("Storage не инициализирован")
            return service.get_core()

    logger.info("S3 API включён: префикс %s, регион %s", config.path_prefix, config.region)
    return S3App(config, provider, S3Service(get_core, region=config.region))
