"""Конфигурация S3 API из общего Settings."""

from __future__ import annotations

import re
from dataclasses import dataclass

from polaris.s3.credentials import CredentialProvider, StaticCredentialProvider

MIB = 1024 * 1024


@dataclass(frozen=True)
class S3Config:
    enabled: bool = False
    path_prefix: str = "/s3"
    region: str = "us-east-1"
    max_request_size: int = 5120 * MIB
    clock_skew_seconds: int = 900
    body_timeout: float = 60.0
    max_xml_body: int = 4 * MIB

    def validate(self) -> list[str]:
        errors = []
        if not re.fullmatch(r"(/[A-Za-z0-9._~-]+)+", self.path_prefix):
            errors.append("S3_PATH_PREFIX: путь вида /s3 (без завершающего /; корень занят Mini App)")
        if not re.fullmatch(r"[a-z]{2}-[a-z]+-\d+", self.region):
            errors.append("S3_REGION: ожидается значение вида us-east-1")
        if self.max_request_size < 1 or self.clock_skew_seconds < 1 or self.body_timeout <= 0:
            errors.append("S3_MAX_REQUEST_SIZE_MIB / S3_CLOCK_SKEW_SECONDS / S3_BODY_TIMEOUT должны быть положительными")
        return errors

    @classmethod
    def from_settings(cls, settings) -> "S3Config":
        return cls(
            enabled=settings.s3_enabled,
            path_prefix=settings.s3_path_prefix.rstrip("/"),  # "/" -> "" -> ошибка валидации, а не молчаливая подмена
            region=settings.s3_region,
            max_request_size=settings.s3_max_request_size_mib * MIB,
            clock_skew_seconds=settings.s3_clock_skew_seconds,
            body_timeout=settings.s3_body_timeout,
        )


def credential_provider(settings) -> CredentialProvider:
    return StaticCredentialProvider.from_settings(settings)
