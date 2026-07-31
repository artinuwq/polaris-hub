from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from polaris.infra.database import get_db


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def generate_uuid() -> str:
    return str(uuid.uuid4())


def get_all_events() -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM events ORDER BY date ASC, time ASC"
        ).fetchall()
        return [dict(row) for row in rows]


def get_event_by_id(event_id: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM events WHERE id = ?", (event_id,)
        ).fetchone()
        return dict(row) if row else None


def create_event(data: dict[str, Any]) -> dict[str, Any]:
    event_id = generate_uuid()
    now = now_iso()

    with get_db() as conn:
        conn.execute(
            """INSERT INTO events
               (id, title, description, date, time, end_time,
                project, project_color, tags, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                data.get("title", ""),
                data.get("description", ""),
                data.get("date", ""),
                data.get("time", ""),
                data.get("end_time", ""),
                data.get("project", ""),
                data.get("project_color", ""),
                json.dumps(data.get("tags", [])),
                now,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return dict(row)


def update_event(event_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
    existing = get_event_by_id(event_id)
    if not existing:
        return None

    updates = []
    for field in ("title", "description", "date", "time", "end_time", "project", "project_color"):
        if field in data:
            updates.append((field, data[field]))

    if "tags" in data:
        updates.append(("tags", json.dumps(data["tags"])))

    updates.append(("updated_at", now_iso()))

    set_clause = ", ".join(f"{field} = ?" for field, _ in updates)
    values = [val for _, val in updates] + [event_id]

    with get_db() as conn:
        conn.execute(f"UPDATE events SET {set_clause} WHERE id = ?", values)
        row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return dict(row) if row else None


def delete_event(event_id: str) -> bool:
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
        return cursor.rowcount > 0
