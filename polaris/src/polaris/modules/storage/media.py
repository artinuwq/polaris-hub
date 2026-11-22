"""Воспроизведение видео/аудио из Storage в плеере браузера: разбор Range и определение типа.

Плеер (`<video>`) не скачивает файл целиком, а просит нужные куски по смещению (HTTP Range) — так работают и
перемотка, и «досмотр во время докачки». Эта часть решает, какой кусок отдать; чтение кусков и кэш — в StorageCore.
"""
from __future__ import annotations

import re

MEDIA_PREFIX = "media:"   # пространство имён токена: токен плеера не открывает /download и наоборот
MEDIA_TTL = 3 * 3600      # плеер обращается к ссылке при каждой перемотке, 5 минут обычной ссылки не хватило бы

# что умеет воспроизводить встроенный плеер браузера; остальное (mkv, avi…) — только скачать
_BY_EXT = {
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm", ".ogv": "video/ogg",
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac", ".ogg": "audio/ogg", ".oga": "audio/ogg",
    ".opus": "audio/ogg", ".wav": "audio/wav", ".flac": "audio/flac",
}
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


class RangeNotSatisfiable(Exception):
    """Запрошенный диапазон целиком за пределами файла (HTTP 416)."""


def media_mime(name: str, stored: str | None) -> str | None:
    """Тип для плеера или None, если файл не аудио/видео. Отдавать inline можно ТОЛЬКО такие типы:
    иначе загруженный html/svg выполнялся бы на нашем домене."""
    stored = (stored or "").split(";")[0].strip().lower()
    if stored.startswith(("video/", "audio/")):
        return stored
    ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return _BY_EXT.get(ext)


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Один диапазон `bytes=a-b` / `a-` / `-n` → (start, end) включительно.

    None — заголовка нет или он не про один диапазон байт: отдаём файл целиком (так поступает и S3).
    RangeNotSatisfiable — диапазон вне файла."""
    if not header or size <= 0:
        return None
    match = _RANGE_RE.match(header.strip().lower())
    if not match:
        return None
    first, last = match.groups()
    if not first and not last:
        return None
    if not first:  # суффикс: последние n байт
        n = int(last)
        if n == 0:
            raise RangeNotSatisfiable
        return max(0, size - n), size - 1
    a = int(first)
    if a >= size:
        raise RangeNotSatisfiable
    b = size - 1 if not last else min(int(last), size - 1)
    if b < a:
        return None  # некорректный диапазон игнорируется, как в RFC 9110
    return a, b
