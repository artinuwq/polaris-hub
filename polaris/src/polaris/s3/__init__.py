"""S3-совместимый API поверх Polaris Storage (ограниченная реализация, см. docs/s3-api.md)."""

from __future__ import annotations

import logging

from polaris.s3.api import S3App
from polaris.s3.config import S3Config
from polaris.s3.credentials import (
    CompositeCredentialProvider, DatabaseCredentialProvider, StaticCredentialProvider,
)
from polaris.s3.service import S3Service

logger = logging.getLogger(__name__)

__all__ = ["S3App", "S3Config", "S3Service", "create_s3_app", "get_s3_app"]

_current: S3App | None = None


def get_s3_app() -> S3App | None:
    """Работающее S3-приложение (None — выключено или не запущено)."""
    return _current


def create_s3_app(settings, get_core=None, get_store=None) -> S3App | None:
    """Построить ASGI-приложение S3 или вернуть None (выключено/неверно настроено — Polaris при этом стартует).

    Ключи: из конфигурации (.env, полный доступ) и из Access Management (права и buckets, управляются в UI)."""
    global _current
    config = S3Config.from_settings(settings)
    if not config.enabled:
        return None
    problems = config.validate()
    if problems:
        logger.error("S3 API не запущен: %s", "; ".join(problems))
        return None
    if get_core is None or get_store is None:
        from polaris.storage import get_storage_service
        from polaris.storage.errors import StorageUnavailable

        def _service():
            service = get_storage_service()
            if service is None:
                raise StorageUnavailable("Storage не инициализирован")
            return service

        if get_core is None:
            def get_core():
                return _service().get_core()

        if get_store is None:
            def get_store():
                service = get_storage_service()
                return service.access_keys if service is not None else None

    static = StaticCredentialProvider.from_settings(settings)
    if not static:
        logger.warning("S3: в .env нет ключей — используйте Access Keys в разделе Storage")
    provider = CompositeCredentialProvider(static, DatabaseCredentialProvider(get_store))
    logger.info("S3 API включён: префикс %s, регион %s, ключей в конфигурации: %d", config.path_prefix,
                config.region, len(static.ids()))
    app = S3App(config, provider, S3Service(get_core, region=config.region))
    app.static_key_ids = static.ids()
    _current = app
    return app
