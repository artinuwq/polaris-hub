from __future__ import annotations

from polaris.modules.events.models import EventCreate, EventResponse, EventUpdate
from polaris.modules.events import repository as repo


def get_all_events() -> list[EventResponse]:
    return [EventResponse.from_db_row(row) for row in repo.get_all_events()]


def get_event(event_id: str) -> EventResponse | None:
    row = repo.get_event_by_id(event_id)
    if not row:
        return None
    return EventResponse.from_db_row(row)


def create_event(data: EventCreate) -> EventResponse:
    row = repo.create_event(data.model_dump())
    return EventResponse.from_db_row(row)


def update_event(event_id: str, data: EventUpdate) -> EventResponse | None:
    payload = data.model_dump(exclude_none=True)
    row = repo.update_event(event_id, payload)
    if not row:
        return None
    return EventResponse.from_db_row(row)


def delete_event(event_id: str) -> bool:
    return repo.delete_event(event_id)
