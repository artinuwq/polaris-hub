from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path("/etc/polaris-agent/config.yaml")


@dataclass
class AgentConfig:
    hub_url: str = ""
    agent_id: str = ""
    agent_token: str = ""
    heartbeat_interval: int = 15
    metrics_interval: int = 30
    collectors: dict[str, bool] = field(default_factory=lambda: {
        "cpu": True, "memory": True, "disk": True, "network": True, "system": True,
    })
    services: list[str] = field(default_factory=list)
    config_path: Path = DEFAULT_CONFIG_PATH

    @property
    def is_registered(self) -> bool:
        return bool(self.agent_id and self.agent_token)

    @classmethod
    def load(cls, path: Path = DEFAULT_CONFIG_PATH) -> "AgentConfig":
        if not path.exists():
            return cls(config_path=path)

        raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        hub = raw.get("hub", {}) or {}
        agent = raw.get("agent", {}) or {}
        heartbeat = raw.get("heartbeat", {}) or {}
        metrics = raw.get("metrics", {}) or {}
        collectors = raw.get("collectors", {}) or {}
        services = raw.get("services", []) or []

        default_collectors = cls().collectors
        default_collectors.update({k: bool(v) for k, v in collectors.items()})

        return cls(
            hub_url=str(hub.get("url", "")).rstrip("/"),
            agent_id=str(agent.get("id", "")),
            agent_token=str(agent.get("token", "")),
            heartbeat_interval=int(heartbeat.get("interval", 15)),
            metrics_interval=int(metrics.get("interval", 30)),
            collectors=default_collectors,
            services=[str(s) for s in services],
            config_path=path,
        )

    def save(self) -> None:
        """Записывает конфиг с правами 0600 — секреты не должны читаться другими пользователями."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)

        data = {
            "hub": {"url": self.hub_url},
            "agent": {"id": self.agent_id, "token": self.agent_token},
            "heartbeat": {"interval": self.heartbeat_interval},
            "metrics": {"interval": self.metrics_interval},
            "collectors": self.collectors,
            "services": self.services,
        }

        tmp_path = self.config_path.with_suffix(".tmp")
        tmp_path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
        os.chmod(tmp_path, 0o600)
        tmp_path.replace(self.config_path)
