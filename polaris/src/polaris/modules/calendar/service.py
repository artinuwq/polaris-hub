from __future__ import annotations

"""Сервисный слой календаря.

Сервис — это слой оркестровки. Он использует репозиторий для получения
сырых сущноестей и возвращает уже пригодные для API ответы.
Сервис НЕ знает о SQL и НЕ хранит данные — он только преобразует.

Архитектурно сервис вынесен отдельно от репозитория, чтобы в будущем
добавить кэширование, бизнес-правила и т.д. без изменения слоя доступа.
"""

from datetime import date, datetime, timedelta
from typing import Any, Optional

from polaris.modules.calendar.models import CalendarEvent, CalendarView
from polaris.modules.calendar import repository as repo


def _today() -> date:
    return datetime.now().date()


def get_month_events(year: int, month: int) -> CalendarView:
    """Вернуть все события для месяца в виде CalendarView."""
    events = repo.get_events_for_month(year, month)
    return CalendarView(month=month, year=year, events=events)


def get_day_events(date_str: str) -> list[CalendarEvent]:
    """Вернуть события для конкретного дня (YYYY-MM-DD)."""
    day = _parse_date(date_str)
    if day is None:
        return []
    return repo.get_events_for_day(day)


def get_today_events() -> list[CalendarEvent]:
    """События на сегодня."""
    return repo.get_events_for_day(_today())


def _parse_date(date_str: str) -> Optional[date]:
    """Разобрать YYYY-MM-DD в date."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def get_day_markers(year: int, month: int) -> dict[str, list[str]]:
    """Для каждого дня месяца вернуть список типов событий.

    Используется на клиенте для отрисовки маркеров на днях.
    Возвращает: {"YYYY-MM-DD": ["task", "reminder", ...], ...}
    """
    events = repo.get_events_for_month(year, month)
    markers: dict[str, list[str]] = {}
    for ev in events:
        if not ev.date:
            continue
        types = markers.setdefault(ev.date, [])
        if ev.type.value not in types:
            types.append(ev.type.value)
    return markers


def get_upcoming_days(days_ahead: int = 14) -> list[dict[str, Any]]:
    """События на ближайшие N дней — для интеграции в Attention.

    Возвращает список: [{"date": "YYYY-MM-DD", "events": [CalendarEvent, ...]}, ...]
    Только дни, где есть хотя бы одно событие.
    """
    today = _today()
    result: list[dict[str, Any]] = []
    for i in range(days_ahead):
        day = today + timedelta(days=i)
        day_events = repo.get_events_for_day(day)
        if day_events:
            result.append({
                "date": day.isoformat(),
                "events": [ev.model_dump() for ev in day_events],
            })
    return result
