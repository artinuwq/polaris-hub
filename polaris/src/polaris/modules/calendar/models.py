"""Calendar module domain models.

The Calendar module is NOT a separate data store. It is a read-only
aggregation layer that projects existing entities (tasks, reminders,
payments, subscriptions) into a uniform `CalendarEvent` representation.

Because all entities live in their own modules, the calendar model is
deliberately anemic: it only describes the *shape* of a calendar entry
and a factory (`from_task`, `from_payment`, etc.) that adapts an existing
domain object into that shape. No persistence is performed here.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class EventType(str, Enum):
    """Категории записей, отображаемых в календаре."""

    TASK = "task"
    REMINDER = "reminder"
    PAYMENT = "payment"
    SUBSCRIPTION = "subscription"
    EVENT = "event"


# Цвета для визуального различения типов записей в календаре.
EVENT_TYPE_COLORS: dict[EventType, str] = {
    EventType.TASK: "#5ab8ff",         # синий
    EventType.REMINDER: "#a78bfa",     # фиолетовый
    EventType.PAYMENT: "#52d273",      # зелёный
    EventType.SUBSCRIPTION: "#f5b942",  # жёлтый
    EventType.EVENT: "#ff5d73",        # красный
}

# Читаемые названия типов для UI.
EVENT_TYPE_LABELS: dict[EventType, str] = {
    EventType.TASK: "Задача",
    EventType.REMINDER: "Напоминание",
    EventType.PAYMENT: "Платёж",
    EventType.SUBSCRIPTION: "Подписка",
    EventType.EVENT: "Событие",
}


class CalendarEvent(BaseModel):
    """Единая модель записи календаря.

    Одна и та же бизнес-сущность (например, задача) может отображаться
    в календаре через эту модель, не создавая дублей.
    """

    id: str
    source: str                                   # например, "tasks", "subscriptions"
    source_id: str                                # id сущности в её модуле
    title: str
    description: str = ""
    date: str = ""                                # YYYY-MM-DD
    time: str = ""                                # HH:MM
    end_time: str = ""                            # HH:MM (для событий с длительностью)
    type: EventType = EventType.TASK
    priority: str = "normal"
    status: str = "todo"
    project: str = ""
    project_color: str = ""
    tags: list[str] = Field(default_factory=list)
    url: str = ""                                 # deep link / переход к сущности

    @property
    def color(self) -> str:
        """Цветовая маркировка по типу записи."""
        return EVENT_TYPE_COLORS.get(self.type, "#5ab8ff")

    @property
    def label(self) -> str:
        """Читаемое название типа записи."""
        return EVENT_TYPE_LABELS.get(self.type, "Задача")

    @property
    def datetime(self) -> Optional[datetime]:
        """Полный datetime записи (дата + время) или None."""
        if not self.date:
            return None
        try:
            if self.time:
                return datetime.strptime(f"{self.date} {self.time}", "%Y-%m-%d %H:%M")
            return datetime.strptime(self.date, "%Y-%m-%d")
        except (ValueError, TypeError):
            return None

    def sort_key(self) -> tuple:
        """Ключ для сортировки событий внутри дня."""
        # Сначала по времени, затем по приоритету, затем по заголовку.
        time_part = self.time or "99:99"
        priority_order = {"fire": 0, "important": 1, "normal": 2, "someday": 3}
        priority_part = priority_order.get(self.priority, 99)
        return (time_part, priority_part, self.title)

    @classmethod
    def from_task(cls, row: dict[str, Any]) -> "CalendarEvent":
        """Адаптировать задачу (TaskResponse / dict) в календарное событие."""
        import json as _json

        tags_raw = row.get("tags", [])
        if isinstance(tags_raw, str):
            tags_raw = _json.loads(tags_raw) if tags_raw else []

        return cls(
            id=row.get("id", ""),
            source="tasks",
            source_id=row.get("id", ""),
            title=row.get("title", ""),
            description=row.get("description", ""),
            date=row.get("date", ""),
            time=row.get("time", ""),
            type=EventType.TASK,
            priority=row.get("priority", "normal"),
            status=row.get("status", "todo"),
            project=row.get("project", ""),
            project_color=row.get("project_color", ""),
            tags=list(tags_raw),
            url=f"/tasks#{row.get('id', '')}",
        )

    @classmethod
    def from_reminder(cls, row: dict[str, Any]) -> "CalendarEvent":
        """Адаптировать напоминание в календарное событие.

        Напоминание хранится в задаче как ``remind_at`` — ISO datetime.
        Мы разбираем его на дату и время.
        """
        remind_at = row.get("remind_at", "")
        date = ""
        time = ""
        if remind_at:
            try:
                dt = datetime.fromisoformat(remind_at.replace("Z", "+00:00"))
                date = dt.strftime("%Y-%m-%d")
                time = dt.strftime("%H:%M")
            except (ValueError, TypeError):
                pass

        return cls(
            id=f"reminder-{row.get('id', '')}",
            source="tasks",
            source_id=row.get("id", ""),
            title=f"🔔 {row.get('title', 'Напоминание')}",
            description=row.get("description", ""),
            date=date,
            time=time,
            type=EventType.REMINDER,
            priority=row.get("priority", "normal"),
            status=row.get("status", "todo"),
            project=row.get("project", ""),
            project_color=row.get("project_color", ""),
            tags=list(row.get("tags", [])),
            url=f"/tasks#{row.get('id', '')}",
        )

    @classmethod
    def from_payment(cls, row: dict[str, Any]) -> "CalendarEvent":
        """Адаптировать платёж в календарное событие.

        Ожидается dict с полями: id, title/name, amount, due_date/date,
        status, currency и т.п.
        """
        title = row.get("title") or row.get("name", "Платёж")
        date = row.get("due_date") or row.get("date", "")
        time = row.get("time", "")
        amount = row.get("amount", 0)
        currency = row.get("currency", "₽")
        status = row.get("status", "pending")

        if amount:
            title = f"{title} — {amount} {currency}"

        return cls(
            id=f"payment-{row.get('id', '')}",
            source="billing",
            source_id=row.get("id", ""),
            title=title,
            description=row.get("description", ""),
            date=date,
            time=time,
            type=EventType.PAYMENT,
            priority="important" if status in ("overdue", "warning") else "normal",
            status=status,
            project=row.get("project", ""),
            tags=list(row.get("tags", [])),
            url=f"/finance#payment-{row.get('id', '')}",
        )

    @classmethod
    def from_subscription(cls, row: dict[str, Any]) -> "CalendarEvent":
        """Адаптировать подписку в календарное событие.

        Ожидается dict с полями: id, name, next_billing_date, amount,
        currency, active, provider.
        """
        name = row.get("name", "Подписка")
        date = row.get("next_billing_date", "") or row.get("billing_date", "")
        amount = row.get("amount", 0)
        currency = row.get("currency", "₽")
        active = row.get("active", True)
        provider = row.get("provider", "")

        title = name
        if amount:
            title += f" — {amount} {currency}"

        if provider:
            title += f" ({provider})"

        return cls(
            id=f"subscription-{row.get('id', '')}",
            source="subscriptions",
            source_id=row.get("id", ""),
            title=title,
            description=row.get("description", ""),
            date=date,
            time="",
            type=EventType.SUBSCRIPTION,
            priority="important" if not active else "normal",
            status="active" if active else "inactive",
            project=row.get("project", ""),
            tags=list(row.get("tags", [])),
            url=f"/finance#subscription-{row.get('id', '')}",
        )

    @classmethod
    def from_recurring_payment(cls, row: dict[str, Any]) -> "CalendarEvent":
        """Адаптировать регулярный платёж (Finance) в календарное событие.

        Ожидается строка таблицы ``recurring_payments``: id, name, amount,
        currency, next_payment_date, status, category.
        """
        name = row.get("name", "Платёж")
        amount = row.get("amount", 0)
        currency = row.get("currency", "")
        status = row.get("status", "active")

        title = f"💰 {name}"
        if amount:
            title += f" — {amount} {currency}".rstrip()

        return cls(
            id=f"payment-{row.get('id', '')}",
            source="finance",
            source_id=row.get("id", ""),
            title=title,
            description=row.get("description", ""),
            date=row.get("next_payment_date", ""),
            time="",
            type=EventType.PAYMENT,
            priority="normal",
            status=status,
            project=row.get("category", ""),
            tags=[],
            url=f"/finance#{row.get('id', '')}",
        )

    @classmethod
    def from_event(cls, row: dict[str, Any]) -> "CalendarEvent":
        """Адаптировать произвольное событие (встреча, вечернинг и т.п.)."""
        title = row.get("title", "Событие")
        date = row.get("date", "")
        return cls(
            id=f"event-{row.get('id', '')}",
            source="events",
            source_id=row.get("id", ""),
            title=title,
            description=row.get("description", ""),
            date=date,
            time=row.get("time", ""),
            end_time=row.get("end_time", ""),
            type=EventType.EVENT,
            priority="important",
            status="todo",
            project=row.get("project", ""),
            project_color=row.get("project_color", ""),
            tags=list(row.get("tags", [])),
            url=f"/events#{row.get('id', '')}",
        )


class CalendarView(BaseModel):
    """Ответ API: список событий для указанного периода."""

    month: int                # 1..12
    year: int
    events: list[CalendarEvent] = Field(default_factory=list)
