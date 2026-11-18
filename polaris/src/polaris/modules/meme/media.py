"""Проверка загружаемых изображений без внешних зависимостей: тип по сигнатуре (а не по заявленному
Content-Type / расширению), размеры, целостность «хвоста» и ограничения Telegram.

Поддерживаются JPEG, PNG, WebP (статический) и GIF. Анимированный WebP Telegram не принимает как
анимацию, поэтому он отклоняется с понятным сообщением.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

# Лимиты Bot API для sendPhoto: файл до 10 МБ, width + height <= 10000, соотношение сторон не больше 20:1.
MAX_BYTES = 10 * 1024 * 1024
MAX_SIDES_SUM = 10_000
MAX_RATIO = 20


class InvalidImage(ValueError):
    """Файл не является корректным поддерживаемым изображением (текст — для пользователя)."""


@dataclass(frozen=True)
class ImageInfo:
    mime: str
    ext: str
    width: int
    height: int
    animated: bool = False  # только GIF: несколько кадров не определяем — GIF всегда отправляем как animation

    @property
    def is_gif(self) -> bool:
        return self.mime == "image/gif"

    @property
    def is_jpeg(self) -> bool:
        return self.mime == "image/jpeg"


def _png(data: bytes) -> ImageInfo:
    if len(data) < 33 or data[12:16] != b"IHDR":
        raise InvalidImage("Повреждённый PNG: нет заголовка")
    width, height = struct.unpack(">II", data[16:24])
    if not data[-32:].rstrip(b"\x00").endswith(b"IEND\xaeB`\x82"):
        raise InvalidImage("Повреждённый PNG: файл обрезан")
    return ImageInfo("image/png", ".png", width, height)


def _gif(data: bytes) -> ImageInfo:
    if len(data) < 14:
        raise InvalidImage("Повреждённый GIF: файл обрезан")
    width, height = struct.unpack("<HH", data[6:10])
    if not data[-32:].rstrip(b"\x00").endswith(b";"):
        raise InvalidImage("Повреждённый GIF: файл обрезан")
    return ImageInfo("image/gif", ".gif", width, height, animated=True)


_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _jpeg(data: bytes) -> ImageInfo:
    pos, size = 2, len(data)
    while pos + 4 <= size:
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        if marker == 0xFF:  # заполнитель
            pos += 1
            continue
        if marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:  # маркеры без длины
            pos += 2
            continue
        length = struct.unpack(">H", data[pos + 2:pos + 4])[0]
        if marker in _SOF:
            if pos + 9 > size:
                break
            height, width = struct.unpack(">HH", data[pos + 5:pos + 9])
            if not data[-32:].rstrip(b"\x00").endswith(b"\xff\xd9"):
                raise InvalidImage("Повреждённый JPEG: файл обрезан")
            return ImageInfo("image/jpeg", ".jpg", width, height)
        pos += 2 + length
    raise InvalidImage("Повреждённый JPEG: не найден размер изображения")


def _webp(data: bytes) -> ImageInfo:
    if len(data) < 30:
        raise InvalidImage("Повреждённый WebP: файл обрезан")
    declared = struct.unpack("<I", data[4:8])[0] + 8
    if declared > len(data):
        raise InvalidImage("Повреждённый WebP: файл обрезан")
    chunk = data[12:16]
    if chunk == b"VP8X":
        if data[20] & 0x02:
            raise InvalidImage("Анимированный WebP не поддерживается — загрузите GIF или статичное изображение")
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
    elif chunk == b"VP8L":
        if data[20] != 0x2F:
            raise InvalidImage("Повреждённый WebP")
        bits = int.from_bytes(data[21:25], "little")
        width, height = (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    elif chunk == b"VP8 ":
        if data[23:26] != b"\x9d\x01\x2a":
            raise InvalidImage("Повреждённый WebP")
        width, height = struct.unpack("<HH", data[26:30])
        width, height = width & 0x3FFF, height & 0x3FFF
    else:
        raise InvalidImage("Неподдерживаемый вариант WebP")
    return ImageInfo("image/webp", ".webp", width, height)


def inspect_image(data: bytes | bytearray) -> ImageInfo:
    """Определить тип и размеры по самим байтам; бросает InvalidImage с пользовательским текстом."""
    data = bytes(data)
    if not data:
        raise InvalidImage("Файл пустой")
    if len(data) > MAX_BYTES:
        raise InvalidImage(f"Файл больше {MAX_BYTES // (1024 * 1024)} МиБ — это предел Telegram для изображений")
    if data[:3] == b"\xff\xd8\xff":
        info = _jpeg(data)
    elif data[:8] == b"\x89PNG\r\n\x1a\n":
        info = _png(data)
    elif data[:6] in (b"GIF87a", b"GIF89a"):
        info = _gif(data)
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        info = _webp(data)
    else:
        raise InvalidImage("Неподдерживаемый формат: нужен JPEG, PNG, WebP или GIF")
    if info.width < 1 or info.height < 1:
        raise InvalidImage("Некорректные размеры изображения")
    if info.width + info.height > MAX_SIDES_SUM:
        raise InvalidImage(f"Сумма ширины и высоты больше {MAX_SIDES_SUM} px — Telegram такое не принимает")
    if max(info.width, info.height) > MAX_RATIO * min(info.width, info.height):
        raise InvalidImage(f"Соотношение сторон больше {MAX_RATIO}:1 — Telegram такое не принимает")
    return info
