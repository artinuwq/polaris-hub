"""Задачи для Athena: тонкая обёртка над модулем todo (отдельной таблицы нет)."""
from __future__ import annotations

import calendar as _calendar
from datetime import date, datetime
from typing import Any

from polaris.modules.athena import logic
from polaris.modules.calendar import repository as calendar_repo
from polaris.modules.finance import repository as finance_repo
from polaris.modules.events import service as events
from polaris.modules.events.models import EventCreate, EventUpdate
from polaris.modules.todo import service as todo
from polaris.modules.todo.models import TaskCreate, TaskUpdate


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


def _projects() -> list[dict[str, Any]]:
    return [p.model_dump() for p in todo.get_full_task_list().projects]


def update_task(task_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """Частичное изменение. None — задачи нет. Статус `done` выставляет и время завершения (как в приложении)."""
    if todo.get_task(task_id) is None:
        return None
    normalized = logic.build_update_payload(payload, _projects())
    updated = todo.update_task(task_id, TaskUpdate(**normalized))
    return _out(updated) if updated else None


def complete_task(task_id: str) -> dict[str, Any] | None:
    """Отметить выполненной (повторно — безопасно: время завершения не затирается)."""
    return update_task(task_id, {"status": "done"})


def delete_task(task_id: str) -> bool:
    return todo.delete_task(task_id)


# ─── напоминания задачи ───


def _reminder_out(row: dict[str, Any]) -> dict[str, Any]:
    return {"id": row["id"], "remind_at": row["remind_at"], "created_at": row["created_at"]}


def list_reminders(task_id: str) -> list[dict[str, Any]] | None:
    rows = todo.list_reminders(task_id)
    return None if rows is None else [_reminder_out(r) for r in rows]


def add_reminder(task_id: str, remind_at: str) -> dict[str, Any] | None:
    """Добавить напоминание (время без пояса — время сервера). None — задачи нет."""
    existing = todo.list_reminders(task_id)
    if existing is None:
        return None
    when = logic.normalize_remind_at(remind_at)
    if len(existing) >= logic.MAX_REMINDERS:
        raise logic.ValidationFailed(f"у задачи уже {logic.MAX_REMINDERS} напоминаний — это предел")
    created = todo.add_reminder(task_id, when)
    return _reminder_out(created) if created else None


def delete_reminder(task_id: str, reminder_id: str) -> bool:
    return todo.delete_reminder(task_id, reminder_id)


# ─── события календаря ───


def _event_out(ev: Any) -> dict[str, Any]:
    data = ev.model_dump() if hasattr(ev, "model_dump") else dict(ev)
    data.pop("project_color", None)
    return data


def list_events(*, date_from: str | None = None, date_to: str | None = None, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """События (только «настоящие», без задач и платежей), по дате и времени; записи без времени — после остальных."""
    start, end = logic.validate_date(date_from or "", "date_from"), logic.validate_date(date_to or "", "date_to")
    items = [_event_out(e) for e in events.get_all_events()]
    items = [e for e in items if (not start or e["date"] >= start) and (not end or e["date"] <= end)]
    items.sort(key=lambda e: (e["date"], e["time"] == "", e["time"], e["title"].lower()))
    return {"items": logic.paginate(items, limit, offset), "total": len(items), "limit": limit, "offset": offset}


def get_event(event_id: str) -> dict[str, Any] | None:
    ev = events.get_event(event_id)
    return _event_out(ev) if ev else None


def create_event(payload: dict[str, Any]) -> dict[str, Any]:
    return _event_out(events.create_event(EventCreate(**logic.build_event_payload(payload, _projects()))))


def update_event(event_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    existing = events.get_event(event_id)
    if existing is None:
        return None
    normalized = logic.build_event_update(payload, _projects(), existing.model_dump())
    updated = events.update_event(event_id, EventUpdate(**normalized))
    return _event_out(updated) if updated else None


def delete_event(event_id: str) -> bool:
    return events.delete_event(event_id)


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

