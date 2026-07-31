from __future__ import annotations

"""Repository для агрегации сущностей в события календаря.

Календарь не хранит свои данные — он проецирует существующие.
Этот слой знает, как собрать события из разных модулей и отфильтровать
по диапазону дат.
"""

from datetime import date, datetime, timedelta
from typing import Any

from polaris.infra.database import get_db
from polaris.modules.calendar.models import CalendarEvent


def _month_range(year: int, month: int):
    """Вернёт (start_date, end_date) для полного месяца."""
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    return start, end


def _parse_date_str(date_str: str) -> date | None:
    """Разобрать YYYY-MM-DD в date объект."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _row_to_task_event(row: dict[str, Any]) -> CalendarEvent | None:
    """Превратить строку задачи в событие (только если есть дата)."""
    if not row.get("date"):
        return None
    return CalendarEvent.from_task(row)


def _row_to_reminder_event(row: dict[str, Any]) -> CalendarEvent | None:
    """Превратить строку напоминания (remind_at) в событие."""
    remind_at = row.get("remind_at", "")
    if not remind_at:
        return None
    try:
        dt = datetime.fromisoformat(remind_at.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    # Дата напоминания
    row_copy = dict(row)
    row_copy["date"] = dt.strftime("%Y-%m-%d")
    row_copy["time"] = dt.strftime("%H:%M")
    return CalendarEvent.from_reminder(row_copy)


def _expand_recurring_task(row: dict[str, Any], month_start: date, month_end: date) -> list[CalendarEvent]:
    """Развернуть повторяющуюся задачу на все дни месяца.

    Задача хранится один раз, а в календаре отображается на каждую дату,
    когда она должна сработать (daily/weekly/monthly/yearly).
    Повторение считается от даты старта задачи (``date``).
    """
    task_date_str = row.get("date", "")
    repeat = row.get("repeat", "never")
    if not task_date_str or repeat in ("never", "custom"):
        return []

    try:
        task_date = datetime.strptime(task_date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return []

    if task_date > month_end:
        return []

    result: list[CalendarEvent] = []
    current = month_start

    while current <= month_end:
        if current < task_date:
            current += timedelta(days=1)
            continue

        occurs = False
        if repeat == "daily":
            occurs = True
        elif repeat == "weekly":
            occurs = task_date.weekday() == current.weekday()
        elif repeat == "monthly":
            occurs = task_date.day == current.day
        elif repeat == "yearly":
            occurs = task_date.month == current.month and task_date.day == current.day
        else:
            occurs = current == task_date

        if occurs:
            row_copy = dict(row)
            row_copy["date"] = current.isoformat()
            ev = _row_to_task_event(row_copy)
            if ev:
                result.append(ev)

        current += timedelta(days=1)

    return result


def get_events_for_month(year: int, month: int) -> list[CalendarEvent]:
    """Получить все события, попадающие в указанный месяц.

    Источники:
      • задачи с ``date`` в месяце
      • задачи с ``remind_at`` в месяце (как отдельные напоминания)
      • платёжные подписки / платежи (если есть таблица)
    """
    start, end = _month_range(year, month)
    start_str = start.isoformat()
    end_str = end.isoformat()

    events: list[CalendarEvent] = []

    with get_db() as conn:
        # ── Задачи с датой в месяце ──
        rows = conn.execute(
            "SELECT * FROM tasks WHERE date != '' AND date >= ? AND date <= ?",
            (start_str, end_str),
        ).fetchall()
        for row in rows:
            ev = _row_to_task_event(dict(row))
            if ev:
                events.append(ev)

        # Recurring tasks that start before this month but fire inside it.
        recur_rows = conn.execute(
            "SELECT * FROM tasks WHERE date != '' AND repeat != 'never' AND repeat != 'custom' AND date <= ?",
            (end_str,),
        ).fetchall()
        for row in recur_rows:
            events.extend(_expand_recurring_task(dict(row), start, end))

        # ── Напоминания (remind_at) в месяце ──
        rows = conn.execute(
            "SELECT * FROM tasks WHERE remind_at != '' "
            "AND substr(remind_at, 1, 10) >= ? AND substr(remind_at, 1, 10) <= ?",
            (start_str, end_str),
        ).fetchall()
        for row in rows:
            ev = _row_to_reminder_event(dict(row))
            if ev:
                events.append(ev)

        # ── Платёжные подписки (таблицы могут быть ещё не созданы) ──
        # Payments
        try:
            pay_rows = conn.execute(
                "SELECT * FROM payments WHERE due_date != '' "
                "AND due_date >= ? AND due_date <= ?",
                (start_str, end_str),
            ).fetchall()
            for row in pay_rows:
                ev = CalendarEvent.from_payment(dict(row))
                events.append(ev)
        except Exception:
            pass  # таблица payments пока не существует

        # Subscriptions
        try:
            sub_rows = conn.execute(
                "SELECT * FROM subscriptions WHERE next_billing_date != '' "
                "AND next_billing_date >= ? AND next_billing_date <= ?",
                (start_str, end_str),
            ).fetchall()
            for row in sub_rows:
                ev = CalendarEvent.from_subscription(dict(row))
                events.append(ev)
        except Exception:
            pass  # таблица subscriptions пока не существует

        # ── Произвольные события (если есть таблица events) ──
        try:
            evt_rows = conn.execute(
                "SELECT * FROM events WHERE date != '' "
                "AND date >= ? AND date <= ?",
                (start_str, end_str),
            ).fetchall()
            for row in evt_rows:
                ev = CalendarEvent.from_event(dict(row))
                events.append(ev)
        except Exception:
            pass

    return events


def _singleton_recurring_event(row: dict[str, Any], day: date) -> CalendarEvent | None:
    """Один экземпляр повторяющейся задачи на конкретную дату, если она выпадает."""
    task_date_str = row.get("date", "")
    repeat = row.get("repeat", "never")
    if not task_date_str or repeat in ("never", "custom"):
        return None
    if day < _parse_date_str(task_date_str):
        return None

    try:
        task_date = datetime.strptime(task_date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None

    occurs = False
    if repeat == "daily":
        occurs = True
    elif repeat == "weekly":
        occurs = task_date.weekday() == day.weekday()
    elif repeat == "monthly":
        occurs = task_date.day == day.day
    elif repeat == "yearly":
        occurs = task_date.month == day.month and task_date.day == day.day
    else:
        occurs = day == task_date

    if not occurs:
        return None

    row_copy = dict(row)
    row_copy["date"] = day.isoformat()
    return _row_to_task_event(row_copy)


def get_events_for_day(day: date) -> list[CalendarEvent]:
    """Получить все события на конкретный день."""
    day_str = day.isoformat()
    events: list[CalendarEvent] = []

    with get_db() as conn:
        # Задачи
        rows = conn.execute(
            "SELECT * FROM tasks WHERE date = ?", (day_str,)
        ).fetchall()
        for row in rows:
            ev = _row_to_task_event(dict(row))
            if ev:
                events.append(ev)

        # Повторяющиеся задачи: проверяем, выпадает ли день в серию repeat.
        recur_rows = conn.execute(
            "SELECT * FROM tasks WHERE date != '' AND date <= ? "
            "AND repeat != 'never' AND repeat != 'custom'",
            (day_str,),
        ).fetchall()
        for row in recur_rows:
            ev = _singleton_recurring_event(dict(row), day)
            if ev:
                events.append(ev)

        # Напоминания
        rows = conn.execute(
            "SELECT * FROM tasks WHERE remind_at != '' "
            "AND substr(remind_at, 1, 10) = ?",
            (day_str,),
        ).fetchall()
        for row in rows:
            ev = _row_to_reminder_event(dict(row))
            if ev:
                events.append(ev)

        # Payments
        try:
            pay_rows = conn.execute(
                "SELECT * FROM payments WHERE due_date = ?", (day_str,)
            ).fetchall()
            for row in pay_rows:
                ev = CalendarEvent.from_payment(dict(row))
                events.append(ev)
        except Exception:
            pass

        # Subscriptions
        try:
            sub_rows = conn.execute(
                "SELECT * FROM subscriptions WHERE next_billing_date = ?", (day_str,)
            ).fetchall()
            for row in sub_rows:
                ev = CalendarEvent.from_subscription(dict(row))
                events.append(ev)
        except Exception:
            pass

        # Events table
        try:
            evt_rows = conn.execute(
                "SELECT * FROM events WHERE date = ?", (day_str,)
            ).fetchall()
            for row in evt_rows:
                ev = CalendarEvent.from_event(dict(row))
                events.append(ev)
        except Exception:
            pass

    events.sort(key=lambda e: e.sort_key())
    return events
