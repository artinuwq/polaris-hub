"""Athena API repository - database operations."""

from __future__ import annotations

from typing import Optional, List
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func

from polaris.infra.database import get_session
from polaris.modules.athena.models import (
    TaskStatus, TaskImportance, ImprintType,
    TaskCreate, TaskUpdate, TaskResponse, TaskListResponse,
    EventCreate, EventResponse, EventListResponse,
    FocusSet, FocusState,
    EntryCreate, EntryResponse, EntryListResponse,
    ServerResponse, ServerStatusResponse, ServerListResponse,
    BalanceResponse, TransactionResponse, TransactionListResponse,
    EmailResponse, EmailListResponse,
    Pagination,
)


class AthenaRepository:
    """Database operations for Athena API."""

    def __init__(self, db: Optional[Session] = None):
        self._db = db

    @property
    def db(self) -> Session:
        if self._db is None:
            self._db = next(get_session())
        return self._db

    # ===== TASKS =====
    def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        project: Optional[str] = None,
        due_before: Optional[datetime] = None,
        due_after: Optional[datetime] = None,
        tags: Optional[str] = None,
        importance: Optional[TaskImportance] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> TaskListResponse:
        from polaris.modules.athena.models import Task as TaskModel
        query = self.db.query(TaskModel)
        
        if status:
            query = query.filter(TaskModel.status == status)
        if project:
            query = query.filter(TaskModel.project == project)
        if due_before:
            query = query.filter(TaskModel.due <= due_before)
        if due_after:
            query = query.filter(TaskModel.due >= due_after)
        if importance:
            query = query.filter(TaskModel.importance == importance)
        if tags:
            tag_list = [t.strip() for t in tags.split(",")]
            for tag in tag_list:
                query = query.filter(TaskModel.tags.contains([tag]))
        
        total = query.count()
        items = query.order_by(TaskModel.created_at.desc()).offset(offset).limit(limit).all()
        
        return TaskListResponse(
            items=[TaskResponse.model_validate(t) for t in items],
            pagination=Pagination(limit=limit, offset=offset, total=total),
        )

    def get_task(self, task_id: str) -> Optional[TaskResponse]:
        from polaris.modules.athena.models import Task as TaskModel
        task = self.db.query(TaskModel).filter(TaskModel.id == task_id).first()
        return TaskResponse.model_validate(task) if task else None

    def create_task(self, task_in: TaskCreate) -> TaskResponse:
        from polaris.modules.athena.models import Task as TaskModel
        task = TaskModel(**task_in.model_dump())
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return TaskResponse.model_validate(task)

    def update_task(self, task_id: str, task_update: TaskUpdate) -> Optional[TaskResponse]:
        from polaris.modules.athena.models import Task as TaskModel
        task = self.db.query(TaskModel).filter(TaskModel.id == task_id).first()
        if not task:
            return None
        update_data = task_update.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(task, field, value)
        task.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(task)
        return TaskResponse.model_validate(task)

    def delete_task(self, task_id: str) -> bool:
        from polaris.modules.athena.models import Task as TaskModel
        task = self.db.query(TaskModel).filter(TaskModel.id == task_id).first()
        if not task:
            return False
        task.status = TaskStatus.cancelled
        task.updated_at = datetime.utcnow()
        self.db.commit()
        return True

    def complete_task(self, task_id: str) -> Optional[TaskResponse]:
        from polaris.modules.athena.models import Task as TaskModel
        task = self.db.query(TaskModel).filter(TaskModel.id == task_id).first()
        if not task:
            return None
        task.status = TaskStatus.done
        task.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(task)
        return TaskResponse.model_validate(task)

    # ===== EVENTS =====
    def list_events(
        self,
        calendar: Optional[str] = None,
        start_after: Optional[datetime] = None,
        start_before: Optional[datetime] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> EventListResponse:
        from polaris.modules.athena.models import Event as EventModel
        query = self.db.query(EventModel)
        
        if calendar:
            query = query.filter(EventModel.calendar == calendar)
        if start_after:
            query = query.filter(EventModel.start >= start_after)
        if start_before:
            query = query.filter(EventModel.start <= start_before)
        
        total = query.count()
        items = query.order_by(EventModel.start.asc()).offset(offset).limit(limit).all()
        
        return EventListResponse(
            items=[EventResponse.model_validate(e) for e in items],
            pagination=Pagination(limit=limit, offset=offset, total=total),
        )

    def get_event(self, event_id: str) -> Optional[EventResponse]:
        from polaris.modules.athena.models import Event as EventModel
        event = self.db.query(EventModel).filter(EventModel.id == event_id).first()
        return EventResponse.model_validate(event) if event else None

    def create_event(self, event_in: EventCreate) -> EventResponse:
        from polaris.modules.athena.models import Event as EventModel
        event = EventModel(**event_in.model_dump())
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        return EventResponse.model_validate(event)

    # ===== FOCUS / ATTENTION =====
    def get_focus(self) -> Optional[FocusState]:
        from polaris.modules.athena.models import Focus as FocusModel
        focus = self.db.query(FocusModel).order_by(FocusModel.since.desc()).first()
        return FocusState.model_validate(focus) if focus else None

    def set_focus(self, focus_in: FocusSet) -> FocusState:
        from polaris.modules.athena.models import Focus as FocusModel
        # Deactivate previous focus
        self.db.query(FocusModel).update({"is_active": False})
        # Create new focus
        focus = FocusModel(
            current_focus=focus_in.focus,
            context=focus_in.context,
            since=datetime.utcnow(),
            is_active=True,
        )
        self.db.add(focus)
        self.db.commit()
        self.db.refresh(focus)
        return FocusState.model_validate(focus)

    # ===== IMPRINTS / INBOX =====
    def list_entries(
        self,
        type: Optional[ImprintType] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> EntryListResponse:
        from polaris.modules.athena.models import Entry as EntryModel
        query = self.db.query(EntryModel)
        if type:
            query = query.filter(EntryModel.type == type)
        total = query.count()
        items = query.order_by(EntryModel.created_at.desc()).offset(offset).limit(limit).all()
        return EntryListResponse(
            items=[EntryResponse.model_validate(e) for e in items],
            pagination=Pagination(limit=limit, offset=offset, total=total),
        )

    def create_entry(self, entry_in: EntryCreate) -> EntryResponse:
        from polaris.modules.athena.models import Entry as EntryModel
        entry = EntryModel(**entry_in.model_dump())
        self.db.add(entry)
        self.db.commit()
        self.db.refresh(entry)
        return EntryResponse.model_validate(entry)

    # ===== SERVERS =====
    def list_servers(self) -> ServerListResponse:
        from polaris.modules.athena.models import Server as ServerModel
        servers = self.db.query(ServerModel).all()
        return ServerListResponse(items=[ServerResponse.model_validate(s) for s in servers])

    def get_server_status(self, server_id: str) -> Optional[ServerStatusResponse]:
        from polaris.modules.athena.models import ServerStatus as ServerStatusModel
        status = self.db.query(ServerStatusModel).filter(ServerStatusModel.server_id == server_id).first()
        return ServerStatusResponse.model_validate(status) if status else None

    # ===== FINANCE =====
    def get_balances(self) -> List[BalanceResponse]:
        from polaris.modules.athena.models import Balance as BalanceModel
        balances = self.db.query(BalanceModel).all()
        return [BalanceResponse.model_validate(b) for b in balances]

    def list_transactions(
        self,
        account: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> TransactionListResponse:
        from polaris.modules.athena.models import Transaction as TransactionModel
        query = self.db.query(TransactionModel)
        if account:
            query = query.filter(TransactionModel.account == account)
        total = query.count()
        items = query.order_by(TransactionModel.date.desc()).offset(offset).limit(limit).all()
        return TransactionListResponse(
            items=[TransactionResponse.model_validate(t) for t in items],
            pagination=Pagination(limit=limit, offset=offset, total=total),
        )

    # ===== EMAIL =====
    def list_emails(
        self,
        is_read: Optional[bool] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> EmailListResponse:
        from polaris.modules.athena.models import Email as EmailModel
        query = self.db.query(EmailModel)
        if is_read is not None:
            query = query.filter(EmailModel.is_read == is_read)
        total = query.count()
        items = query.order_by(EmailModel.received_at.desc()).offset(offset).limit(limit).all()
        return EmailListResponse(
            items=[EmailResponse.model_validate(e) for e in items],
            pagination=Pagination(limit=limit, offset=offset, total=total),
        )

    # ===== HEALTH =====
    def health_check(self, version: str) -> dict:
        return {"status": "ok", "version": version, "time": datetime.utcnow()}


# Global instance
_repo: Optional[AthenaRepository] = None

def get_repo() -> AthenaRepository:
    global _repo
    if _repo is None:
        _repo = AthenaRepository()
    return _repo