"""Чистая логика задач для Athena: валидация, фильтры, сортировка.

Здесь нет ни FastAPI, ни Pydantic, ни базы — поэтому это легко проверять тестами.
Работаем с обычными словарями.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

PRIORITIES = ("fire", "important", "normal", "someday")
STATUSES = ("todo", "in-progress", "waiting", "done")
ENERGIES = ("quick", "medium", "large")
REPEATS = ("never", "daily", "weekly", "monthly", "yearly", "custom")
IMPORTANT_PRIORITIES = ("fire", "important")

MAX_TAGS = 20
MAX_TAG_LEN = 40
MAX_CHECKLIST = 50
MAX_ITEM_LEN = 300

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^\d{2}:\d{2}$")


class ValidationFailed(ValueError):
    """Понятное сообщение для агента: что именно не так в запросе."""


def validate_date(value: str, field: str = "date") -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if not _DATE_RE.match(value):
        raise ValidationFailed(f"{field}: ожидается формат YYYY-MM-DD, получено «{value}»")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValidationFailed(f"{field}: такой даты не существует («{value}»)") from exc
    return value


def validate_time(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if not _TIME_RE.match(value):
        raise ValidationFailed(f"time: ожидается формат HH:MM, получено «{value}»")
    try:
        datetime.strptime(value, "%H:%M")
    except ValueError as exc:
        raise ValidationFailed(f"time: такого времени не существует («{value}»)") from exc
    return value


def validate_choice(value: str, allowed: tuple[str, ...], field: str) -> str:
    if value not in allowed:
        raise ValidationFailed(f"{field}: допустимо {', '.join(allowed)}; получено «{value}»")
    return value


def clean_tags(tags: list[str]) -> list[str]:
    """Убирает #, пробелы и дубли, сохраняя порядок."""
    result: list[str] = []
    for raw in tags or []:
        tag = str(raw).strip().lstrip("#").strip()
        if not tag:
            continue
        if len(tag) > MAX_TAG_LEN:
            raise ValidationFailed(f"tags: тег длиннее {MAX_TAG_LEN} символов")
        if tag not in result:
            result.append(tag)
    if len(result) > MAX_TAGS:
        raise ValidationFailed(f"tags: не больше {MAX_TAGS} тегов")
    return result


def clean_checklist(items: list[str]) -> list[dict[str, Any]]:
    result = []
    for raw in items or []:
        text = str(raw).strip()
        if not text:
            continue
        if len(text) > MAX_ITEM_LEN:
            raise ValidationFailed(f"checklist: пункт длиннее {MAX_ITEM_LEN} символов")
        result.append({"text": text, "done": False})
    if len(result) > MAX_CHECKLIST:
        raise ValidationFailed(f"checklist: не больше {MAX_CHECKLIST} пунктов")
    return result


def build_create_payload(data: dict[str, Any], projects: list[dict[str, Any]]) -> dict[str, Any]:
    """Проверяет запрос на создание задачи и приводит его к виду, который понимает модуль todo."""
    title = str(data.get("title") or "").strip()
    if not title:
        raise ValidationFailed("title: название не может быть пустым")
    if len(title) > 500:
        raise ValidationFailed("title: не длиннее 500 символов")

    project = str(data.get("project") or "").strip()
    color = ""
    if project:
        match = next((p for p in projects if str(p.get("name", "")).lower() == project.lower()), None)
        if match:
            project, color = match["name"], match.get("color", "")  # берём написание и цвет существующего проекта

    status = validate_choice(data.get("status") or "todo", STATUSES, "status")
    return {
        "title": title,
        "description": str(data.get("description") or "").strip(),
        "date": validate_date(data.get("date") or ""),
        "time": validate_time(data.get("time") or ""),
        "repeat": validate_choice(data.get("repeat") or "never", REPEATS, "repeat"),
        "priority": validate_choice(data.get("priority") or "normal", PRIORITIES, "priority"),
        "status": status,
        "energy": validate_choice(data.get("energy") or "medium", ENERGIES, "energy"),
        "tags": clean_tags(data.get("tags") or []),
        "project": project,
        "project_color": color,
        "checklist": clean_checklist(data.get("checklist") or []),
    }


def is_important(task: dict[str, Any]) -> bool:
    """Важная = срочная или важная по приоритету и ещё не выполнена."""
    return task.get("priority") in IMPORTANT_PRIORITIES and task.get("status") != "done"


def urgency_key(task: dict[str, Any]) -> tuple:
    """Сортировка по срочности: сначала с ближайшей датой, затем без даты;
    внутри одной даты — по времени, затем по приоритету."""
    priority = PRIORITIES.index(task["priority"]) if task.get("priority") in PRIORITIES else len(PRIORITIES)
    date = task.get("date") or ""
    return (0 if date else 1, date, task.get("time") or "99:99", priority, task.get("created_at") or "")


def filter_tasks(
    tasks: list[dict[str, Any]],
    *,
    status: str = "active",
    priority: str | None = None,
    project: str | None = None,
    tag: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    """status: active (всё, кроме выполненных; по умолчанию), all, либо один конкретный статус."""
    if status not in ("active", "all", *STATUSES):
        raise ValidationFailed(f"status: допустимо active, all, {', '.join(STATUSES)}")
    if priority is not None:
        validate_choice(priority, PRIORITIES, "priority")
    date_from = validate_date(date_from or "", "date_from") or None
    date_to = validate_date(date_to or "", "date_to") or None

    result = []
    for task in tasks:
        if status == "active" and task.get("status") == "done":
            continue
        if status not in ("active", "all") and task.get("status") != status:
            continue
        if priority and task.get("priority") != priority:
            continue
        if project and str(task.get("project", "")).lower() != project.lower():
            continue
        if tag and tag.lstrip("#").lower() not in [str(t).lower() for t in task.get("tags", [])]:
            continue
        if date_from or date_to:
            date = task.get("date") or ""
            if not date:
                continue  # задачи без даты в выборку по датам не попадают
            if date_from and date < date_from:
                continue
            if date_to and date > date_to:
                continue
        result.append(task)
    return sorted(result, key=urgency_key)


def paginate(items: list[Any], limit: int, offset: int) -> list[Any]:
    return items[offset : offset + limit]
