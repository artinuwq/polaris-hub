from __future__ import annotations

import hashlib
import json
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from polaris.infra.database import get_db

# Сколько последних точек metrics хранить на сервер (простая история,
# без полноценной time-series платформы — так и задумано на этом этапе).
METRICS_HISTORY_LIMIT = 200
# Не запускаем DELETE при каждом поступлении метрик. Допускаем небольшой
# буфер, после которого обрезаем историю обратно до METRICS_HISTORY_LIMIT.
METRICS_PRUNE_BATCH = 20

# Закрытые события старше этого срока удаляются при следующем создании события.
# Срок можно переопределить переменной POLARIS_SERVER_EVENT_RETENTION_DAYS.
# Незакрытые события не удаляются независимо от возраста.
DEFAULT_EVENT_RETENTION_DAYS = 180


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def generate_uuid() -> str:
    return str(uuid.uuid4())


def generate_token(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _event_retention_days() -> int:
    raw = os.getenv("POLARIS_SERVER_EVENT_RETENTION_DAYS", "").strip()
    if not raw:
        return DEFAULT_EVENT_RETENTION_DAYS
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_EVENT_RETENTION_DAYS


def _record_gap_in_transaction(
    conn,
    server_id: str,
    now: datetime,
    now_text: str,
    offline_threshold_seconds: int,
) -> None:
    """Сохраняет пропущенный интервал, не открывая отдельное соединение."""
    row = conn.execute(
        "SELECT status, last_seen FROM servers WHERE id = ?", (server_id,)
    ).fetchone()
    if not row or row["status"] != "online" or not row["last_seen"]:
        return

    try:
        seen = datetime.fromisoformat(str(row["last_seen"]).replace("Z", "+00:00"))
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
    except ValueError:
        return

    if (now - seen).total_seconds() <= offline_threshold_seconds:
        return

    unresolved = conn.execute(
        """SELECT 1 FROM server_events
           WHERE server_id = ? AND type = 'agent_offline' AND resolved_at IS NULL
           LIMIT 1""",
        (server_id,),
    ).fetchone()
    if unresolved:
        return

    _create_event_in_transaction(
        conn,
        server_id,
        "agent_offline",
        "critical",
        {"last_seen": row["last_seen"], "retro": True},
        created_at=str(row["last_seen"]),
        resolved_at=now_text,
        now_text=now_text,
    )


def _update_server_in_transaction(conn, server_id: str, data: dict[str, Any], now_text: str) -> None:
    updates = [(field, data[field]) for field in _UPDATABLE_SERVER_FIELDS if field in data]
    if not updates:
        return
    updates.append(("updated_at", now_text))
    set_clause = ", ".join(f"{field} = ?" for field, _ in updates)
    values = [value for _, value in updates] + [server_id]
    conn.execute(f"UPDATE servers SET {set_clause} WHERE id = ?", values)


def _insert_metric_in_transaction(
    conn, server_id: str, data: dict[str, Any], recorded_at: str
) -> None:
    conn.execute(
        """INSERT INTO server_metrics
           (id, server_id, cpu_usage, cpu_load1, cpu_load5, cpu_load15,
            mem_total, mem_used, mem_available, mem_percent, disk_json,
            net_rx_bytes, net_tx_bytes, uptime_seconds, recorded_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            generate_uuid(), server_id,
            data.get("cpu_usage"), data.get("cpu_load1"), data.get("cpu_load5"), data.get("cpu_load15"),
            data.get("mem_total"), data.get("mem_used"), data.get("mem_available"), data.get("mem_percent"),
            json.dumps(data.get("disk", [])),
            data.get("net_rx_bytes"), data.get("net_tx_bytes"), data.get("uptime_seconds"),
            recorded_at,
        ),
    )

    # LIMIT/OFFSET проверяет только наличие точки за пределами допустимого
    # буфера по индексированному времени. DELETE выполняется примерно раз
    # в METRICS_PRUNE_BATCH новых точек, а не на каждом metrics-запросе.
    prune_offset = METRICS_HISTORY_LIMIT + METRICS_PRUNE_BATCH
    overflow = conn.execute(
        """SELECT 1 FROM server_metrics
           WHERE server_id = ?
           ORDER BY recorded_at DESC
           LIMIT 1 OFFSET ?""",
        (server_id, prune_offset),
    ).fetchone()
    if overflow:
        conn.execute(
            """DELETE FROM server_metrics WHERE server_id = ? AND id NOT IN (
                   SELECT id FROM server_metrics WHERE server_id = ?
                   ORDER BY recorded_at DESC LIMIT ?
               )""",
            (server_id, server_id, METRICS_HISTORY_LIMIT),
        )


def _upsert_service_status_in_transaction(
    conn, server_id: str, name: str, status: str, now_text: str
) -> None:
    conn.execute(
        """INSERT INTO server_services_status (server_id, service_name, status, updated_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(server_id, service_name)
           DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at
           WHERE server_services_status.status IS NOT excluded.status""",
        (server_id, name, status, now_text),
    )


def _find_recent_unresolved_event_in_transaction(
    conn, server_id: str, type_: str, dedup_key: str | None, cutoff: str
) -> dict[str, Any] | None:
    rows = conn.execute(
        """SELECT * FROM server_events
           WHERE server_id = ? AND type = ? AND resolved_at IS NULL AND created_at >= ?
           ORDER BY created_at DESC""",
        (server_id, type_, cutoff),
    ).fetchall()
    for row in rows:
        row_dict = dict(row)
        if dedup_key is None:
            return row_dict
        try:
            event_payload = json.loads(row_dict.get("payload") or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if event_payload.get("_dedup_key") == dedup_key:
            return row_dict
    return None


def _resolve_events_in_transaction(
    conn, server_id: str, type_: str, dedup_key: str | None, resolved_at: str
) -> int:
    if dedup_key is None:
        cursor = conn.execute(
            """UPDATE server_events SET resolved_at = ?
               WHERE server_id = ? AND type = ? AND resolved_at IS NULL""",
            (resolved_at, server_id, type_),
        )
        return max(cursor.rowcount, 0)

    rows = conn.execute(
        """SELECT id, payload FROM server_events
           WHERE server_id = ? AND type = ? AND resolved_at IS NULL""",
        (server_id, type_),
    ).fetchall()
    matching_ids: list[tuple[str, str]] = []
    for row in rows:
        try:
            event_payload = json.loads(row["payload"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if event_payload.get("_dedup_key") == dedup_key:
            matching_ids.append((resolved_at, row["id"]))
    if matching_ids:
        conn.executemany(
            "UPDATE server_events SET resolved_at = ? WHERE id = ? AND resolved_at IS NULL",
            matching_ids,
        )
    return len(matching_ids)


def _create_event_in_transaction(
    conn,
    server_id: str,
    type_: str,
    severity: str,
    payload: dict[str, Any],
    *,
    created_at: str | None = None,
    resolved_at: str | None = None,
    now_text: str | None = None,
) -> dict[str, Any]:
    now_text = now_text or now_iso()
    # Удаляем только уже закрытые события старше срока хранения. Открытые
    # события сохраняются всегда, чтобы не терять текущие проблемы.
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=_event_retention_days())
    ).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    conn.execute(
        "DELETE FROM server_events WHERE resolved_at IS NOT NULL AND resolved_at < ?",
        (cutoff,),
    )

    event_id = generate_uuid()
    conn.execute(
        """INSERT INTO server_events
           (id, server_id, type, severity, payload, created_at, resolved_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            event_id, server_id, type_, severity, json.dumps(payload),
            created_at or now_text, resolved_at,
        ),
    )
    row = conn.execute("SELECT * FROM server_events WHERE id = ?", (event_id,)).fetchone()
    return dict(row)


def _apply_event_change_in_transaction(
    conn,
    server_id: str,
    type_: str,
    severity: str,
    payload: dict[str, Any],
    *,
    resolve: bool,
    dedup_seconds: int,
    now_dt: datetime,
    now_text: str,
) -> dict[str, Any] | None:
    dedup_key = payload.get("_dedup_key")
    if resolve:
        _resolve_events_in_transaction(conn, server_id, type_, dedup_key, now_text)
        return None

    cutoff = (now_dt - timedelta(seconds=dedup_seconds)).strftime(
        "%Y-%m-%dT%H:%M:%S.%f"
    )[:-3] + "Z"
    existing = _find_recent_unresolved_event_in_transaction(
        conn, server_id, type_, dedup_key, cutoff
    )
    if existing:
        return existing
    return _create_event_in_transaction(
        conn, server_id, type_, severity, payload, now_text=now_text
    )


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
    if not any(field in data for field in _UPDATABLE_SERVER_FIELDS):
        return get_server_by_id(server_id)

    now_text = now_iso()
    with get_db() as conn:
        _update_server_in_transaction(conn, server_id, data, now_text)
        row = conn.execute("SELECT * FROM servers WHERE id = ?", (server_id,)).fetchone()
        return dict(row) if row else None


def record_agent_heartbeat(
    server_id: str,
    updates: dict[str, Any],
    offline_threshold_seconds: int,
) -> None:
    """Обрабатывает heartbeat одним соединением и одной транзакцией."""
    now_dt = datetime.now(timezone.utc)
    now_text = now_iso()
    with get_db() as conn:
        _record_gap_in_transaction(
            conn, server_id, now_dt, now_text, offline_threshold_seconds
        )
        _update_server_in_transaction(conn, server_id, updates, now_text)
        _resolve_events_in_transaction(conn, server_id, "agent_offline", None, now_text)


def _tracked_services_in_transaction(conn, server_id: str) -> set[str] | None:
    """Сервисы, заданные в настройках Hub; None — Hub составом не управляет (работает локальный конфиг агента)."""
    row = conn.execute(
        "SELECT config_json, config_version FROM server_agent_settings WHERE server_id = ?", (server_id,)
    ).fetchone()
    if not row or not row["config_version"]:
        return None
    try:
        config = json.loads(row["config_json"] or "{}")
    except ValueError:
        return None
    services = config.get("services") if isinstance(config, dict) else None
    return {str(name) for name in services} if isinstance(services, list) else None


def _save_service_catalog_in_transaction(conn, server_id: str, services: list[dict[str, Any]], now_text: str) -> None:
    conn.execute(
        """INSERT INTO server_service_catalog (server_id, services_json, updated_at)
           VALUES (?, ?, ?)
           ON CONFLICT(server_id) DO UPDATE SET
               services_json = excluded.services_json, updated_at = excluded.updated_at""",
        (server_id, json.dumps(services, ensure_ascii=False), now_text),
    )


def ingest_metrics_sample(
    server_id: str,
    metric: dict[str, Any],
    server_updates: dict[str, Any],
    disk_states: list[tuple[str, float | None]],
    service_states: list[tuple[str, str]],
    *,
    offline_threshold_seconds: int,
    disk_warning_threshold: float,
    disk_critical_threshold: float,
    disk_event_dedup_seconds: int,
    service_event_dedup_seconds: int,
    catalog: list[dict[str, Any]] | None = None,
) -> None:
    """Сохраняет метрики, состояние сервера и события одним транзакционным пакетом.

    catalog — список установленных сервисов (агент шлёт его раз в 10 минут). Если состав отслеживаемых
    сервисов задан в Hub, чужие замеры (агент ещё не применил настройки) отбрасываются."""
    now_dt = datetime.now(timezone.utc)
    now_text = now_iso()
    with get_db() as conn:
        tracked = _tracked_services_in_transaction(conn, server_id)
        if tracked is not None:
            service_states = [(name, status) for name, status in service_states if name in tracked]
        if catalog is not None:
            _save_service_catalog_in_transaction(conn, server_id, catalog, now_text)
        _record_gap_in_transaction(
            conn, server_id, now_dt, now_text, offline_threshold_seconds
        )
        _insert_metric_in_transaction(conn, server_id, metric, now_text)
        _update_server_in_transaction(conn, server_id, server_updates, now_text)
        _resolve_events_in_transaction(conn, server_id, "agent_offline", None, now_text)

        for mount, percent in disk_states:
            if percent is None:
                continue
            if percent >= disk_warning_threshold:
                severity = "critical" if percent >= disk_critical_threshold else "warning"
                _apply_event_change_in_transaction(
                    conn,
                    server_id,
                    "disk_threshold",
                    severity,
                    {"mount": mount, "usage": percent, "_dedup_key": mount},
                    resolve=False,
                    dedup_seconds=disk_event_dedup_seconds,
                    now_dt=now_dt,
                    now_text=now_text,
                )
            else:
                _apply_event_change_in_transaction(
                    conn,
                    server_id,
                    "disk_threshold",
                    "warning",
                    {"_dedup_key": mount},
                    resolve=True,
                    dedup_seconds=disk_event_dedup_seconds,
                    now_dt=now_dt,
                    now_text=now_text,
                )

        for service_name, status in service_states:
            _upsert_service_status_in_transaction(
                conn, server_id, service_name, status, now_text
            )
            if status in ("stopped", "failed"):
                severity = "critical" if status == "failed" else "warning"
                _apply_event_change_in_transaction(
                    conn,
                    server_id,
                    "service_down",
                    severity,
                    {"service": service_name, "status": status, "_dedup_key": service_name},
                    resolve=False,
                    dedup_seconds=service_event_dedup_seconds,
                    now_dt=now_dt,
                    now_text=now_text,
                )
            else:
                _apply_event_change_in_transaction(
                    conn,
                    server_id,
                    "service_down",
                    "warning",
                    {"_dedup_key": service_name},
                    resolve=True,
                    dedup_seconds=service_event_dedup_seconds,
                    now_dt=now_dt,
                    now_text=now_text,
                )


def delete_server(server_id: str) -> bool:
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM servers WHERE id = ?", (server_id,))
        return cursor.rowcount > 0


# ─────────────────────────── Настройки и обновление агента ───────────────────────────

def _settings_from_row(row: Any) -> dict[str, Any]:
    try:
        config = json.loads(row["config_json"] or "{}")
    except ValueError:
        config = {}
    return {
        "config": config if isinstance(config, dict) else {},
        "config_version": int(row["config_version"] or 0),
        "update_requested_at": row["update_requested_at"],
        "update_result": row["update_result"] or "",
        "update_detail": row["update_detail"] or "",
        "update_result_at": row["update_result_at"],
    }


def get_agent_settings(server_id: str) -> dict[str, Any]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM server_agent_settings WHERE server_id = ?", (server_id,)
        ).fetchone()
    if row:
        return _settings_from_row(row)
    return {"config": {}, "config_version": 0, "update_requested_at": None,
            "update_result": "", "update_detail": "", "update_result_at": None}


def save_agent_config(server_id: str, config: dict[str, Any]) -> dict[str, Any]:
    """Сохраняет настройки и увеличивает config_version — агент заметит это по heartbeat."""
    now = now_iso()
    with get_db() as conn:
        conn.execute(
            """INSERT INTO server_agent_settings (server_id, config_json, config_version, updated_at)
               VALUES (?, ?, 1, ?)
               ON CONFLICT(server_id) DO UPDATE SET
                   config_json = excluded.config_json,
                   config_version = server_agent_settings.config_version + 1,
                   updated_at = excluded.updated_at""",
            (server_id, json.dumps(config, ensure_ascii=False), now),
        )
        row = conn.execute(
            "SELECT * FROM server_agent_settings WHERE server_id = ?", (server_id,)
        ).fetchone()
    return _settings_from_row(row)


def request_agent_update(server_ids: list[str]) -> int:
    """Ставит флаг «обновись» — агент получит его в ответе на ближайший heartbeat."""
    now = now_iso()
    with get_db() as conn:
        for server_id in server_ids:
            conn.execute(
                """INSERT INTO server_agent_settings (server_id, update_requested_at, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(server_id) DO UPDATE SET
                       update_requested_at = excluded.update_requested_at,
                       updated_at = excluded.updated_at""",
                (server_id, now, now),
            )
    return len(server_ids)


def record_update_result(server_id: str, result: str, detail: str) -> None:
    """Результат попытки обновления от агента; заодно снимает флаг запроса."""
    now = now_iso()
    with get_db() as conn:
        conn.execute(
            """INSERT INTO server_agent_settings
                   (server_id, update_result, update_detail, update_result_at, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(server_id) DO UPDATE SET
                   update_requested_at = NULL,
                   update_result = excluded.update_result,
                   update_detail = excluded.update_detail,
                   update_result_at = excluded.update_result_at,
                   updated_at = excluded.updated_at""",
            (server_id, result, detail, now, now),
        )


# ─────────────────────────── Каталог сервисов и отслеживание ───────────────────────────

def save_service_catalog(server_id: str, services: list[dict[str, Any]]) -> None:
    with get_db() as conn:
        _save_service_catalog_in_transaction(conn, server_id, services, now_iso())


def get_service_catalog(server_id: str) -> tuple[list[dict[str, Any]], str | None]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT services_json, updated_at FROM server_service_catalog WHERE server_id = ?", (server_id,)
        ).fetchone()
    if not row:
        return [], None
    try:
        items = json.loads(row["services_json"] or "[]")
    except ValueError:
        items = []
    return (items if isinstance(items, list) else []), row["updated_at"]


