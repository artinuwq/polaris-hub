from __future__ import annotations

import json
from typing import Any

from polaris.modules.todo.models import (
    ChecklistItem,
    ProjectResponse,
    TagResponse,
    TaskCreate,
    TaskListResponse,
    TaskResponse,
    TaskUpdate,
)
from polaris.modules.todo import repository as repo


def get_full_task_list() -> TaskListResponse:
    """Get all tasks, projects, and tags."""
    tasks = [TaskResponse.from_db_row(row) for row in repo.get_all_tasks()]
    projects = [ProjectResponse.from_db_row(row) for row in repo.get_all_projects()]
    tags = [TagResponse.from_db_row(row) for row in repo.get_all_tags()]
    return TaskListResponse(tasks=tasks, projects=projects, tags=tags)


def create_task(data: TaskCreate) -> TaskResponse:
    """Create a new task."""
    payload = data.model_dump()
    # Serialize checklist items
    if "checklist" in payload and payload["checklist"]:
        payload["checklist"] = [
            {"text": item.text, "done": item.done} for item in payload["checklist"]
        ]
    else:
        payload["checklist"] = []

    row = repo.create_task(payload)
    return TaskResponse.from_db_row(row)


def get_task(task_id: str) -> TaskResponse | None:
    """Get a single task by ID."""
    row = repo.get_task_by_id(task_id)
    if not row:
        return None
    return TaskResponse.from_db_row(row)


def update_task(task_id: str, data: TaskUpdate) -> TaskResponse | None:
    """Update a task. Only include non-None fields."""
    payload = data.model_dump(exclude_none=True)

    # Serialize checklist
    if "checklist" in payload:
        payload["checklist"] = [
            {"text": item.text, "done": item.done} for item in payload["checklist"]
        ]

    row = repo.update_task(task_id, payload)
    if not row:
        return None
    return TaskResponse.from_db_row(row)


def delete_task(task_id: str) -> bool:
    """Delete a task by ID."""
    return repo.delete_task(task_id)


def create_project(name: str, color: str = "#5ab8ff") -> ProjectResponse:
    """Create a new project."""
    row = repo.create_project(name, color)
    return ProjectResponse.from_db_row(row)


def create_tag(name: str) -> TagResponse:
    """Create a new tag."""
    row = repo.create_tag(name)
    return TagResponse.from_db_row(row)