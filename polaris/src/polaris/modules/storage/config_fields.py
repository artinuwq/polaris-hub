"""Описание настраиваемых из интерфейса параметров Storage/S3, их проверка и маскирование секретов."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

RESERVED_PREFIXES = {"api", "assets", "health", "docs", "openapi.json", "redoc"}
_TOKEN_RE = re.compile(r"^\d{5,16}:[A-Za-z0-9_-]{30,}$")
_CHAT_RE = re.compile(r"^(-?\d{1,20}|@[A-Za-z0-9_]{4,32})$")
_PREFIX_RE = re.compile(r"^(/[A-Za-z0-9._~-]+)+$")
_REGION_RE = re.compile(r"^[a-z]{2}-[a-z]+-\d+$")


class FieldError(ValueError):
    pass


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "1", "yes", "false", "0", "no"}:
        return value.strip().lower() in {"true", "1", "yes"}
    raise FieldError("ожидается true или false")


def _int(low: int, high: int) -> Callable[[Any], int]:
    def parse(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, str)) or (isinstance(value, str) and not re.fullmatch(r"-?\d+", value.strip())):
            raise FieldError("ожидается целое число")
        number = int(value)
        if not low <= number <= high:
            raise FieldError(f"допустимо от {low} до {high}")
        return number
    return parse


def _token(value: Any) -> str:
    if not isinstance(value, str):
        raise FieldError("ожидается строка")
    value = value.strip()
    if value and not _TOKEN_RE.match(value):
        raise FieldError("не похоже на токен бота (формат 123456:ABC…)")
    return value


def _chat(value: Any) -> str:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise FieldError("ожидается строка")
    value = str(value).strip()
    if value and not _CHAT_RE.match(value):
        raise FieldError("нужен числовой chat id (например -1001234567890) или @username канала")
    return value


def _prefix(value: Any) -> str:
    if not isinstance(value, str):
        raise FieldError("ожидается строка")
    value = value.strip().rstrip("/")
    if not _PREFIX_RE.match(value):
        raise FieldError("путь вида /s3 (латиница, цифры, - _ . ~; без завершающего /)")
    if value.split("/")[1].lower() in RESERVED_PREFIXES:
        raise FieldError("этот путь занят Polaris (api, assets, health, docs)")
    return value


def _region(value: Any) -> str:
    if not isinstance(value, str) or not _REGION_RE.match(value.strip()):
        raise FieldError("ожидается значение вида us-east-1")
    return value.strip()


@dataclass(frozen=True)
class Field:
    key: str  # имя в API
    env: str  # переменная в .env
    attr: str  # поле Settings
    group: str
    label: str
    kind: str  # bool | text | secret | int
    parse: Callable[[Any], Any]
    help: str = ""
    secret: bool = False


FIELDS: list[Field] = [
    Field("storage_enabled", "STORAGE_ENABLED", "storage_enabled", "general", "Storage включён", "bool", _bool),
    Field("telegram_bot_token", "STORAGE_TELEGRAM_BOT_TOKEN", "storage_telegram_bot_token", "telegram",
          "Telegram Bot Token (для Storage)", "secret", _token,
          "Отдельный бот хранилища. Пусто — используется основной TELEGRAM_BOT_TOKEN Polaris (его отсюда менять нельзя: "
          "им проверяется вход в Mini App).", secret=True),
    Field("telegram_chat_id", "CLOUD_STORAGE_CHAT_ID", "cloud_storage_chat_id", "telegram", "ID чата хранения", "text",
          _chat),
    Field("s3_enabled", "S3_ENABLED", "s3_enabled", "s3", "S3 API включён", "bool", _bool),
    Field("s3_path_prefix", "S3_PATH_PREFIX", "s3_path_prefix", "s3", "Путь endpoint", "text", _prefix),
    Field("s3_region", "S3_REGION", "s3_region", "s3", "Регион", "text", _region, "Регион в подписи клиента должен совпадать."),
    Field("s3_max_request_size_mib", "S3_MAX_REQUEST_SIZE_MIB", "s3_max_request_size_mib", "s3",
          "Максимальный размер запроса, MiB", "int", _int(1, 5120), "Лимит одного PutObject/UploadPart (S3: до 5120)."),
    Field("s3_clock_skew_seconds", "S3_CLOCK_SKEW_SECONDS", "s3_clock_skew_seconds", "s3",
          "Допустимое расхождение часов SigV4, с", "int", _int(60, 3600), "По умолчанию 900 (как в AWS)."),
    Field("s3_min_part_size_mib", "S3_MIN_PART_SIZE_MIB", "s3_min_part_size_mib", "s3",
          "Минимальный размер части multipart, MiB", "int", _int(1, 5120), "Все части, кроме последней (S3: 5)."),
]
BY_KEY = {f.key: f for f in FIELDS}


def mask_secret(value: str) -> str:
    """Токен никогда не показывается целиком: только точки и последние 4 символа."""
    if not value:
        return ""
    return "•" * 8 + (value[-4:] if len(value) >= 12 else "")


def to_env(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def from_env(field: Field, text: str) -> Any:
    """Типизированное значение из .env; мусор в файле не должен ронять страницу."""
    try:
        if field.kind == "bool":
            return text.strip().lower() in {"1", "true", "yes"}
        if field.kind == "int":
            return int(text.strip())
    except ValueError:
        return text
    return text.strip()
