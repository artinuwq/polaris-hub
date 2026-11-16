"""Источники S3-ключей и права доступа.

Проверка подписи одна (sigv4.verify); провайдеры только отдают ключ и его секрет:
* StaticCredentialProvider — ключи из .env (S3_ACCESS_KEY_ID / S3_CREDENTIALS): полный доступ, как раньше;
* DatabaseCredentialProvider — ключи из Access Management (права и buckets, отзыв действует немедленно);
* CompositeCredentialProvider — сначала конфигурация, затем база.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Credential:
    access_key_id: str
    secret_access_key: str = field(repr=False)
    enabled: bool = True
    credential_id: str | None = None  # id в Access Management; None — ключ из конфигурации
    name: str = ""
    permissions: frozenset[str] | None = None  # None — все права
    buckets: frozenset[str] | None = None  # None — все buckets

    @property
    def unrestricted(self) -> bool:
        """Ключ из конфигурации: полный доступ, включая создание/удаление buckets."""
        return self.credential_id is None and self.permissions is None and self.buckets is None

    def allows(self, permission: str, bucket: str) -> bool:
        if self.permissions is not None and permission not in self.permissions:
            return False
        return self.buckets is None or bucket in self.buckets

    def can_see_bucket(self, bucket: str) -> bool:
        return self.buckets is None or bucket in self.buckets


class CredentialProvider(ABC):
    @abstractmethod
    def lookup(self, access_key_id: str) -> Credential | None:
        """Ключ по идентификатору или None. Отключённые/отозванные ключи не должны возвращаться."""

    def record_use(self, credential: Credential | None) -> None:  # noqa: B027
        """Вызывается после успешной проверки подписи (учёт «последнего использования»)."""


class StaticCredentialProvider(CredentialProvider):
    def __init__(self, credentials: list[Credential]) -> None:
        self._by_id = {c.access_key_id: c for c in credentials if c.enabled}

    def lookup(self, access_key_id: str) -> Credential | None:
        return self._by_id.get(access_key_id)

    def ids(self) -> list[str]:
        return sorted(self._by_id)

    def __bool__(self) -> bool:
        return bool(self._by_id)

    @classmethod
    def from_settings(cls, settings) -> "StaticCredentialProvider":
        """S3_ACCESS_KEY_ID/S3_SECRET_ACCESS_KEY + S3_CREDENTIALS='id:secret,id2:secret2'; S3_DISABLED_KEYS='id'."""
        disabled = {x.strip() for x in settings.s3_disabled_keys.split(",") if x.strip()}
        pairs: list[tuple[str, str]] = []
        if settings.s3_access_key_id and settings.s3_secret_access_key:
            pairs.append((settings.s3_access_key_id, settings.s3_secret_access_key))
        for item in settings.s3_credentials.split(","):
            key_id, sep, secret = item.strip().partition(":")
            if sep and key_id.strip() and secret:
                pairs.append((key_id.strip(), secret))
        return cls([Credential(k, s, k not in disabled) for k, s in pairs])


class DatabaseCredentialProvider(CredentialProvider):
    """Ключи из Access Management. Каждая проверка читает БД: отзыв и ротация действуют немедленно."""

    def __init__(self, get_store: Callable[[], object | None]) -> None:
        self._get_store = get_store

    def lookup(self, access_key_id: str) -> Credential | None:
        from polaris.storage.errors import StorageUnavailable

        store = self._get_store()
        if store is None:
            raise StorageUnavailable("Access Management недоступен")
        resolved = store.resolve(access_key_id)  # type: ignore[attr-defined]
        if resolved is None:
            return None
        key = resolved.key
        return Credential(
            key.access_key_id, resolved.secret, True, credential_id=key.id, name=key.name,
            permissions=frozenset(key.permissions), buckets=None if key.all_buckets else frozenset(key.buckets))

    def record_use(self, credential: Credential | None) -> None:
        if credential is None or credential.credential_id is None:
            return
        try:
            store = self._get_store()
            if store is not None:
                store.touch(credential.credential_id)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 — учёт использования не должен ломать запрос
            logger.debug("S3: не удалось отметить использование ключа", exc_info=True)


class CompositeCredentialProvider(CredentialProvider):
    def __init__(self, *providers: CredentialProvider) -> None:
        self.providers = providers

    def lookup(self, access_key_id: str) -> Credential | None:
        for provider in self.providers:
            found = provider.lookup(access_key_id)
            if found is not None:
                return found
        return None

    def record_use(self, credential: Credential | None) -> None:
        for provider in self.providers:
            provider.record_use(credential)
