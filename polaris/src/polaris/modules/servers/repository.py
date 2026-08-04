from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from polaris.infra.database import get_db

# Сколько последних точек metrics хранить на сервер (простая история,
# без полноценной time-series платформы — так и задумано на этом этапе).
METRICS_HISTORY_LIMIT = 200


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def generate_uuid() -> str:
    return str(uuid.uuid4())


def generate_token(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ─────────────────────────── Servers ───────────────────────────

def get_all_servers() -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM servers ORDER BY name ASC").fetchall()
        return [dict(r) for r in rows]


def get_server_by_id(server_id: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM servers WHERE id = ?", (server_id,)).fetchone()
        return dict(row) if row else None


def get_server_by_agent_id(agent_id: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM servers WHERE agent_id = ?", (agent_id,)).fetchone()
        return dict(row) if row else None


def create_server(name: str, address: str = "") -> dict[str, Any]:
    server_id = generate_uuid()
    now = now_iso()
    with get_db() as conn:
        conn.execute(
            """INSERT INTO servers
               (id, name, hostname, address, status, agent_version, os, kernel,
                architecture, created_at, updated_at)
               VALUES (?, ?, '', ?, 'pending', '', '', '', '', ?, ?)""",
            (server_id, name, address, now, now),
        )
        row = conn.execute("SELECT * FROM servers WHERE id = ?", (server_id,)).fetchone()
        return dict(row)


_UPDATABLE_SERVER_FIELDS = (
    "name", "hostname", "address", "status", "status_reason", "agent_id",
    "agent_token_hash", "agent_version", "os", "kernel", "architecture",
    "uptime_seconds", "last_seen",
)


def update_server(server_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
    updates = [(f, data[f]) for f in _UPDATABLE_SERVER_FIELDS if f in data]
    if not updates:
        return get_server_by_id(server_id)

    updates.append(("updated_at", now_iso()))
    set_clause = ", ".join(f"{f} = ?" for f, _ in updates)
    values = [v for _, v in updates] + [server_id]

    with get_db() as conn:
        conn.execute(f"UPDATE servers SET {set_clause} WHERE id = ?", values)
        row = conn.execute("SELECT * FROM servers WHERE id = ?", (server_id,)).fetchone()
        return dict(row) if row else None


def delete_server(server_id: str) -> bool:
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM servers WHERE id = ?", (server_id,))
        return cursor.rowcount > 0


# ─────────────────────────── Registration tokens ───────────────────────────

def create_registration_token(server_id: str, ttl_seconds: int) -> tuple[str, str, str]:
    """Возвращает (raw_token, token_id, expires_at_iso)."""
    raw_token = generate_token("plr_reg")
    token_id = generate_uuid()
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).strftime(
        "%Y-%m-%dT%H:%M:%S.%f"
    )[:-3] + "Z"

    with get_db() as conn:
        conn.execute(
            """INSERT INTO registration_tokens (id, server_id, token_hash, expires_at, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (token_id, server_id, hash_token(raw_token), expires_at, now_iso()),
        )
    return raw_token, token_id, expires_at


def get_valid_registration_token(server_id: str) -> dict[str, Any] | None:
    """Последний неиспользованный и не истёкший токен для сервера (для UI: показать таймер)."""
    with get_db() as conn:
        row = conn.execute(
            """SELECT * FROM registration_tokens
               WHERE server_id = ? AND used_at IS NULL AND expires_at > ?
               ORDER BY created_at DESC LIMIT 1""",
            (server_id, now_iso()),
        ).fetchone()
        return dict(row) if row else None


def find_registration_token_by_raw(raw_token: str) -> dict[str, Any] | None:
    token_hash = hash_token(raw_token)
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM registration_tokens WHERE token_hash = ?", (token_hash,)
        ).fetchone()
        return dict(row) if row else None


def mark_token_used(token_id: str) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE registration_tokens SET used_at = ? WHERE id = ?", (now_iso(), token_id)
        )


# ─────────────────────────── Metrics ───────────────────────────

def insert_metric(server_id: str, data: dict[str, Any]) -> None:
    metric_id = generate_uuid()
    with get_db() as conn:
        conn.execute(
            """INSERT INTO server_metrics
               (id, server_id, cpu_usage, cpu_load1, cpu_load5, cpu_load15,
                mem_total, mem_used, mem_available, mem_percent, disk_json,
                net_rx_bytes, net_tx_bytes, uptime_seconds, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                metric_id, server_id,
                data.get("cpu_usage"), data.get("cpu_load1"), data.get("cpu_load5"), data.get("cpu_load15"),
                data.get("mem_total"), data.get("mem_used"), data.get("mem_available"), data.get("mem_percent"),
                json.dumps(data.get("disk", [])),
                data.get("net_rx_bytes"), data.get("net_tx_bytes"), data.get("uptime_seconds"),
                now_iso(),
            ),
        )
        # Обрезаем историю — оставляем только последние N точек на сервер.
        conn.execute(
            """DELETE FROM server_metrics WHERE server_id = ? AND id NOT IN (
                   SELECT id FROM server_metrics WHERE server_id = ?
                   ORDER BY recorded_at DESC LIMIT ?
               )""",
            (server_id, server_id, METRICS_HISTORY_LIMIT),
        )


def get_latest_metric(server_id: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM server_metrics WHERE server_id = ? ORDER BY recorded_at DESC LIMIT 1",
            (server_id,),
        ).fetchone()
        return dict(row) if row else None


def get_metrics_history(server_id: str, limit: int = 60) -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM server_metrics WHERE server_id = ? ORDER BY recorded_at DESC LIMIT ?",
            (server_id, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


# ─────────────────────────── Service statuses ───────────────────────────

def upsert_service_status(server_id: str, name: str, status: str) -> None:
    with get_db() as conn:
        conn.execute(
            """INSERT INTO server_services_status (server_id, service_name, status, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(server_id, service_name)
               DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at""",
            (server_id, name, status, now_iso()),
        )


def get_services_for_server(server_id: str) -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT service_name AS name, status, updated_at FROM server_services_status "
            "WHERE server_id = ? ORDER BY service_name ASC",
            (server_id,),
        ).fetchall()
        return [dict(r) for r in rows]


# ─────────────────────────── Events ───────────────────────────

def create_event(server_id: str, type_: str, severity: str, payload: dict[str, Any]) -> dict[str, Any]:
    event_id = generate_uuid()
    now = now_iso()
    with get_db() as conn:
        conn.execute(
            """INSERT INTO server_events (id, server_id, type, severity, payload, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (event_id, server_id, type_, severity, json.dumps(payload), now),
        )
        row = conn.execute("SELECT * FROM server_events WHERE id = ?", (event_id,)).fetchone()
        return dict(row)


def find_recent_unresolved_event(
    server_id: str, type_: str, dedup_key: str | None, within_seconds: int
) -> dict[str, Any] | None:
    """Ищет недавнее событие того же типа (и, если задан, того же контекста —
    напр. того же mount/service) для базовой анти-спам дедупликации."""
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=within_seconds)).strftime(
        "%Y-%m-%dT%H:%M:%S.%f"
    )[:-3] + "Z"
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM server_events
               WHERE server_id = ? AND type = ? AND resolved_at IS NULL AND created_at >= ?
               ORDER BY created_at DESC""",
            (server_id, type_, cutoff),
        ).fetchall()
        for row in rows:
            row_d = dict(row)
            if dedup_key is None:
                return row_d
            payload = json.loads(row_d.get("payload") or "{}")
            if payload.get("_dedup_key") == dedup_key:
                return row_d
        return None


def get_events_for_server(server_id: str, limit: int = 30) -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM server_events WHERE server_id = ? ORDER BY created_at DESC LIMIT ?",
            (server_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_recent_events(limit: int = 50) -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM server_events ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def resolve_events_of_type(server_id: str, type_: str, dedup_key: str | None = None) -> None:
    """Закрыть открытые события данного типа — напр. когда сервис снова 'running'
    или диск снова ниже порога."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, payload FROM server_events WHERE server_id = ? AND type = ? AND resolved_at IS NULL",
            (server_id, type_),
        ).fetchall()
        for row in rows:
            payload = json.loads(row["payload"] or "{}")
            if dedup_key is not None and payload.get("_dedup_key") != dedup_key:
                continue
            conn.execute(
                "UPDATE server_events SET resolved_at = ? WHERE id = ?", (now_iso(), row["id"])
            )
