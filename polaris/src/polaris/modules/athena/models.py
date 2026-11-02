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
