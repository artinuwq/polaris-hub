from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


def _split_ids(raw: str) -> list[int]:
    result: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        result.append(int(part))
    return result


def _env_number(name: str, default, cast):
    """Число из окружения; мусор не должен ронять весь Polaris — берётся значение по умолчанию."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return cast(raw)
    except ValueError:
        logger.warning("%s=%r не число, используется %s", name, raw, default)
        return default


@dataclass
class Settings:
    app_name: str = "polaris"
    debug: bool = True
    database_url: str = "sqlite:///database/data/polaris.db"
    telegram_bot_token: str = ""
    telegram_admin_ids: list[int] = field(default_factory=list)
    update_branch: str = "main"
    update_remote: str = "origin"
    update_repo_dir: str = ""
    update_service_name: str = ""
    update_api_token: str = ""
    api_token: str = ""
    web_host: str = "127.0.0.1"
    web_port: int = 8000
    telegram_force_ipv4: bool = True
    telegram_init_data_max_age: int = 86400
    webapp_url: str = ""
    miniapp_name: str = "app"
    notify_timezone: str = "Europe/Helsinki"
    mail_body_style: str = "details"
    telegram_proxy_url: str = ""
    telegram_api_base: str = "https://api.telegram.org"
    # Storage (см. docs/storage.md)
    storage_enabled: bool = False
    storage_db_path: str = ""
    cloud_storage_chat_id: str = ""
    storage_chunk_size_mib: int = 15
    storage_connect_timeout: float = 10.0
    storage_read_timeout: float = 120.0
    storage_max_retries: int = 3
    storage_retry_delay: float = 2.0
    storage_max_concurrent_uploads: int = 2
    storage_max_concurrent_downloads: int = 4
    storage_stale_upload_seconds: int = 1800
    storage_maintenance_interval: int = 600

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            app_name=os.getenv("APP_NAME", cls.app_name),
            debug=os.getenv("DEBUG", "true").lower() == "true",
            database_url=os.getenv("DATABASE_URL", cls.database_url),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_admin_ids=_split_ids(os.getenv("TELEGRAM_ADMIN_IDS", "")),
            update_branch=os.getenv("UPDATE_BRANCH", "main"),
            update_remote=os.getenv("UPDATE_REMOTE", "origin"),
            update_repo_dir=os.getenv("UPDATE_REPO_DIR", ""),
            update_service_name=os.getenv("UPDATE_SERVICE_NAME", ""),
            update_api_token=os.getenv("UPDATE_API_TOKEN", ""),
            api_token=os.getenv("API_TOKEN", ""),
            web_host=os.getenv("WEB_HOST", "127.0.0.1"),
            web_port=int(os.getenv("WEB_PORT", "8000")),
            telegram_force_ipv4=os.getenv("TELEGRAM_FORCE_IPV4", "true").lower()
            in {"1", "true", "yes"},
            telegram_init_data_max_age=int(os.getenv("TELEGRAM_INIT_DATA_MAX_AGE", "86400")),
            webapp_url=os.getenv("WEBAPP_URL", "").strip(),
            miniapp_name=os.getenv("MINIAPP_NAME", "app").strip() or "app",
            notify_timezone=os.getenv("NOTIFY_TIMEZONE", "Europe/Helsinki").strip() or "Europe/Helsinki",
            mail_body_style=os.getenv("MAIL_BODY_STYLE", "details").strip().lower() or "details",
            telegram_proxy_url=os.getenv("TELEGRAM_PROXY_URL", "").strip(),
            telegram_api_base=os.getenv("TELEGRAM_API_BASE", "").strip() or cls.telegram_api_base,
            storage_enabled=os.getenv("STORAGE_ENABLED", "false").strip().lower() in {"1", "true", "yes"},
            storage_db_path=os.getenv("STORAGE_DB_PATH", "").strip(),
            cloud_storage_chat_id=os.getenv("CLOUD_STORAGE_CHAT_ID", "").strip(),
            storage_chunk_size_mib=_env_number("STORAGE_CHUNK_SIZE_MIB", 15, int),
            storage_connect_timeout=_env_number("STORAGE_CONNECT_TIMEOUT", 10.0, float),
            storage_read_timeout=_env_number("STORAGE_READ_TIMEOUT", 120.0, float),
            storage_max_retries=_env_number("STORAGE_MAX_RETRIES", 3, int),
            storage_retry_delay=_env_number("STORAGE_RETRY_DELAY", 2.0, float),
            storage_max_concurrent_uploads=_env_number("STORAGE_MAX_CONCURRENT_UPLOADS", 2, int),
            storage_max_concurrent_downloads=_env_number("STORAGE_MAX_CONCURRENT_DOWNLOADS", 4, int),
            storage_stale_upload_seconds=_env_number("STORAGE_STALE_UPLOAD_SECONDS", 1800, int),
            storage_maintenance_interval=_env_number("STORAGE_MAINTENANCE_INTERVAL", 600, int),
        )
