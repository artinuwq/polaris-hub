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


# ─── Tasks ───


def get_all_tasks() -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY created_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]


def get_task_by_id(task_id: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return dict(row) if row else None


def create_task(data: dict[str, Any]) -> dict[str, Any]:
    task_id = data.get("id", generate_uuid())
    now = now_iso()

    with get_db() as conn:
        conn.execute(
            """INSERT INTO tasks
               (id, title, description, date, time, repeat, priority, status, energy,
                tags, project, project_color, checklist, remind_at, created_at, updated_at, done_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id,
                data.get("title", ""),
                data.get("description", ""),
                data.get("date", ""),
                data.get("time", ""),
                data.get("repeat", "never"),
                data.get("priority", "normal"),
                data.get("status", "todo"),
                data.get("energy", "medium"),
                json.dumps(data.get("tags", [])),
                data.get("project", ""),
                data.get("project_color", ""),
                json.dumps(data.get("checklist", [])),
                data.get("remind_at", ""),
                now,
                now,
                "",
            ),
        )
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row)


def update_task(task_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
    existing = get_task_by_id(task_id)
    if not existing:
        return None

    now = now_iso()
    updates = []

    for field in ("title", "description", "date", "time", "repeat", "priority",
                   "status", "energy", "project", "project_color", "remind_at"):
        if field in data:
            updates.append((field, data[field]))

    if "tags" in data:
        updates.append(("tags", json.dumps(data["tags"])))

    if "checklist" in data:
        raw = data["checklist"]
        # checklist items can be dicts or ChecklistItem objects
        serialized = json.dumps([
            {"text": item["text"] if isinstance(item, dict) else item.text,
             "done": item.get("done", False) if isinstance(item, dict) else item.done}
            for item in raw
        ])
        updates.append(("checklist", serialized))

    # Auto-set done_at
    if data.get("status") == "done" and not existing.get("done_at"):
        updates.append(("done_at", now))
        updates.append(("status", "done"))
    elif "status" in data and data["status"] != "done":
        updates.append(("done_at", ""))
        updates.append(("status", data["status"]))

    updates.append(("updated_at", now))

    set_clause = ", ".join(f"{field} = ?" for field, _ in updates)
    values = [val for _, val in updates] + [task_id]

    with get_db() as conn:
        conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", values)
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row) if row else None


def delete_task(task_id: str) -> bool:
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        return cursor.rowcount > 0


def get_upcoming_reminders() -> list[dict[str, Any]]:
    """Get tasks that have a remind_at in the past and are not done."""
    now = now_iso()
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE remind_at != '' AND remind_at <= ? AND status != 'done'",
            (now,),
        ).fetchall()
        return [dict(row) for row in rows]


# ─── Projects ───


def get_all_projects() -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM projects ORDER BY name ASC"
        ).fetchall()
        return [dict(row) for row in rows]


def create_project(name: str, color: str = "#5ab8ff") -> dict[str, Any]:
    project_id = generate_uuid()
    now = now_iso()
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO projects (id, name, color, created_at) VALUES (?, ?, ?, ?)",
            (project_id, name, color, now),
        )
        row = conn.execute(
            "SELECT * FROM projects WHERE name = ?", (name,)
        ).fetchone()
        return dict(row) if row else {"id": project_id, "name": name, "color": color, "created_at": now}


# ─── Tags ───


def get_all_tags() -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM tags ORDER BY name ASC"
        ).fetchall()
        return [dict(row) for row in rows]


def create_tag(name: str) -> dict[str, Any]:
    tag_id = generate_uuid()
    now = now_iso()
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO tags (id, name, created_at) VALUES (?, ?, ?)",
            (tag_id, name, now),
        )
        row = conn.execute(
            "SELECT * FROM tags WHERE name = ?", (name,)
        ).fetchone()
        return dict(row) if row else {"id": tag_id, "name": name, "created_at": now}


# ─── Reminders ───


def _parse_iso(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _sync_remind_at(conn, task_id: str) -> None:
    """tasks.remind_at хранит «главное» напоминание задачи — его используют календарь и
    лента внимания. Это ближайшее будущее, а если будущих нет, самое позднее из прошедших."""
    rows = conn.execute("SELECT remind_at FROM task_reminders WHERE task_id = ?", (task_id,)).fetchall()
    parsed = [(dt, r["remind_at"]) for r in rows if (dt := _parse_iso(r["remind_at"]))]
    now = datetime.now(timezone.utc)
    future = sorted(p for p in parsed if p[0] >= now)
    past = sorted(p for p in parsed if p[0] < now)
    main = future[0][1] if future else (past[-1][1] if past else "")
    conn.execute("UPDATE tasks SET remind_at = ? WHERE id = ?", (main, task_id))


def list_reminders(task_id: str) -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, task_id, remind_at, created_at FROM task_reminders WHERE task_id = ?",
            (task_id,),
        ).fetchall()
    items = [dict(r) for r in rows]
    items.sort(key=lambda r: (_parse_iso(r["remind_at"]) or datetime.max.replace(tzinfo=timezone.utc)))
    return items


def add_reminder(task_id: str, remind_at: str) -> dict[str, Any]:
    reminder = {"id": generate_uuid(), "task_id": task_id, "remind_at": remind_at, "created_at": now_iso()}
    with get_db() as conn:
        conn.execute(
            "INSERT INTO task_reminders (id, task_id, remind_at, created_at) VALUES (?, ?, ?, ?)",
            (reminder["id"], task_id, remind_at, reminder["created_at"]),
        )
        _sync_remind_at(conn, task_id)
    return reminder


def delete_reminder(task_id: str, reminder_id: str) -> bool:
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM task_reminders WHERE id = ? AND task_id = ?", (reminder_id, task_id)
        )
        if cursor.rowcount:
            _sync_remind_at(conn, task_id)
        return cursor.rowcount > 0
