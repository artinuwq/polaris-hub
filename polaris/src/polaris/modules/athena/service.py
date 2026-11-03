"""Задачи для Athena: тонкая обёртка над модулем todo (отдельной таблицы нет)."""
from __future__ import annotations

import calendar as _calendar
from datetime import date, datetime
from typing import Any

from polaris.modules.athena import logic
from polaris.modules.calendar import repository as calendar_repo
from polaris.modules.finance import repository as finance_repo
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


# ───────────────────────── Календарь ─────────────────────────


def _today() -> date:
    return datetime.now().date()


def _payments_by_id() -> dict[str, dict[str, Any]]:
    try:
        return {str(p["id"]): p for p in finance_repo.get_all_payments()}
    except Exception:  # noqa: BLE001 — таблицы платежей может не быть
        return {}


def _convert(events: list[Any]) -> list[dict[str, Any]]:
    payments = _payments_by_id()
    result = []
    for ev in events:
        raw = ev.model_dump()
        raw["type"] = ev.type.value
        result.append(logic.event_out(raw, payments))
    return result


def _select(
    events: list[dict[str, Any]], types: set[str] | None, important_only: bool, include_done: bool
) -> list[dict[str, Any]]:
    return logic.sort_events(
        logic.dedupe_events(
            logic.filter_events(events, types=types, important_only=important_only, include_done=include_done)
        )
    )


def calendar_day(date_str: str, *, types=None, important_only=False, include_done=False) -> dict[str, Any]:
    """Всё, что запланировано на день: события, задачи на этот день, напоминания, платежи."""
    day = logic.validate_date(date_str, "date")
    if not day:
        raise logic.ValidationFailed("date: обязательный параметр, формат YYYY-MM-DD")
    events = _select(_convert(calendar_repo.get_events_for_day(date.fromisoformat(day))), types, important_only, include_done)
    return {"date": day, "total": len(events), "events": events}


def calendar_month(year: int, month: int, *, types=None, important_only=False, include_done=False) -> dict[str, Any]:
    first = date(year, month, 1)
    last = date(year, month, _calendar.monthrange(year, month)[1])
    events = _select(_convert(calendar_repo.get_events_for_month(year, month)), types, important_only, include_done)
    events = logic.in_range(events, first, last)
    return {"start": first.isoformat(), "end": last.isoformat(), "total": len(events), "days": logic.group_by_day(events)}


def _window_events(start: date, end: date) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    for year, month in logic.months_between(start, end):
        collected.extend(_convert(calendar_repo.get_events_for_month(year, month)))
    return logic.in_range(collected, start, end)


def calendar_upcoming(days: int = 30, *, types=None, important_only=False, include_done=False) -> dict[str, Any]:
    """Следующие N дней начиная с сегодня (сегодняшние уже прошедшие по времени не показываются)."""
    start, end = logic.upcoming_window(_today(), days)
    events = _select(_window_events(start, end), types, important_only, include_done)
    now = datetime.now()
    events = logic.drop_past_today(events, start.isoformat(), now.strftime("%H:%M"))
    return {"start": start.isoformat(), "end": end.isoformat(), "total": len(events), "days": logic.group_by_day(events)}


def calendar_next(limit: int = 5, *, types=None, important_only=False, include_done=False) -> dict[str, Any]:
    """Ближайшие события одним списком: что случится дальше (смотрим на 60 дней вперёд)."""
    start, end = logic.upcoming_window(_today(), 60)
    events = _select(_window_events(start, end), types, important_only, include_done)
    events = logic.drop_past_today(events, start.isoformat(), datetime.now().strftime("%H:%M"))
    return {"total": len(events), "events": events[:limit]}

