from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from polaris.infra.settings import Settings
from polaris.modules.servers import repository as repo
from polaris.modules.servers.models import (
    AgentEventRequest,
    AgentMetricsRequest,
    AgentRegisterRequest,
    AgentRegisterResponse,
    RegistrationTokenResponse,
    ServerEventOut,
    ServerResponse,
)

# ── Константы протокола (соответствуют agent/config.example.yaml) ──
HEARTBEAT_INTERVAL_SECONDS = 15
METRICS_INTERVAL_SECONDS = 30
OFFLINE_THRESHOLD_SECONDS = 45          # last_seen старше — сервер offline
REGISTRATION_TOKEN_TTL_SECONDS = 900    # 15 минут на установку агента
DISK_WARNING_THRESHOLD = 90.0
DISK_CRITICAL_THRESHOLD = 95.0
DISK_EVENT_DEDUP_SECONDS = 1800         # не спамить событием чаще, чем раз в 30 мин
SERVICE_EVENT_DEDUP_SECONDS = 600


def _hub_public_url() -> str:
    url = Settings.from_env().webapp_url.strip().rstrip("/")
    return url or os.getenv("HUB_PUBLIC_URL", "").rstrip("/") or "https://<your-hub-domain>"


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ─────────────────────────── Admin: серверы ───────────────────────────

def list_servers() -> list[ServerResponse]:
    rows = [_ensure_current_status(r) for r in repo.get_all_servers()]
    return [_dump_server(r) for r in rows]


def get_server(server_id: str) -> ServerResponse | None:
    row = repo.get_server_by_id(server_id)
    if not row:
        return None
    row = _ensure_current_status(row)
    return _dump_server(row)


def create_server(name: str, address: str = "") -> tuple[ServerResponse, RegistrationTokenResponse]:
    row = repo.create_server(name=name, address=address)
    token_resp = generate_registration_token(row["id"])
    return _dump_server(row), token_resp


def update_server(server_id: str, data: dict[str, Any]) -> ServerResponse | None:
    payload = {k: v for k, v in data.items() if v is not None}
    row = repo.update_server(server_id, payload)
    if not row:
        return None
    return _dump_server(_ensure_current_status(row))


def delete_server(server_id: str) -> bool:
    return repo.delete_server(server_id)


def _dump_server(row: dict[str, Any]) -> ServerResponse:
    services = repo.get_services_for_server(row["id"])
    latest_metric = repo.get_latest_metric(row["id"])
    return ServerResponse.from_db_row(row, services=services, latest_metric=latest_metric)


def _ensure_current_status(row: dict[str, Any]) -> dict[str, Any]:
    """Ленивое определение online/offline и истечения registration token —
    без фонового планировщика: пересчитывается при каждом чтении, как и
    аналогичная логика в Finance для дат платежей."""
    server_id = row["id"]
    status = row.get("status")
    changed: dict[str, Any] = {}

    if status == "online":
        last_seen = row.get("last_seen")
        if last_seen:
            try:
                seen_dt = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
                elapsed = (datetime.now(timezone.utc) - seen_dt).total_seconds()
            except ValueError:
                elapsed = 0
            if elapsed > OFFLINE_THRESHOLD_SECONDS:
                changed["status"] = "offline"
                changed["status_reason"] = None
                _raise_or_resolve_event(
                    server_id, "agent_offline", "critical",
                    {"last_seen": last_seen, "_dedup_key": None},
                    resolve=False,
                )

    elif status == "pending":
        token = repo.get_valid_registration_token(server_id)
        if not token:
            # Был ли вообще выпущен токен, который теперь истёк?
            with_any_token = repo.get_server_by_id(server_id)  # noop read, keeps intent explicit
            changed["status"] = "error"
            changed["status_reason"] = "token_expired"

    elif status == "offline":
        # Если внезапно снова начали приходить heartbeat — heartbeat() сам переключит в online.
        pass

    if changed:
        updated = repo.update_server(server_id, changed)
        return updated or row
    return row


def generate_registration_token(server_id: str) -> RegistrationTokenResponse:
    raw_token, _token_id, expires_at = repo.create_registration_token(
        server_id, REGISTRATION_TOKEN_TTL_SECONDS
    )
    repo.update_server(server_id, {"status": "pending", "status_reason": None})

    install_command = (
        f"curl -fsSL {_hub_public_url()}/install.sh | "
        f"sudo bash -s -- agent --hub {_hub_public_url()} --token {raw_token}"
    )

    return RegistrationTokenResponse(
        server_id=server_id,
        token=raw_token,
        expires_at=expires_at,
        expires_in_seconds=REGISTRATION_TOKEN_TTL_SECONDS,
        install_command=install_command,
    )