def prune_services(server_id: str, keep: list[str]) -> None:
    """Сервис убрали из отслеживаемых: забываем его статус и закрываем его открытые события."""
    now = now_iso()
    with get_db() as conn:
        names = [r["service_name"] for r in conn.execute(
            "SELECT service_name FROM server_services_status WHERE server_id = ?", (server_id,)).fetchall()]
        stale = {n for n in names if n not in keep}
        for event in conn.execute(
            "SELECT payload FROM server_events WHERE server_id = ? AND type = 'service_down' AND resolved_at IS NULL",
            (server_id,),
        ).fetchall():
            try:
                stale.add(json.loads(event["payload"] or "{}").get("_dedup_key") or "")
            except ValueError:
                continue
        for name in stale:
            if not name or name in keep:
                continue
            conn.execute("DELETE FROM server_services_status WHERE server_id = ? AND service_name = ?", (server_id, name))
            _resolve_events_in_transaction(conn, server_id, "service_down", name, now)


def get_resolved_events_since(type_: str, since_iso: str) -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM server_events WHERE type = ? AND resolved_at IS NOT NULL AND resolved_at >= ? "
            "ORDER BY resolved_at DESC",
            (type_, since_iso),
        ).fetchall()
        return [dict(r) for r in rows]


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
    now_text = now_iso()
    with get_db() as conn:
        _insert_metric_in_transaction(conn, server_id, data, now_text)


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
    now_text = now_iso()
    with get_db() as conn:
        _upsert_service_status_in_transaction(conn, server_id, name, status, now_text)


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
    now_text = now_iso()
    with get_db() as conn:
        return _create_event_in_transaction(
            conn, server_id, type_, severity, payload, now_text=now_text
        )


