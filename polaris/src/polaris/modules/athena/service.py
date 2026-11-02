"""Задачи для Athena: тонкая обёртка над модулем todo (отдельной таблицы нет)."""
from __future__ import annotations

from typing import Any

from polaris.modules.athena import logic
from polaris.modules.todo import service as todo
from polaris.modules.todo.models import TaskCreate


def _out(task: Any) -> dict[str, Any]:
    data = task.model_dump() if hasattr(task, "model_dump") else dict(task)
    data.pop("project_color", None)
    data.pop("remind_at", None)
    data["important"] = logic.is_important(data)
    return data


def _all_tasks() -> list[dict[str, Any]]:
    return [_out(t) for t in todo.get_full_task_list().tasks]


def list_tasks(
    *,
    important_only: bool = False,
    status: str = "active",
    priority: str | None = None,
    project: str | None = None,
    tag: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    tasks = logic.filter_tasks(
        _all_tasks(),
        status="active" if important_only else status,
        priority=priority,
        project=project,
        tag=tag,
        date_from=date_from,
        date_to=date_to,
    )
    if important_only:
        tasks = [t for t in tasks if t["important"]]
    return {"items": logic.paginate(tasks, limit, offset), "total": len(tasks), "limit": limit, "offset": offset}


def get_task(task_id: str) -> dict[str, Any] | None:
    task = todo.get_task(task_id)
    return _out(task) if task else None


def create_task(payload: dict[str, Any]) -> dict[str, Any]:
    projects = [p.model_dump() for p in todo.get_full_task_list().projects]
    normalized = logic.build_create_payload(payload, projects)
    created = todo.create_task(TaskCreate(**normalized))
    return _out(created)
