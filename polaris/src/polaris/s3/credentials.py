"""Источники S3-ключей. Сейчас — статическая конфигурация; интерфейс позволяет позже подключить
полноценную систему ключей, не трогая проверку подписи."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Credential:
    access_key_id: str
    secret_access_key: str = field(repr=False)
    enabled: bool = True


class CredentialProvider(ABC):
    @abstractmethod
    def lookup(self, access_key_id: str) -> Credential | None:
        """Ключ по идентификатору или None. Отключённые ключи не должны возвращаться."""


class StaticCredentialProvider(CredentialProvider):
    def __init__(self, credentials: list[Credential]) -> None:
        self._by_id = {c.access_key_id: c for c in credentials if c.enabled}

    def lookup(self, access_key_id: str) -> Credential | None:
        return self._by_id.get(access_key_id)

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
