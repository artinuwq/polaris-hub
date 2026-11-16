"""Запуск Storage внутри Polaris: конфигурация -> БД -> драйвер -> ядро, фоновая сверка.

Storage не запускается отдельным процессом: сервис живёт в процессе веб-приложения.
Ошибка конфигурации или недоступность Telegram никогда не мешают стартовать самому Polaris —
Storage просто остаётся в состоянии unavailable с понятной причиной.
"""

from __future__ import annotations

import logging
import threading

from polaris.storage.access_keys import AccessKeyStore, MasterKey
from polaris.storage.backends.base import BackendDriver
from polaris.storage.backends.telegram import TelegramConfig, TelegramDriver
from polaris.storage.config import StorageConfig
from polaris.storage.core import StorageCore
from polaris.storage.database import utcnow_iso
from polaris.storage.database import StorageDatabase
from polaris.storage.errors import StorageUnavailable
from polaris.storage.recovery import Reconciler, ReconcileReport

logger = logging.getLogger(__name__)

DISABLED, READY, UNAVAILABLE = "disabled", "ready", "unavailable"


class StorageService:
    def __init__(self, config: StorageConfig) -> None:
        self.config = config
        self.state = DISABLED
        self.reasons: list[str] = []
        self._db: StorageDatabase | None = None
        self._core: StorageCore | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_report: ReconcileReport | None = None
        self.last_report_at: str | None = None
        self.access_keys: AccessKeyStore | None = None
        self.access_keys_error: str = ""

    def start(self) -> None:
        cfg = self.config
        if not cfg.enabled:
            self.state = DISABLED
            logger.info("Storage выключен (STORAGE_ENABLED=false)")
            return
        errors = cfg.validate()
        if errors:
            self._unavailable("неверная конфигурация Storage: " + "; ".join(errors))
            return
        problems = cfg.backend_problems()
        if problems:
            self._unavailable("Telegram backend недоступен: " + ", ".join(problems))
            return
        try:
            self._db = StorageDatabase(cfg.db_path, busy_timeout_ms=cfg.busy_timeout_ms)
            self._db.migrate()
            self._core = StorageCore(
                self._db,
                self._build_driver(),
                chunk_size=cfg.chunk_size,
                max_concurrent_uploads=cfg.max_concurrent_uploads,
                max_concurrent_downloads=cfg.max_concurrent_downloads,
                delete_max_attempts=cfg.delete_max_attempts,
                stale_upload_seconds=cfg.stale_upload_seconds,
            )
            self._core.multipart.min_part_size = cfg.multipart_min_part_size
            self._init_access_keys()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Storage: запуск не удался")
            if self._db:
                self._db.close()
            self._db = self._core = None
            self._unavailable(f"запуск Storage не удался: {type(exc).__name__}: {exc}")
            return
        self.state, self.reasons = READY, []
        logger.info("Storage готов: база %s, backend %s, чанк %d MiB", cfg.db_path, cfg.backend,
                    cfg.chunk_size // (1024 * 1024))
        self._thread = threading.Thread(target=self._maintenance, name="storage-reconcile", daemon=True)
        self._thread.start()

    def _init_access_keys(self) -> None:
        """Access Management необязателен для Storage: проблема с мастер-ключом не должна выключать хранилище."""
        try:
            master = MasterKey.load(self.config.master_key_path, self.config.master_key)
            self.access_keys = AccessKeyStore(self._db, master)  # type: ignore[arg-type]
            self.access_keys_error = ""
        except Exception as exc:  # noqa: BLE001
            self.access_keys = None
            self.access_keys_error = f"{type(exc).__name__}: {exc}"
            logger.error("Storage: Access Management недоступен: %s", self.access_keys_error)

    def _build_driver(self) -> BackendDriver:
        cfg = self.config
        return TelegramDriver(
            TelegramConfig(
                token=cfg.telegram_bot_token,
                chat_id=cfg.telegram_chat_id,
                api_base=cfg.telegram_api_base,
                proxy_url=cfg.telegram_proxy_url,
                force_ipv4=cfg.force_ipv4,
                connect_timeout=cfg.connect_timeout,
                read_timeout=cfg.read_timeout,
                max_retries=cfg.max_retries,
                retry_delay=cfg.retry_delay,
                max_retry_after=cfg.max_retry_after,
            )
        )

    def _unavailable(self, reason: str) -> None:
        self.state = UNAVAILABLE
        self.reasons = [reason]
        logger.error("Storage недоступен: %s", reason)

    def _maintenance(self) -> None:
        while not self._stop.is_set():
            try:
                self.last_report = self.reconcile()
                self.last_report_at = utcnow_iso()
                logger.info("Storage reconcile: %s", self.last_report.summary())
            except Exception:  # noqa: BLE001
                logger.exception("Storage reconcile: ошибка")
            if self.config.maintenance_interval <= 0:
                return
            self._stop.wait(self.config.maintenance_interval)

    def reconcile(self, **kwargs) -> ReconcileReport:
        core = self.get_core()
        if self.config.multipart_expiry_seconds > 0:
            expired = core.multipart.abort_expired(self.config.multipart_expiry_seconds)
            if expired:
                logger.info("Storage: отменено просроченных multipart-загрузок: %d", expired)
        return Reconciler(core).run(**kwargs)

    def get_core(self) -> StorageCore:
        if self.state != READY or self._core is None:
            reason = "; ".join(self.reasons) or ("Storage выключен" if self.state == DISABLED else "Storage не готов")
            raise StorageUnavailable(reason)
        return self._core

    def status(self) -> dict:
        """Состояние без секретов (токен, chat id) — для диагностики и будущего UI."""
        info = {"state": self.state, "reasons": list(self.reasons), "backend": self.config.backend}
        if self.state == READY and self._db is not None:
            info["db_path"] = str(self.config.db_path)
            info["schema_version"] = self._db.schema_version()
        return info

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)
        if self._db:
            self._db.close()
        self._db = self._core = None
        self.access_keys = None
        self.state = DISABLED


_service: StorageService | None = None


def init_storage(settings) -> StorageService:
    """Создать и запустить Storage по Settings. Никогда не бросает исключений."""
    global _service
    if _service is not None:
        _service.stop()
    service = StorageService(StorageConfig.from_settings(settings))
    try:
        service.start()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Storage: непредвиденная ошибка запуска")
        service._unavailable(f"{type(exc).__name__}: {exc}")
    _service = service
    return service


def get_storage_service() -> StorageService | None:
    return _service


def shutdown_storage() -> None:
    global _service
    if _service is not None:
        _service.stop()
        _service = None
