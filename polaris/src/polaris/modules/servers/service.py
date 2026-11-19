from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
UPDATE_REQUEST_TTL_SECONDS = 3600       # агент не ответил за час (старая версия?) — запрос считается устаревшим
MAX_AGENT_SERVICES = 32
MIN_AGENT_HEARTBEAT = 15                # те же нижние пороги, что и в самом агенте
MIN_AGENT_METRICS = 30
MAX_AGENT_INTERVAL = 3600
_UNIT_RE = re.compile(r"^[A-Za-z0-9@_.:\-]+$")
_AGENT_FILE = Path(__file__).resolve().parents[4] / "agent" / "polaris-agent.py"
_VERSION_RE = re.compile(r'^__version__\s*=\s*["\']([0-9]+(?:\.[0-9]+){1,3})["\']', re.MULTILINE)


class SettingsError(ValueError):
    pass


def _hub_public_url() -> str:
    url = Settings.from_env().webapp_url.strip().rstrip("/")
    return url or os.getenv("HUB_PUBLIC_URL", "").rstrip("/") or "https://<your-hub-domain>"


def _install_script_url() -> str:
    """install.sh раздаётся из GitHub (как и в README проекта), а не с домена
    Hub — сам Hub (FastAPI) не отдаёт этот файл как статику."""
    repo = os.getenv("POLARIS_REPO_SLUG", "artinuwq/polaris-hub")
    branch = os.getenv("POLARIS_BRANCH", "main")
    return f"https://raw.githubusercontent.com/{repo}/{branch}/install.sh"


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
    return ServerResponse.from_db_row(
        row, services=services, latest_metric=latest_metric, agent_extra=_agent_extra(row)
    )


# ─────────────────────────── Настройки и обновление агента ───────────────────────────

def agent_bundled_version() -> str:
    """Версия агента в клоне репозитория, на котором стоит Hub (обновился Hub — это число выросло)."""
    try:
        match = _VERSION_RE.search(_AGENT_FILE.read_text(encoding="utf-8"))
    except OSError:
        return ""
    return match.group(1) if match else ""


def _version_tuple(value: str) -> tuple[int, ...] | None:
    if not value or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", value):
        return None
    return tuple(int(part) for part in value.split("."))


def _is_newer(candidate: str, current: str) -> bool:
    a, b = _version_tuple(candidate), _version_tuple(current)
    return bool(a and b and a > b)


def _update_pending(settings: dict[str, Any]) -> bool:
    requested = settings.get("update_requested_at")
    if not requested:
        return False
    try:
        at = datetime.fromisoformat(requested.replace("Z", "+00:00"))
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - at).total_seconds() < UPDATE_REQUEST_TTL_SECONDS


def _agent_extra(row: dict[str, Any]) -> dict[str, Any]:
    settings = repo.get_agent_settings(row["id"])
    latest = agent_bundled_version()
    return {
        "agent_config": settings["config"],
        "agent_config_version": settings["config_version"],
        "update_pending": _update_pending(settings),
        "update_result": settings["update_result"],
        "update_detail": settings["update_detail"],
        "update_result_at": settings["update_result_at"],
        "latest_agent_version": latest,
        "update_available": bool(row.get("agent_id")) and _is_newer(latest, row.get("agent_version", "")),
    }


def normalize_agent_config(data: dict[str, Any]) -> dict[str, Any]:
    services: list[str] = []
    for raw in data.get("services") or []:
        name = str(raw).strip()
        if not name:
            continue
        if len(name) > 100 or name.startswith("-") or not _UNIT_RE.match(name):
            raise SettingsError(f"Недопустимое имя сервиса: {name[:40]}")
        if name not in services:
            services.append(name)
    if len(services) > MAX_AGENT_SERVICES:
        raise SettingsError(f"Не больше {MAX_AGENT_SERVICES} сервисов")
    try:
        heartbeat = int(data.get("heartbeat_interval", 30))
        metrics = int(data.get("metrics_interval", 60))
    except (TypeError, ValueError) as exc:
        raise SettingsError("Интервалы должны быть числами") from exc
    if not MIN_AGENT_HEARTBEAT <= heartbeat <= MAX_AGENT_INTERVAL:
        raise SettingsError(f"Heartbeat: от {MIN_AGENT_HEARTBEAT} до {MAX_AGENT_INTERVAL} секунд")
    if not MIN_AGENT_METRICS <= metrics <= MAX_AGENT_INTERVAL:
        raise SettingsError(f"Метрики: от {MIN_AGENT_METRICS} до {MAX_AGENT_INTERVAL} секунд")
    return {"services": services, "heartbeat_interval": heartbeat, "metrics_interval": metrics}


def update_agent_settings(server_id: str, data: dict[str, Any]) -> ServerResponse | None:
    row = repo.get_server_by_id(server_id)
    if not row:
        return None
    repo.save_agent_config(server_id, normalize_agent_config(data))
    return _dump_server(_ensure_current_status(row))


def request_agent_update(server_id: str) -> ServerResponse | None:
    row = repo.get_server_by_id(server_id)
    if not row:
        return None
    if not row.get("agent_id"):
        raise SettingsError("Агент на этом сервере ещё не зарегистрирован")
    repo.request_agent_update([server_id])
    return _dump_server(_ensure_current_status(row))


