from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class ChecklistItem(BaseModel):
    text: str
    done: bool = False


class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: str = ""
    date: str = ""
    time: str = ""
    repeat: str = "never"
    priority: str = "normal"
    status: str = "todo"
    energy: str = "medium"
    tags: list[str] = []
    project: str = ""
    project_color: str = ""
    checklist: list[ChecklistItem] = []
    remind_at: str = ""


class TaskUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    date: str | None = None
    time: str | None = None
    repeat: str | None = None
    priority: str | None = None
    status: str | None = None
    energy: str | None = None
    tags: list[str] | None = None
    project: str | None = None
    project_color: str | None = None
    checklist: list[ChecklistItem] | None = None
    remind_at: str | None = None


class TaskResponse(BaseModel):
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
    project_color: str
    checklist: list[ChecklistItem]
    remind_at: str
    created_at: str
    updated_at: str
    done_at: str

    @classmethod
    def from_db_row(cls, row: dict[str, Any]) -> TaskResponse:
        import json

        tags = json.loads(row.get("tags", "[]"))
        checklist_raw = json.loads(row.get("checklist", "[]"))
        checklist = [ChecklistItem(**item) for item in checklist_raw]

        return cls(
            id=row["id"],
            title=row["title"],
            description=row.get("description", ""),
            date=row.get("date", ""),
            time=row.get("time", ""),
            repeat=row.get("repeat", "never"),
            priority=row.get("priority", "normal"),
            status=row.get("status", "todo"),
            energy=row.get("energy", "medium"),
            tags=tags,
            project=row.get("project", ""),
            project_color=row.get("project_color", ""),
            checklist=checklist,
            remind_at=row.get("remind_at", ""),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            done_at=row.get("done_at", ""),
        )


class ProjectResponse(BaseModel):
    id: str
    name: str
    color: str
    created_at: str

    @classmethod
    def from_db_row(cls, row: dict[str, Any]) -> ProjectResponse:
        return cls(
            id=row["id"],
            name=row["name"],
            color=row["color"],
            created_at=row["created_at"],
        )


class TagResponse(BaseModel):
    id: str
    name: str
    created_at: str

    @classmethod
    def from_db_row(cls, row: dict[str, Any]) -> TagResponse:
        return cls(
            id=row["id"],
            name=row["name"],
            created_at=row["created_at"],
        )


class TaskListResponse(BaseModel):
    tasks: list[TaskResponse]
    projects: list[ProjectResponse]
    tags: list[TagResponse]