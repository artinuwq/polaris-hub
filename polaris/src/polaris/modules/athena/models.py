"""Athena API models - token-based auth, tasks, events, attention, imprints, servers, finance, emails."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional, List, Any
from pydantic import BaseModel, Field, ConfigDict
import uuid


# ===== ENUMS =====
class TaskStatus(str, Enum):
    open = "open"
    in_progress = "in_progress"
    done = "done"
    cancelled = "cancelled"


class TaskImportance(str, Enum):
    high = "high"
    normal = "normal"
    low = "low"


class ImprintType(str, Enum):
    imprint = "imprint"
    task_inbox = "task_inbox"
    work_note = "work_note"


# ===== PAGINATION =====
class Pagination(BaseModel):
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    total: int = 0


# ===== TASKS =====
class TaskBase(BaseModel):
    title: str = Field(max_length=500)
    project: Optional[str] = Field(default=None, max_length=100)
    due: Optional[datetime] = None
    tags: List[str] = Field(default_factory=list)
    notes: Optional[str] = None
    importance: TaskImportance = TaskImportance.normal


class TaskCreate(TaskBase):
    pass


class TaskUpdate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=500)
    status: Optional[TaskStatus] = None
    project: Optional[str] = Field(default=None, max_length=100)
    due: Optional[datetime] = None
    tags: Optional[List[str]] = None
    notes: Optional[str] = None
    importance: Optional[TaskImportance] = None


class TaskResponse(TaskBase):
    id: str
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class TaskListResponse(BaseModel):
    items: List[TaskResponse]
    pagination: Pagination


# ===== EVENTS =====
class EventBase(BaseModel):
    title: str = Field(max_length=500)
    start: datetime
    end: datetime
    calendar: Optional[str] = Field(default=None, max_length=100)
    description: Optional[str] = None
    location: Optional[str] = Field(default=None, max_length=500)


class EventCreate(EventBase):
    pass


class EventResponse(EventBase):
    id: str
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class EventListResponse(BaseModel):
    items: List[EventResponse]
    pagination: Pagination


# ===== ATTENTION / FOCUS =====
class FocusState(BaseModel):
    current_focus: Optional[str] = None
    context: Optional[str] = None
    since: datetime
    previous: List[str] = Field(default_factory=list)
    model_config = ConfigDict(from_attributes=True)


class FocusSet(BaseModel):
    focus: str = Field(max_length=500)
    context: Optional[str] = None


# ===== IMPRINTS / INBOX =====
class EntryBase(BaseModel):
    type: ImprintType
    content: str
    source: Optional[str] = Field(default=None, max_length=100)
    tags: List[str] = Field(default_factory=list)


class EntryCreate(EntryBase):
    pass


class EntryResponse(EntryBase):
    id: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class EntryListResponse(BaseModel):
    items: List[EntryResponse]
    pagination: Pagination


# ===== SERVERS =====
class ServerResponse(BaseModel):
    id: str
    name: str
    host: str
    role: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    model_config = ConfigDict(from_attributes=True)


class ServerStatusResponse(BaseModel):
    server_id: str
    online: bool
    last_check: datetime
    metrics: dict = Field(default_factory=dict)
    model_config = ConfigDict(from_attributes=True)


class ServerListResponse(BaseModel):
    items: List[ServerResponse]


# ===== FINANCE =====
class BalanceResponse(BaseModel):
    account: str
    currency: str
    amount: int
    as_of: datetime
    model_config = ConfigDict(from_attributes=True)


class TransactionResponse(BaseModel):
    id: str
    date: datetime
    account: str
    amount: int
    currency: str
    category: Optional[str] = None
    description: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class TransactionListResponse(BaseModel):
    items: List[TransactionResponse]
    pagination: Pagination


# ===== EMAIL =====
class EmailResponse(BaseModel):
    id: str
    from_: str = Field(alias="from")
    subject: str
    snippet: str
    received_at: datetime
    is_read: bool
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class EmailListResponse(BaseModel):
    items: List[EmailResponse]
    pagination: Pagination


# ===== HEALTH =====
class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    time: datetime


# ===== AUTH =====
class TokenVerify(BaseModel):
    valid: bool
    message: str