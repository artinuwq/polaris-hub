from __future__ import annotations

"""API роутер для событий (произвольные записи календаря, добавляемые вручную).

Эндпоинты:
  GET    /api/events        — список всех событий
  POST   /api/events        — создать событие
  GET    /api/events/{id}   — получить событие
  PATCH  /api/events/{id}   — обновить событие
  DELETE /api/events/{id}   — удалить событие
"""

from fastapi import APIRouter, Depends, HTTPException

from polaris.integrations.telegram.auth import TelegramWebAppUser
from polaris.shared.auth import require_admin
from polaris.modules.events.models import EventCreate, EventUpdate
from polaris.modules.events import service

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("")
def list_events(_user: TelegramWebAppUser | None = Depends(require_admin)):
    """Получить все события."""
    events = service.get_all_events()
    return {
        "success": True,
        "data": {"events": [ev.model_dump() for ev in events]},
    }


@router.post("")
def create_event(body: EventCreate, _user: TelegramWebAppUser | None = Depends(require_admin)):
    """Создать новое событие."""
    try:
        event = service.create_event(body)
        return {
            "success": True,
            "data": event.model_dump(),
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{event_id}")
def get_event(event_id: str, _user: TelegramWebAppUser | None = Depends(require_admin)):
    """Получить событие по ID."""
    event = service.get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return {
        "success": True,
        "data": event.model_dump(),
    }


@router.patch("/{event_id}")
def update_event(
    event_id: str,
    body: EventUpdate,
    _user: TelegramWebAppUser | None = Depends(require_admin),
):
    """Обновить событие."""
    event = service.update_event(event_id, body)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return {
        "success": True,
        "data": event.model_dump(),
    }


@router.delete("/{event_id}")
def delete_event(event_id: str, _user: TelegramWebAppUser | None = Depends(require_admin)):
    """Удалить событие."""
    deleted = service.delete_event(event_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Event not found")
    return {
        "success": True,
        "message": "Event deleted",
    }
