"""Конфигурация Storage: строится из общего Settings Polaris."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

MIB = 1024 * 1024
DEFAULT_CHUNK_SIZE = 15 * MIB
MAX_CHUNK_SIZE = 19 * MIB  # getFile в стандартном Bot API отдаёт максимум 20 MB

# корень проекта (polaris/): здесь же лежит database/data, переживающий /update
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH = PROJECT_ROOT / "database" / "data" / "storage.db"
DEFAULT_MASTER_KEY_PATH = PROJECT_ROOT / "database" / "data" / "storage_master.key"


@dataclass(frozen=True)
class StorageConfig:
    enabled: bool = False
    db_path: Path = DEFAULT_DB_PATH
    backend: str = "telegram"

    telegram_bot_token: str = field(default="", repr=False)
    telegram_chat_id: str = ""
    telegram_api_base: str = "https://api.telegram.org"
    telegram_proxy_url: str = field(default="", repr=False)
    force_ipv4: bool = True

    chunk_size: int = DEFAULT_CHUNK_SIZE
    connect_timeout: float = 10.0
    read_timeout: float = 120.0
    max_retries: int = 3
    retry_delay: float = 2.0
    max_retry_after: float = 120.0

    max_concurrent_uploads: int = 2
    max_concurrent_downloads: int = 4
    busy_timeout_ms: int = 10_000
    stale_upload_seconds: int = 1800
    delete_max_attempts: int = 10
    maintenance_interval: int = 600  # секунд; 0 — только разовая сверка при старте
    master_key: str = field(default="", repr=False)
    master_key_path: Path = DEFAULT_MASTER_KEY_PATH
    multipart_min_part_size: int = 5 * MIB
    multipart_expiry_seconds: int = 0  # 0 — незавершённые multipart-загрузки не истекают

    def validate(self) -> list[str]:
        """Ошибки, из-за которых Storage не должен стартовать. Пустой список — всё в порядке."""
        errors: list[str] = []
        if self.backend != "telegram":
            errors.append(f"STORAGE_BACKEND: неизвестный backend {self.backend!r} (доступен: telegram)")
        if not 0 < self.chunk_size <= MAX_CHUNK_SIZE:
            errors.append(
                f"размер чанка должен быть от 1 байта до {MAX_CHUNK_SIZE // MIB} MiB "
                f"(лимит скачивания Bot API 20 MB), получено {self.chunk_size}"
            )
        if self.connect_timeout <= 0 or self.read_timeout <= 0:
            errors.append("таймауты должны быть положительными")
        if self.max_retries < 0 or self.max_retries > 20:
            errors.append("число повторов должно быть от 0 до 20")
        if self.retry_delay < 0:
            errors.append("задержка между повторами не может быть отрицательной")
        if self.max_concurrent_uploads < 1 or self.max_concurrent_downloads < 1:
            errors.append("лимиты параллельных операций должны быть >= 1")
        if self.busy_timeout_ms < 0 or self.stale_upload_seconds < 60 or self.delete_max_attempts < 1:
            errors.append("busy_timeout/stale_upload_seconds(>=60)/delete_max_attempts(>=1) заданы неверно")
        if self.multipart_min_part_size < 1 or self.multipart_expiry_seconds < 0:
            errors.append("multipart: минимальный размер части >= 1, срок жизни >= 0")
        return errors

    def backend_problems(self) -> list[str]:
        """Что мешает работе backend-а (не настроен токен/чат). Не приводит к падению Polaris."""
        problems = []
        if self.backend == "telegram":
            if not self.telegram_bot_token:
                problems.append("не задан TELEGRAM_BOT_TOKEN")
            if not self.telegram_chat_id:
                problems.append("не задан CLOUD_STORAGE_CHAT_ID")
        return problems

    @classmethod
    def from_settings(cls, settings) -> "StorageConfig":
        raw_path = (settings.storage_db_path or "").strip()
        db_path = Path(raw_path).expanduser() if raw_path else DEFAULT_DB_PATH
        if not db_path.is_absolute():
            db_path = PROJECT_ROOT / db_path
        key_path = Path(settings.storage_master_key_path).expanduser() if settings.storage_master_key_path \
            else DEFAULT_MASTER_KEY_PATH
        if not key_path.is_absolute():
            key_path = PROJECT_ROOT / key_path
        return cls(
            enabled=settings.storage_enabled,
            master_key=settings.storage_master_key,
            master_key_path=key_path,
            db_path=db_path,
            telegram_bot_token=settings.storage_telegram_bot_token or settings.telegram_bot_token,
            telegram_chat_id=settings.cloud_storage_chat_id,
            telegram_api_base=settings.telegram_api_base,
            telegram_proxy_url=settings.telegram_proxy_url,
            force_ipv4=settings.telegram_force_ipv4,
            chunk_size=int(settings.storage_chunk_size_mib * MIB),
            connect_timeout=settings.storage_connect_timeout,
            read_timeout=settings.storage_read_timeout,
            max_retries=settings.storage_max_retries,
            retry_delay=settings.storage_retry_delay,
            max_concurrent_uploads=settings.storage_max_concurrent_uploads,
            max_concurrent_downloads=settings.storage_max_concurrent_downloads,
            stale_upload_seconds=settings.storage_stale_upload_seconds,
            maintenance_interval=settings.storage_maintenance_interval,
            multipart_min_part_size=int(settings.s3_min_part_size_mib * MIB),
            multipart_expiry_seconds=int(settings.s3_multipart_expiry_hours * 3600),
        )