def request_agent_update_all() -> int:
    """Просит обновиться все серверы с зарегистрированным агентом, которым это нужно."""
    latest = agent_bundled_version()
    ids = [r["id"] for r in repo.get_all_servers()
           if r.get("agent_id") and _is_newer(latest, r.get("agent_version", ""))]
    return repo.request_agent_update(ids) if ids else 0


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
        f"curl -fsSL {_install_script_url()} | "
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

def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def record_heartbeat(
    server_id: str,
    agent_version: str = "",
    config_version: int = 0,
    update_result: str = "",
    update_detail: str = "",
) -> dict[str, Any]:
    """Фиксирует heartbeat и возвращает агенту «директиву»: новые настройки и/или команду обновиться."""
    updates: dict[str, Any] = {"status": "online", "status_reason": None, "last_seen": _iso_now()}
    if agent_version:
        updates["agent_version"] = agent_version
    repo.record_agent_heartbeat(
        server_id, updates, offline_threshold_seconds=OFFLINE_THRESHOLD_SECONDS
    )
    if update_result:  # результат приходит раньше, чем считается директива: флаг запроса уже снят
        repo.record_update_result(server_id, update_result[:20], update_detail[:300])

    settings = repo.get_agent_settings(server_id)
    directive: dict[str, Any] = {
        "config_version": settings["config_version"],
        "update": _update_pending(settings),
    }
    if settings["config_version"] and settings["config_version"] != config_version:
        directive["config"] = settings["config"]
    return directive


def ingest_metrics(server_id: str, payload: AgentMetricsRequest) -> None:
    disk_dicts = [disk.model_dump() for disk in payload.disk]
    metric = {
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
    }

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

    disk_states = [
        (str(disk.get("mount", "")), disk.get("percent"))
        for disk in disk_dicts
    ]
    service_states = [(svc.name, svc.status.value) for svc in payload.services]

    repo.ingest_metrics_sample(
        server_id,
        metric,
        server_updates,
        disk_states,
        service_states,
        offline_threshold_seconds=OFFLINE_THRESHOLD_SECONDS,
        disk_warning_threshold=DISK_WARNING_THRESHOLD,
        disk_critical_threshold=DISK_CRITICAL_THRESHOLD,
        disk_event_dedup_seconds=DISK_EVENT_DEDUP_SECONDS,
        service_event_dedup_seconds=SERVICE_EVENT_DEDUP_SECONDS,
    )

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


# ─────────────────────────── Сводка для страницы «Серверы» ───────────────────────────

UPTIME_WINDOW_DAYS = 7
UPTIME_MIN_OBSERVED_SECONDS = 300  # серверы моложе 5 минут в аптайм не учитываем


def compute_uptime(
    servers: list[dict[str, Any]],
    offline_events: list[dict[str, Any]],
    now: datetime,
    window_days: int = UPTIME_WINDOW_DAYS,
) -> tuple[float | None, int]:
    """Общий аптайм (в процентах) за последние window_days дней, взвешенный по времени
    наблюдения. Простой берётся из событий agent_offline: от последнего heartbeat
    (payload.last_seen) до resolved_at (или до сейчас, если сервер ещё не вернулся).
    Возвращает (процент | None, сколько серверов учтено)."""
    window_start = now - timedelta(days=window_days)
    by_server: dict[str, list[dict[str, Any]]] = {}
    for ev in offline_events:
        by_server.setdefault(ev["server_id"], []).append(ev)

    total_observed = 0.0
    total_down = 0.0
    counted = 0
    for srv in servers:
        if not srv.get("agent_id") or srv.get("status") == "pending":
            continue
        created = _parse_ts(srv.get("created_at")) or window_start
        start = max(created, window_start)
        observed = (now - start).total_seconds()
        if observed < UPTIME_MIN_OBSERVED_SECONDS:
            continue

        down = 0.0
        for ev in by_server.get(srv["id"], []):
            try:
                payload = json.loads(ev.get("payload") or "{}")
            except ValueError:
                payload = {}
            ev_start = _parse_ts(payload.get("last_seen")) or _parse_ts(ev.get("created_at"))
            ev_end = _parse_ts(ev.get("resolved_at")) or now
            if not ev_start:
                continue
            lo, hi = max(ev_start, start), min(ev_end, now)
            if hi > lo:
                down += (hi - lo).total_seconds()

        total_observed += observed
        total_down += min(down, observed)
        counted += 1

    if not counted or total_observed <= 0:
        return None, 0
    return round((1 - total_down / total_observed) * 100, 1), counted


def get_summary() -> dict[str, Any]:
    """Общие данные для верхних блоков страницы: аптайм за 7 дней и открытые проблемы сервисов.
    Нагрузку и «онлайн / всего» фронтенд считает сам по списку серверов."""
    rows = [_ensure_current_status(r) for r in repo.get_all_servers()]
    now = datetime.now(timezone.utc)
    window_start = (now - timedelta(days=UPTIME_WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    uptime, counted = compute_uptime(rows, repo.get_events_overlapping("agent_offline", window_start), now)

    names = {r["id"]: r["name"] for r in rows}
    issues = []
    for ev in repo.get_open_events("service_down"):
        if ev["server_id"] not in names:
            continue
        try:
            payload = json.loads(ev.get("payload") or "{}")
        except ValueError:
            payload = {}
        issues.append({
            "server_id": ev["server_id"],
            "server_name": names[ev["server_id"]],
            "service": payload.get("service", ""),
            "severity": ev.get("severity", "warning"),
        })
    return {"uptime_7d": uptime, "uptime_servers": counted, "service_issues": issues}
