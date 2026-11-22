"""Небольшой кэш проверенных чанков для чтения диапазонов (воспроизведение видео, Range в S3 API).

Плеер читает файл мелкими запросами (десятки килобайт – единицы мегабайт), а чанк — это целое сообщение Telegram
(до ~15 МиБ), которое скачивается и проверяется целиком. Без кэша каждый такой запрос заново качал бы чанк.

- LRU по суммарному размеру (`max_bytes`; 0 — кэш выключен). В кэш попадают только чанки, прошедшие проверку
  размера и SHA-256, поэтому повреждённые данные не раздаются повторно.
- «Один запрос на чанк»: если несколько запросов одновременно просят один и тот же чанк, он качается один раз,
  остальные ждут результат (и получают ту же ошибку, если загрузка не удалась).
- Упреждающая подкачка следующего чанка (best effort): пока плеер доигрывает текущий, следующий уже в памяти.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Callable


class _Flight:
    __slots__ = ("event", "data", "error")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.data: bytes | None = None
        self.error: BaseException | None = None


class ChunkCache:
    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max(0, int(max_bytes))
        self._lock = threading.Lock()
        self._items: "OrderedDict[str, bytes]" = OrderedDict()
        self._size = 0
        self._flights: dict[str, _Flight] = {}

    @property
    def enabled(self) -> bool:
        return self.max_bytes > 0

    @property
    def size(self) -> int:
        return self._size

    def has(self, key: str) -> bool:
        with self._lock:
            return key in self._items or key in self._flights

    def _store(self, key: str, data: bytes) -> None:
        # хотя бы один чанк держим, даже если он больше лимита; остальное вытесняем с «холодного» конца
        self._items[key] = data
        self._items.move_to_end(key)
        self._size += len(data)
        while self._size > self.max_bytes and len(self._items) > 1:
            _, old = self._items.popitem(last=False)
            self._size -= len(old)

    def fetch(self, key: str, loader: Callable[[], "bytes | bytearray"]) -> bytes:
        """Данные чанка из кэша или через loader (один параллельный вызов loader на ключ)."""
        if not self.enabled:
            return bytes(loader())
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
            flight = self._flights.get(key)
            leader = flight is None
            if leader:
                flight = self._flights[key] = _Flight()
        if not leader:
            flight.event.wait()
            if flight.error is not None:
                raise flight.error
            return flight.data  # type: ignore[return-value]
        try:
            flight.data = bytes(loader())
        except BaseException as exc:  # noqa: BLE001 — ту же ошибку получат все ожидающие
            flight.error = exc
            raise
        else:
            with self._lock:
                self._store(key, flight.data)
            return flight.data
        finally:
            with self._lock:
                self._flights.pop(key, None)
            flight.event.set()

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._size = 0