def find_recent_unresolved_event(
    server_id: str, type_: str, dedup_key: str | None, within_seconds: int
) -> dict[str, Any] | None:
    """Ищет недавнее открытое событие того же типа и контекста."""
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=within_seconds)).strftime(
        "%Y-%m-%dT%H:%M:%S.%f"
    )[:-3] + "Z"
    with get_db() as conn:
        return _find_recent_unresolved_event_in_transaction(
            conn, server_id, type_, dedup_key, cutoff
        )


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
    """Закрывает открытые события; общий случай выполняется одним UPDATE."""
    with get_db() as conn:
        _resolve_events_in_transaction(conn, server_id, type_, dedup_key, now_iso())


# ─────────────────────────── Аптайм и открытые события ───────────────────────────

def add_downtime(server_id: str, start_iso: str, end_iso: str) -> None:
    """Записывает уже закончившийся простой задним числом."""
    with get_db() as conn:
        _create_event_in_transaction(
            conn,
            server_id,
            "agent_offline",
            "critical",
            {"last_seen": start_iso, "retro": True},
            created_at=start_iso,
            resolved_at=end_iso,
            now_text=end_iso,
        )


def has_unresolved_event(server_id: str, type_: str) -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM server_events WHERE server_id = ? AND type = ? AND resolved_at IS NULL LIMIT 1",
            (server_id, type_),
        ).fetchone()
        return row is not None


def get_events_overlapping(type_: str, since_iso: str) -> list[dict[str, Any]]:
    """События типа type_, не закончившиеся раньше since_iso (открытые или закрытые позже)."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM server_events WHERE type = ? AND (resolved_at IS NULL OR resolved_at >= ?)",
            (type_, since_iso),
        ).fetchall()
        return [dict(r) for r in rows]


def get_open_events(type_: str) -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM server_events WHERE type = ? AND resolved_at IS NULL ORDER BY created_at DESC",
            (type_,),
        ).fetchall()
        return [dict(r) for r in rows]
