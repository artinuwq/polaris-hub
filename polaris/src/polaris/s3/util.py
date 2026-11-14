"""Небольшие HTTP/S3-утилиты без внешних зависимостей."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from urllib.parse import quote, unquote, unquote_plus

from polaris.s3 import errors

_UNRESERVED = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~"
_XML_INVALID = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff\ud800-\udfff]")
_IPV4 = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def aws_quote(value: str, *, keep_slash: bool = False) -> str:
    """URI-кодирование по правилам AWS (RFC 3986 unreserved, прописные %XX)."""
    return quote(value, safe=_UNRESERVED + ("/" if keep_slash else ""))


def decode_path(raw_path: str) -> str:
    """Процентное декодирование пути (UTF-8, '+' остаётся плюсом)."""
    try:
        return unquote(raw_path, encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        raise errors.InvalidURI("путь не является корректным UTF-8") from None


def parse_query(raw_query: str) -> list[tuple[str, str]]:
    """Параметры запроса в порядке следования; имя и значение декодированы."""
    pairs = []
    if not raw_query:
        return pairs
    for part in raw_query.split("&"):
        if not part:
            continue
        name, _, value = part.partition("=")
        try:
            pairs.append((unquote_plus(name, errors="strict"), unquote_plus(value, errors="strict")))
        except UnicodeDecodeError:
            raise errors.InvalidURI("параметры запроса не являются корректным UTF-8") from None
    return pairs


def split_bucket_key(path: str) -> tuple[str, str | None]:
    """'/bucket/some/key' -> ('bucket', 'some/key'); '/' -> ('', None); '/bucket' и '/bucket/' -> ('bucket', None)."""
    rest = path.lstrip("/") if path.startswith("/") else path
    if not rest:
        return "", None
    bucket, slash, key = rest.partition("/")
    return bucket, (key if slash and key else None)


def validate_bucket_name(name: str) -> None:
    ok = (3 <= len(name) <= 63 and re.fullmatch(r"[a-z0-9][a-z0-9.-]*[a-z0-9]", name) is not None
          and ".." not in name and ".-" not in name and "-." not in name and not _IPV4.match(name))
    if not ok:
        raise errors.InvalidBucketName("имя bucket не соответствует правилам S3")


def validate_key(key: str) -> None:
    if _XML_INVALID.search(key):  # включает одиночные суррогаты, которые нельзя закодировать в UTF-8
        raise errors.InvalidArgument("ключ содержит управляющие символы, которые этот сервер не поддерживает")
    if len(key.encode("utf-8")) > 1024:
        raise errors.KeyTooLongError()


def url_encode_for_listing(value: str) -> str:
    """encoding-type=url: проценты, пробел как %20 (SDK декодирует unquote-ом)."""
    return quote(value, safe="/")


def iso8601(ts: str | None) -> str:
    """'2026-11-14T10:20:30.123456Z' -> '2026-11-14T10:20:30.123Z'."""
    if not ts:
        return "1970-01-01T00:00:00.000Z"
    return parse_ts(ts).strftime("%Y-%m-%dT%H:%M:%S.") + f"{parse_ts(ts).microsecond // 1000:03d}Z"


def parse_ts(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def http_date(ts: str | None) -> str:
    return format_datetime(parse_ts(ts) if ts else datetime.fromtimestamp(0, timezone.utc), usegmt=True)


def parse_http_date(value: str) -> datetime | None:
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def quote_etag(etag: str | None) -> str:
    return f'"{etag or ""}"'


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Разбор Range для одного диапазона. None — заголовок игнорируется (как в S3: некорректный синтаксис,
    не 'bytes', несколько диапазонов). Бросает InvalidRange, если диапазон не удовлетворим."""
    if not header:
        return None
    m = re.fullmatch(r"\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*", header)
    if not m or (m.group(1) == "" and m.group(2) == ""):
        return None
    first, last = m.group(1), m.group(2)
    if first == "":  # суффикс: последние N байт
        n = int(last)
        if n == 0 or size == 0:
            raise errors.InvalidRange()
        return max(0, size - n), size - 1
    start = int(first)
    end = int(last) if last != "" else size - 1
    if last != "" and end < start:
        return None  # некорректный диапазон игнорируется
    if start >= size:
        raise errors.InvalidRange()
    return start, min(end, size - 1)