# ─────────────────────────── Agent: регистрация ───────────────────────────

class RegistrationError(Exception):
    pass


def register_agent(payload: AgentRegisterRequest) -> AgentRegisterResponse:
    token_row = repo.find_registration_token_by_raw(payload.token)
    if not token_row:
        raise RegistrationError("Неверный registration token")
    if token_row.get("used_at"):
        raise RegistrationError("Registration token уже использован")

    expires_at = token_row.get("expires_at", "")
    try:
        expires_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if datetime.now(timezone.utc) > expires_dt:
            raise RegistrationError("Registration token истёк")
    except ValueError:
        pass

    server_id = token_row["server_id"]
    server = repo.get_server_by_id(server_id)
    if not server:
        raise RegistrationError("Сервер не найден")

    agent_id = repo.generate_uuid()
    agent_token = repo.generate_token("plr_agent")

    repo.update_server(server_id, {
        "hostname": payload.hostname,
        "os": payload.os,
        "kernel": payload.kernel,
        "architecture": payload.architecture,
        "agent_version": payload.agent_version,
        "address": payload.address or server.get("address", ""),
        "agent_id": agent_id,
        "agent_token_hash": repo.hash_token(agent_token),
        "status": "online",
        "status_reason": None,
        "last_seen": _iso_now(),
    })
    repo.mark_token_used(token_row["id"])
    _raise_or_resolve_event(server_id, "agent_offline", "critical", {}, resolve=True)

    return AgentRegisterResponse(
        server_id=server_id,
        agent_id=agent_id,
        agent_token=agent_token,
        heartbeat_interval=HEARTBEAT_INTERVAL_SECONDS,
        metrics_interval=METRICS_INTERVAL_SECONDS,
    )


def authenticate_agent(agent_id: str, agent_token: str) -> dict[str, Any] | None:
    server = repo.get_server_by_agent_id(agent_id)
    if not server or not server.get("agent_token_hash"):
        return None
    if repo.hash_token(agent_token) != server["agent_token_hash"]:
        return None
    return server


# ─────────────────────────── Agent: heartbeat / metrics / events ───────────────────────────

def record_heartbeat(server_id: str, agent_version: str = "") -> None:
    updates: dict[str, Any] = {"status": "online", "status_reason": None, "last_seen": _iso_now()}
    if agent_version:
        updates["agent_version"] = agent_version
    repo.update_server(server_id, updates)
    _raise_or_resolve_event(server_id, "agent_offline", "critical", {}, resolve=True)


