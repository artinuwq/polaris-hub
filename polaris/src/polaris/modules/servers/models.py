from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ServerStatus(str, Enum):
    PENDING = "pending"
    ONLINE = "online"
    OFFLINE = "offline"
    ERROR = "error"


class EventSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class ServiceState(str, Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"
    UNKNOWN = "unknown"


# ─────────────────────────── Admin-facing (Hub UI) ───────────────────────────

class ServerCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    address: str = ""


class ServerUpdate(BaseModel):
    name: str | None = None
    address: str | None = None
    status: ServerStatus | None = None  # ручной override (напр. пометить offline)


class ServiceStatusOut(BaseModel):
    name: str
    status: ServiceState
    updated_at: str


class ServerResponse(BaseModel):
    id: str
    name: str
    hostname: str
    address: str
    status: ServerStatus
    status_reason: str | None = None
    agent_id: str | None = None
    agent_version: str
    os: str
    kernel: str
    architecture: str
    uptime_seconds: int | None = None
    last_seen: str | None = None
    seconds_since_seen: int | None = None
    cpu_usage: float | None = None
    mem_percent: float | None = None
    disk_percent: float | None = None
    services: list[ServiceStatusOut] = Field(default_factory=list)
    created_at: str
    updated_at: str

    @classmethod
    def from_db_row(
        cls,
        row: dict[str, Any],
        services: list[dict[str, Any]] | None = None,
        latest_metric: dict[str, Any] | None = None,
    ) -> "ServerResponse":
        from datetime import datetime, timezone

        seconds_since_seen = None
        last_seen = row.get("last_seen")
        if last_seen:
            try:
                seen_dt = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
                seconds_since_seen = int((datetime.now(timezone.utc) - seen_dt).total_seconds())
            except ValueError:
                seconds_since_seen = None

        cpu_usage = mem_percent = disk_percent = None
        if latest_metric:
            import json
            cpu_usage = latest_metric.get("cpu_usage")
            mem_percent = latest_metric.get("mem_percent")
            try:
                disks = json.loads(latest_metric.get("disk_json") or "[]")
            except ValueError:
                disks = []
            root_disk = next((d for d in disks if d.get("mount") == "/"), None)
            if root_disk:
                disk_percent = root_disk.get("percent")
            elif disks:
                disk_percent = max((d.get("percent") or 0) for d in disks)

        return cls(
            id=row["id"],
            name=row["name"],
            hostname=row.get("hostname", ""),
            address=row.get("address", ""),
            status=row.get("status", "pending"),
            status_reason=row.get("status_reason"),
            agent_id=row.get("agent_id"),
            agent_version=row.get("agent_version", ""),
            os=row.get("os", ""),
            kernel=row.get("kernel", ""),
            architecture=row.get("architecture", ""),
            uptime_seconds=row.get("uptime_seconds"),
            last_seen=last_seen,
            seconds_since_seen=seconds_since_seen,
            cpu_usage=cpu_usage,
            mem_percent=mem_percent,
            disk_percent=disk_percent,
            services=[ServiceStatusOut(**s) for s in (services or [])],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class RegistrationTokenResponse(BaseModel):
    server_id: str
    token: str  # сырой токен — показывается только один раз
    expires_at: str
    expires_in_seconds: int
    install_command: str


class ServerMetricPoint(BaseModel):
    cpu_usage: float | None = None
    cpu_load1: float | None = None
    cpu_load5: float | None = None
    cpu_load15: float | None = None
    mem_total: int | None = None
    mem_used: int | None = None
    mem_available: int | None = None
    mem_percent: float | None = None
    disk: list[dict[str, Any]] = Field(default_factory=list)
    net_rx_bytes: int | None = None
    net_tx_bytes: int | None = None
    uptime_seconds: int | None = None
    recorded_at: str


class ServerEventOut(BaseModel):
    id: str
    server_id: str
    type: str
    severity: EventSeverity
    payload: dict[str, Any]
    created_at: str
    resolved_at: str | None = None

    @classmethod
    def from_db_row(cls, row: dict[str, Any]) -> "ServerEventOut":
        import json
        payload_raw = row.get("payload", "{}")
        payload = json.loads(payload_raw) if isinstance(payload_raw, str) else dict(payload_raw)
        return cls(
            id=row["id"],
            server_id=row["server_id"],
            type=row["type"],
            severity=row.get("severity", "info"),
            payload=payload,
            created_at=row["created_at"],
            resolved_at=row.get("resolved_at"),
        )


# ─────────────────────────── Agent-facing (Polaris Agent protocol) ───────────────────────────

class AgentRegisterRequest(BaseModel):
    token: str = Field(..., min_length=1)
    hostname: str = ""
    os: str = ""
    kernel: str = ""
    architecture: str = ""
    agent_version: str = ""
    address: str = ""


class AgentRegisterResponse(BaseModel):
    server_id: str
    agent_id: str
    agent_token: str  # постоянный credential — показывается только один раз, при регистрации
    heartbeat_interval: int
    metrics_interval: int


class AgentHeartbeatRequest(BaseModel):
    agent_version: str = ""
    status: str = "online"


class AgentServiceStatus(BaseModel):
    name: str
    status: ServiceState = ServiceState.UNKNOWN


class AgentSystemInfo(BaseModel):
    hostname: str = ""
    os: str = ""
    kernel: str = ""
    architecture: str = ""
    uptime_seconds: int | None = None


class AgentMetricsCpu(BaseModel):
    usage: float | None = None
    load1: float | None = None
    load5: float | None = None
    load15: float | None = None


class AgentMetricsMemory(BaseModel):
    total: int | None = None
    used: int | None = None
    available: int | None = None
    percent: float | None = None


class AgentMetricsDisk(BaseModel):
    mount: str
    total: int | None = None
    used: int | None = None
    available: int | None = None
    percent: float | None = None


class AgentMetricsNetwork(BaseModel):
    rx_bytes: int | None = None
    tx_bytes: int | None = None


class AgentMetricsRequest(BaseModel):
    cpu: AgentMetricsCpu = Field(default_factory=AgentMetricsCpu)
    memory: AgentMetricsMemory = Field(default_factory=AgentMetricsMemory)
    disk: list[AgentMetricsDisk] = Field(default_factory=list)
    network: AgentMetricsNetwork = Field(default_factory=AgentMetricsNetwork)
    system: AgentSystemInfo = Field(default_factory=AgentSystemInfo)
    services: list[AgentServiceStatus] = Field(default_factory=list)


class AgentEventRequest(BaseModel):
    type: str = Field(..., min_length=1, max_length=64)
    severity: EventSeverity = EventSeverity.INFO
    payload: dict[str, Any] = Field(default_factory=dict)
