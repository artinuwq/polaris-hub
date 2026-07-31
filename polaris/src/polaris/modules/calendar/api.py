from __future__ import annotations

"""API роутер для календаря.

Эндпоинты:
  GET /api/calendar/month   — события на месяц (year, month)
  GET /api/calendar/day     — события на конкретный день (date)
  GET /api/calendar/today   — события на сегодня
  GET /api/calendar/markers — маркеры на дни месяца
  GET /api/calendar/upcoming — предстоящие события (для Attention)
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from polaris.integrations.telegram.auth import TelegramWebAppUser
from polaris.shared.auth import require_admin
from polaris.modules.calendar import service
from polaris.modules.calendar.models import CalendarEvent, CalendarView

router = APIRouter(prefix="/api/calendar", tags=["calendar"])


def _dump_event(ev: CalendarEvent) -> dict:
    """Сериализовать событие в dict для JSON-ответа."""
    data = ev.model_dump()
    # EventType — Enum, сериализуем как строку
    data["type"] = ev.type.value
    data["color"] = ev.color
    data["label"] = ev.label
    return data


@router.get("/month")
def calendar_month(
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    _user: TelegramWebAppUser | None = Depends(require_admin),
):
    """События на указанный месяц."""
    view: CalendarView = service.get_month_events(year, month)
    return {
        "success": True,
        "data": {
            "month": view.month,
            "year": view.year,
            "events": [_dump_event(ev) for ev in view.events],
        },
    }


@router.get("/day")
def calendar_day(
    date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    _user: TelegramWebAppUser | None = Depends(require_admin),
):
    """События на конкретный день (формат YYYY-MM-DD)."""
    events = service.get_day_events(date)
    return {
        "success": True,
        "data": {
            "date": date,
            "events": [_dump_event(ev) for ev in events],
        },
    }


@router.get("/today")
def calendar_today(
    _user: TelegramWebAppUser | None = Depends(require_admin),
):
    """События на сегодняшний день."""
    events = service.get_today_events()
    return {
        "success": True,
        "data": {
            "events": [_dump_event(ev) for ev in events],
        },
    }


@router.get("/markers")
def calendar_markers(
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    _user: TelegramWebAppUser | None = Depends(require_admin),
):
    """Маркеры на дни месяца (какие типы событий есть в каждом дне)."""
    markers = service.get_day_markers(year, month)
    return {
        "success": True,
        "data": {"markers": markers},
    }


@router.get("/upcoming")
def calendar_upcoming(
    days_ahead: int = Query(14, ge=1, le=90),
    _user: TelegramWebAppUser | None = Depends(require_admin),
):
    """Предстоящие события на ближайшие N дней — для Attention."""
    upcoming = service.get_upcoming_days(days_ahead)
    return {
        "success": True,
        "data": {"upcoming": upcoming},
    }
