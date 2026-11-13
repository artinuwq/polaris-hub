"""Интерфейс Backend Driver.

Storage Core работает с физическим хранилищем только через этот интерфейс.
`reference` — непрозрачная для ядра строка: её формат известен только драйверу
(для Telegram это JSON с chat_id/message_id/file_id).

Единица обмена с драйвером — один чанк (ограничен `max_chunk_size`), поэтому
память под операцию всегда ограничена размером чанка.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from polaris.storage.errors import StorageError


class BackendError(StorageError):
    """Ошибка backend-а.

    uncertain_attempts — сколько попыток отправки закончились неопределённо
    (сообщение могло быть сохранено, но ответ не получен). Ядро регистрирует
    их как возможные «осиротевшие» объекты.
    """

    def __init__(self, message: str, *, uncertain_attempts: int = 0) -> None:
        super().__init__(message)
        self.uncertain_attempts = uncertain_attempts


class TransientBackendError(BackendError):
    """Временный сбой (сеть, 429, 5xx): повтор позже имеет смысл."""


class PermanentBackendError(BackendError):
    """Повтор не поможет (неверная настройка, нет прав, плохой запрос)."""

    def __init__(self, message: str, *, status: int = 0, uncertain_attempts: int = 0) -> None:
        super().__init__(message, uncertain_attempts=uncertain_attempts)
        self.status = status


class BackendNotFound(PermanentBackendError):
    """Физическое содержимое по ссылке недоступно."""


class DeleteRejected(PermanentBackendError):
    """Backend отказался удалять (например, Telegram: message can't be deleted)."""


@dataclass(frozen=True)
class UploadContext:
    blob_id: str
    chunk_index: int
    sha256: str
    size: int

    @property
    def marker(self) -> str:
        """Метка, которую драйвер кладёт в сохранённые данные для ручной идентификации."""
        return make_marker(self.blob_id, self.chunk_index, self.sha256)


def make_marker(blob_id: str, chunk_index: int, sha256: str) -> str:
    return f"polaris-storage:v1 blob={blob_id} chunk={chunk_index} sha256={sha256[:16]}"


@dataclass(frozen=True)
class UploadResult:
    reference: str
    uncertain_attempts: int = 0


@dataclass(frozen=True)
class BackendMetadata:
    size_bytes: int | None


@dataclass(frozen=True)
class DeleteResult:
    already_absent: bool = False


@dataclass(frozen=True)
class HealthStatus:
    ok: bool
    message: str = ""
    details: dict = field(default_factory=dict)


class BackendDriver(ABC):
    name: str

    @property
    @abstractmethod
    def max_chunk_size(self) -> int:
        """Максимальный размер одного чанка, который backend надёжно и записывает, и отдаёт."""

    @abstractmethod
    def upload(self, data: bytes, ctx: UploadContext) -> UploadResult:
        """Сохранить один чанк. Ошибки — BackendError (с uncertain_attempts)."""

    @abstractmethod
    def download(self, reference: str) -> bytes | bytearray:
        """Вернуть чанк целиком. Не найден — BackendNotFound."""

    @abstractmethod
    def delete(self, reference: str) -> DeleteResult:
        """Физически удалить чанк. Отказ — DeleteRejected; временный сбой — TransientBackendError."""

    @abstractmethod
    def get_metadata(self, reference: str) -> BackendMetadata:
        """Проверить существование чанка и узнать размер без скачивания."""

    @abstractmethod
    def health_check(self) -> HealthStatus:
        """Быстрая проверка доступности (без исключений наружу)."""
