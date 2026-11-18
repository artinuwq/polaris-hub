"""Общие средства тестов Polaris Meme: генераторы изображений без Pillow, БД Polaris во временном каталоге,
реальный StorageCore с MemoryDriver (чанки по 64 байта: любая картинка режется на много частей)."""
from __future__ import annotations

import importlib
import struct
import sys
import types
import zlib
from unittest import mock

from polaris.infra import database, migrations
from polaris.infra.settings import Settings
from polaris.modules.meme.service import MemeService
from tests import fastapi_stub
from tests.storage_helpers import StorageTestCase

BOT_TOKEN = "123456:TEST-BOT-TOKEN"


def png(width: int = 40, height: int = 30, seed: int = 0) -> bytes:
    rows = b"".join(
        b"\x00" + b"".join(bytes(((seed + x * 7 + y * 3) % 256, (x * 5) % 256, (y * 11) % 256)) for x in range(width))
        for y in range(height)
    )

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def jpeg(width: int = 64, height: int = 48, seed: int = 0) -> bytes:
    """Структурно корректный по маркерам JPEG (для проверки разбора; не для декодирования)."""
    comment = bytes((seed + i) % 256 for i in range(300))
    return (b"\xff\xd8" + b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            + b"\xff\xfe" + struct.pack(">H", len(comment) + 2) + comment
            + b"\xff\xc0\x00\x11\x08" + struct.pack(">HH", height, width) + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01"
            + b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00" + bytes((seed + i) % 251 for i in range(200)) + b"\xff\xd9")


def gif(width: int = 20, height: int = 20, seed: int = 0) -> bytes:
    note = bytes((seed + i) % 256 for i in range(50))
    return (b"GIF89a" + struct.pack("<HH", width, height) + b"\x00\x00\x00"
            + b"\x21\xfe" + bytes([len(note)]) + note + b"\x00" + b";")


def webp(width: int = 32, height: int = 16, seed: int = 0, animated: bool = False) -> bytes:
    flags = 0x02 if animated else 0x00
    vp8x = b"VP8X" + struct.pack("<I", 10) + bytes([flags, 0, 0, 0]) + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    extra = b"EXIF" + struct.pack("<I", 40) + bytes((seed + i) % 256 for i in range(40))
    body = b"WEBP" + vp8x + extra
    return b"RIFF" + struct.pack("<I", len(body)) + body


def load_meme_api():
    """polaris.modules.meme.api поверх заглушки FastAPI (как load_stubbed для Storage). Возвращает (auth, api)."""
    names = ("fastapi", "fastapi.responses", "fastapi.concurrency", "polaris.shared.auth", "polaris.modules.meme.api")
    saved = {n: sys.modules.get(n) for n in names}
    sys.modules["fastapi"], sys.modules["fastapi.responses"], sys.modules["fastapi.concurrency"] = fastapi_stub._stub_modules()
    for n in names[3:]:
        sys.modules.pop(n, None)
    try:
        auth = importlib.import_module("polaris.shared.auth")
        api = importlib.import_module("polaris.modules.meme.api")
    finally:
        for n, module in saved.items():
            if module is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = module
    return auth, api


class MemeTestCase(StorageTestCase):
    """polaris.db во временном каталоге + настоящее ядро Storage на MemoryDriver."""

    chunk_size = 64

    def setUp(self) -> None:
        super().setUp()
        for patch in (mock.patch.object(database, "DB_PATH", self.tmp / "data" / "polaris.db"),
                      mock.patch.object(database, "_wal_ready", set())):
            patch.start()
            self.addCleanup(patch.stop)
        migrations.migrate()
        self.settings = Settings(telegram_bot_token=BOT_TOKEN, telegram_admin_ids=[1], webapp_url="https://hub.example.com")
        self.storage = types.SimpleNamespace(get_core=lambda: self.core)
        self.push_calls: list = []
        self.push_result = None  # (file_id, kind) | None | исключение — что «ответит Telegram» при добавлении
        self.service = MemeService(self.settings, get_service=lambda: self.storage, telegram_push=self._push)

    def _push(self, settings, row, data):
        self.push_calls.append((row["id"], bytes(data)))
        if isinstance(self.push_result, Exception):
            raise self.push_result
        return self.push_result

    def stored_objects(self) -> list:
        from polaris.modules.meme.service import BUCKET
        from polaris.storage.errors import BucketNotFound

        try:
            return self.core.list_objects(BUCKET)
        except BucketNotFound:  # bucket создаётся при первой успешной загрузке
            return []
