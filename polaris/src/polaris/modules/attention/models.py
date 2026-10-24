"""Attention Engine models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


class AttentionSource(str, Enum):
    CALENDAR = "calendar"
    FINANCE = "finance"
    SERVERS = "servers"


class AttentionType(str, Enum):
    # Calendar
    TASK = "task"
    REMINDER = "reminder"
    EVENT = "event"
    PAYMENT = "payment"
    SUBSCRIPTION = "subscription"
    # Finance
    PAYMENT_DUE = "payment_due"
    PAYMENT_OVERDUE = "payment_overdue"
    # Servers
    SERVER_OFFLINE = "server_offline"
    DISK_WARNING = "disk_warning"
    DISK_CRITICAL = "disk_critical"
    SERVICE_DOWN = "service_down"
    SERVICE_FAILED = "service_failed"


class Urgency(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class UserAction(str, Enum):
    DISMISS = "dismiss"
    SNOOZE = "snooze"
    PIN = "pin"


class AttentionItem(BaseModel):
    """Единая модель элемента внимания."""
    id: str
    source: AttentionSource
    type: AttentionType
    title: str
    description: str = ""
    urgency: Urgency = Urgency.MEDIUM
    score: float = 0.0
    due_date: Optional[str] = None
    due_time: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)
    url: str = ""
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    pinned: bool = False
    snoozed_until: Optional[str] = None


class AttentionFeed(BaseModel):
    """Ответ API: ранжированный список элементов."""
    items: list[AttentionItem] = Field(default_factory=list)
    total: int = 0
    critical_count: int = 0
    high_count: int = 0


class UserActionRequest(BaseModel):
    item_id: str
    action: UserAction
    snooze_until: Optional[str] = None


# Веса приоритета по типам
TYPE_URGENCY_WEIGHTS: dict[AttentionType, int] = {
    AttentionType.SERVER_OFFLINE: 100,
    AttentionType.SERVICE_FAILED: 95,
    AttentionType.DISK_CRITICAL: 90,
    AttentionType.PAYMENT_OVERDUE: 85,
    AttentionType.SERVICE_DOWN: 80,
    AttentionType.DISK_WARNING: 70,
    AttentionType.PAYMENT_DUE: 60,
    AttentionType.REMINDER: 50,
    AttentionType.PAYMENT: 45,
    AttentionType.SUBSCRIPTION: 40,
    AttentionType.EVENT: 35,
    AttentionType.TASK: 30,
}

# Модификаторы по времени
def time_modifier(due_date: Optional[str], due_time: Optional[str]) -> float:
    """Возвращает множитель срочности на основе даты/времени."""
    if not due_date:
        return 1.0
    try:
        from datetime import datetime, date
        due = datetime.fromisoformat(f"{due_date} {due_time or '00:00'}")
        now = datetime.utcnow()
        delta = due - now
        days = delta.total_seconds() / 86400
        if days < 0:
            return 1.5  # просрочено
        elif days <= 1:
            return 1.3  # сегодня/завтра
        elif days <= 3:
            return 1.15
        elif days <= 7:
            return 1.05
        return 1.0
    except Exception:
        return 1.0


def calculate_score(item: AttentionItem) -> float:
    """Вычисляет итоговый скор элемента."""
    base = TYPE_URGENCY_WEIGHTS.get(item.type, 20)
    modifier = time_modifier(item.due_date, item.due_time)
    if item.pinned:
        base += 50
    return round(base * modifier, 2)