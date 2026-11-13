"""Ошибки Storage Core. Ошибки backend-ов — в backends/base.py."""

from __future__ import annotations

from polaris.shared.exceptions import PolarisError


class StorageError(PolarisError):
    """Базовая ошибка подсистемы хранения."""


class StorageUnavailable(StorageError):
    """Storage выключен, не настроен или не смог стартовать."""


class StorageBusy(StorageError):
    """Достигнут лимит параллельных операций или БД занята слишком долго."""


class InvalidName(StorageError):
    """Недопустимое имя bucket или ключ объекта."""


class BucketNotFound(StorageError):
    pass


class ObjectNotFound(StorageError):
    pass


class ObjectNotReady(StorageError):
    """Объект существует, но не в статусе ready (идёт загрузка, ошибка, удаление)."""


class ObjectConflict(StorageError):
    """Операция конфликтует с текущим состоянием объекта (в т.ч. параллельной операцией)."""


class IntegrityError(StorageError):
    """Данные не прошли проверку размера или SHA-256."""


class ChecksumMismatch(IntegrityError):
    """Фактический SHA-256/размер не совпал с ожидаемым, который указал вызывающий."""


class MigrationError(StorageError):
    pass
