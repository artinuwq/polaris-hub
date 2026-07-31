from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class EventCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: str = ""
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    time: str = ""
    end_time: str = ""
    project: str = ""
    project_color: str = ""
    tags: list[str] = []


class EventUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    date: str | None = None
    time: str | None = None
    end_time: str | None = None
    project: str | None = None
    project_color: str | None = None
    tags: list[str] | None = None


class EventResponse(BaseModel):
    id: str
    title: str
    description: str
    date: str
    time: str
    end_time: str
    project: str
    project_color: str
    tags: list[str]
    created_at: str
    updated_at: str

    @classmethod
    def from_db_row(cls, row: dict[str, Any]) -> "EventResponse":
        import json

        tags_raw = row.get("tags", "[]")
        tags = json.loads(tags_raw) if isinstance(tags_raw, str) else list(tags_raw)

        return cls(
            id=row["id"],
            title=row["title"],
            description=row.get("description", ""),
            date=row.get("date", ""),
            time=row.get("time", ""),
            end_time=row.get("end_time", ""),
            project=row.get("project", ""),
            project_color=row.get("project_color", ""),
            tags=tags,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
