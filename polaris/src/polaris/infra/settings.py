from __future__ import annotations

import os
from dataclasses import dataclass, field


def _split_ids(raw: str) -> list[int]:
    result: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        result.append(int(part))
    return result


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
    web_host: str = "127.0.0.1"
    web_port: int = 8000
    telegram_force_ipv4: bool = True
    telegram_init_data_max_age: int = 86400

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
            web_host=os.getenv("WEB_HOST", "127.0.0.1"),
            web_port=int(os.getenv("WEB_PORT", "8000")),
            telegram_force_ipv4=os.getenv("TELEGRAM_FORCE_IPV4", "true").lower()
            in {"1", "true", "yes"},
            telegram_init_data_max_age=int(os.getenv("TELEGRAM_INIT_DATA_MAX_AGE", "86400")),
        )
