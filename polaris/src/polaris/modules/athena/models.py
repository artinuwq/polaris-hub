"""Схемы запросов и ответов Athena API."""
from __future__ import annotations

from pydantic import BaseModel, Field


class AthenaChecklistItem(BaseModel):
    text: str
    done: bool = False


class AthenaTaskCreate(BaseModel):
    """Создание задачи. Обязательно только title; остальное — по желанию."""

    title: str = Field(..., min_length=1, max_length=500, description="Название задачи")
    description: str = Field(default="", description="Описание")
    date: str = Field(default="", description="Срок, YYYY-MM-DD")
    time: str = Field(default="", description="Время, HH:MM")
    priority: str = Field(default="normal", description="fire | important | normal | someday")
    energy: str = Field(default="medium", description="quick | medium | large")
    repeat: str = Field(default="never", description="never | daily | weekly | monthly | yearly | custom")
    status: str = Field(default="todo", description="todo | in-progress | waiting | done")
    tags: list[str] = Field(default_factory=list, description="Теги без #")
    project: str = Field(default="", description="Название проекта (цвет подставится сам, если проект уже есть)")
    checklist: list[str] = Field(default_factory=list, description="Пункты чеклиста текстом")


class AthenaTask(BaseModel):
    id: str
    title: str
    description: str
    date: str
    time: str
    repeat: str
    priority: str
    status: str
    energy: str
    tags: list[str]
    project: str
    checklist: list[AthenaChecklistItem]
    important: bool = Field(description="Срочная или важная по приоритету и не выполнена")
    created_at: str
    updated_at: str
    done_at: str


class AthenaTaskList(BaseModel):
    items: list[AthenaTask]
    total: int
    limit: int
    offset: int


class AthenaCalendarEvent(BaseModel):
    """Одна запись календаря: событие, задача на день, напоминание, платёж или подписка."""

    id: str
    type: str = Field(description="event | task | reminder | payment | subscription")
    type_label: str
    title: str
    description: str
    date: str
    time: str
    end_time: str
    all_day: bool = Field(description="true, если время не указано")
    priority: str
    important: bool = Field(description="Срочная или важная и не выполнена (встречи — всегда важные)")
    status: str
    project: str
    tags: list[str]
    source: str = Field(description="Откуда запись: events | tasks | finance | subscriptions")
    source_id: str = Field(description="id в своём разделе; для задачи — для GET /tasks/{id}")
    amount: float | None = Field(default=None, description="Только для платежей и подписок")
    currency: str = ""


class AthenaCalendarDay(BaseModel):
    date: str
    events: list[AthenaCalendarEvent]


class AthenaCalendarDayResponse(BaseModel):
    date: str
    total: int
    events: list[AthenaCalendarEvent]


class AthenaCalendarPeriod(BaseModel):
    start: str
    end: str
    total: int
    days: list[AthenaCalendarDay] = Field(description="Только дни, в которых что-то есть")


class AthenaCalendarNext(BaseModel):
    total: int
    events: list[AthenaCalendarEvent]