def ingest_metrics(server_id: str, payload: AgentMetricsRequest) -> None:
    disk_dicts = [d.model_dump() for d in payload.disk]

    repo.insert_metric(server_id, {
        "cpu_usage": payload.cpu.usage,
        "cpu_load1": payload.cpu.load1,
        "cpu_load5": payload.cpu.load5,
        "cpu_load15": payload.cpu.load15,
        "mem_total": payload.memory.total,
        "mem_used": payload.memory.used,
        "mem_available": payload.memory.available,
        "mem_percent": payload.memory.percent,
        "disk": disk_dicts,
        "net_rx_bytes": payload.network.rx_bytes,
        "net_tx_bytes": payload.network.tx_bytes,
        "uptime_seconds": payload.system.uptime_seconds,
    })

    server_updates: dict[str, Any] = {"status": "online", "status_reason": None, "last_seen": _iso_now()}
    if payload.system.hostname:
        server_updates["hostname"] = payload.system.hostname
    if payload.system.os:
        server_updates["os"] = payload.system.os
    if payload.system.kernel:
        server_updates["kernel"] = payload.system.kernel
    if payload.system.architecture:
        server_updates["architecture"] = payload.system.architecture
    if payload.system.uptime_seconds is not None:
        server_updates["uptime_seconds"] = payload.system.uptime_seconds
    repo.update_server(server_id, server_updates)
    _raise_or_resolve_event(server_id, "agent_offline", "critical", {}, resolve=True)

    # ── Диски: порог фиксируем как факт, "достойно ли внимания" решает Attention Engine ──
    for disk in disk_dicts:
        mount = disk.get("mount", "")
        percent = disk.get("percent")
        if percent is None:
            continue
        if percent >= DISK_WARNING_THRESHOLD:
            severity = "critical" if percent >= DISK_CRITICAL_THRESHOLD else "warning"
            _raise_or_resolve_event(
                server_id, "disk_threshold", severity,
                {"mount": mount, "usage": percent, "_dedup_key": mount},
                resolve=False, dedup_seconds=DISK_EVENT_DEDUP_SECONDS,
            )
        else:
            _raise_or_resolve_event(server_id, "disk_threshold", "warning", {"_dedup_key": mount}, resolve=True)

    # ── Сервисы: статус + событие при переходе в stopped/failed ──
    for svc in payload.services:
        repo.upsert_service_status(server_id, svc.name, svc.status.value)
        if svc.status.value in ("stopped", "failed"):
            severity = "critical" if svc.status.value == "failed" else "warning"
            _raise_or_resolve_event(
                server_id, "service_down", severity,
                {"service": svc.name, "status": svc.status.value, "_dedup_key": svc.name},
                resolve=False, dedup_seconds=SERVICE_EVENT_DEDUP_SECONDS,
            )
        else:
            _raise_or_resolve_event(server_id, "service_down", "warning", {"_dedup_key": svc.name}, resolve=True)

    # Заведомо НЕ создаём событие по одиночному замеру CPU/RAM. Sustained-паттерны
    # (например "CPU>90% несколько минут подряд") — задача Attention Engine,
    # который может читать get_metrics_history() и сам решать, что важно.


def ingest_event(server_id: str, payload: AgentEventRequest) -> ServerEventOut:
    dedup_key = payload.payload.get("service") or payload.payload.get("mount")
    event = _raise_or_resolve_event(
        server_id, payload.type, payload.severity.value,
        {**payload.payload, "_dedup_key": dedup_key},
        resolve=False, dedup_seconds=SERVICE_EVENT_DEDUP_SECONDS,
    )
    return ServerEventOut.from_db_row(event) if event else None


def _raise_or_resolve_event(
    server_id: str,
    type_: str,
    severity: str,
    payload: dict[str, Any],
    resolve: bool,
    dedup_seconds: int = 300,
) -> dict[str, Any] | None:
    dedup_key = payload.get("_dedup_key")

    if resolve:
        repo.resolve_events_of_type(server_id, type_, dedup_key)
        return None

    existing = repo.find_recent_unresolved_event(server_id, type_, dedup_key, dedup_seconds)
    if existing:
        return existing  # анти-спам: не плодим дубликаты в пределах окна
    return repo.create_event(server_id, type_, severity, payload)


# ─────────────────────────── Чтение для UI ───────────────────────────

def get_events(server_id: str, limit: int = 30) -> list[ServerEventOut]:
    return [ServerEventOut.from_db_row(r) for r in repo.get_events_for_server(server_id, limit)]


def get_metrics_history(server_id: str, limit: int = 60) -> list[dict[str, Any]]:
    return repo.get_metrics_history(server_id, limit)


# ─────────────────────────── Attention Engine feed ───────────────────────────

def get_attention_candidates() -> list[dict[str, Any]]:
    """Сырые факты для Attention Engine. Servers НЕ решает приоритет —
    только сообщает: какой сервер offline, какие события ещё не resolved."""
    candidates: list[dict[str, Any]] = []

    for row in [_ensure_current_status(r) for r in repo.get_all_servers()]:
        if row.get("status") == "offline":
            candidates.append({
                "source": "servers",
                "type": "agent_offline",
                "server_id": row["id"],
                "server_name": row["name"],
                "severity": "critical",
                "last_seen": row.get("last_seen"),
            })

    for ev in repo.get_all_recent_events(limit=100):
        if ev.get("resolved_at"):
            continue
        if ev.get("type") == "agent_offline":
            continue  # уже отражено выше через статус сервера
        server = repo.get_server_by_id(ev["server_id"])
        candidates.append({
            "source": "servers",
            "type": ev["type"],
            "server_id": ev["server_id"],
            "server_name": server["name"] if server else "",
            "severity": ev.get("severity", "info"),
            "created_at": ev.get("created_at"),
        })

    return candidates
