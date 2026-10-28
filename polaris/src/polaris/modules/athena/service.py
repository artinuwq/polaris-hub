"""Athena API service layer."""

from __future__ import annotations

from typing import Optional, List
from datetime import datetime

from polaris.modules.athena import repository as repo
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
    TokenVerify,
)
from polaris.infra.settings import Settings


class AthenaService:
    """Business logic for Athena API."""

    def __init__(self, repository: Optional[repo.AthenaRepository] = None):
        self._repo = repository or repo.get_repo()
        self._settings = Settings.from_env()

    # ===== AUTH =====
    def verify_token(self, token: str) -> TokenVerify:
        """Verify API token."""
        if not self._settings.api_token:
            return TokenVerify(valid=False, message="API token not configured")
        if token == self._settings.api_token:
            return TokenVerify(valid=True, message="Token valid")
        return TokenVerify(valid=False, message="Invalid token")

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
        return self._repo.list_tasks(status, project, due_before, due_after, tags, importance, limit, offset)

    def get_task(self, task_id: str) -> Optional[TaskResponse]:
        return self._repo.get_task(task_id)

    def create_task(self, task_in: TaskCreate) -> TaskResponse:
        return self._repo.create_task(task_in)

    def update_task(self, task_id: str, task_update: TaskUpdate) -> Optional[TaskResponse]:
        return self._repo.update_task(task_id, task_update)

    def delete_task(self, task_id: str) -> bool:
        return self._repo.delete_task(task_id)

    def complete_task(self, task_id: str) -> Optional[TaskResponse]:
        return self._repo.complete_task(task_id)

    # ===== EVENTS =====
    def list_events(
        self,
        calendar: Optional[str] = None,
        start_after: Optional[datetime] = None,
        start_before: Optional[datetime] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> EventListResponse:
        return self._repo.list_events(calendar, start_after, start_before, limit, offset)

    def get_event(self, event_id: str) -> Optional[EventResponse]:
        return self._repo.get_event(event_id)

    def create_event(self, event_in: EventCreate) -> EventResponse:
        return self._repo.create_event(event_in)

    # ===== FOCUS / ATTENTION =====
    def get_focus(self) -> Optional[FocusState]:
        return self._repo.get_focus()

    def set_focus(self, focus_in: FocusSet) -> FocusState:
        return self._repo.set_focus(focus_in)

    # ===== IMPRINTS / INBOX =====
    def list_entries(
        self,
        type: Optional[ImprintType] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> EntryListResponse:
        return self._repo.list_entries(type, limit, offset)

    def create_entry(self, entry_in: EntryCreate) -> EntryResponse:
        return self._repo.create_entry(entry_in)

    # ===== SERVERS =====
    def list_servers(self) -> ServerListResponse:
        return self._repo.list_servers()

    def get_server_status(self, server_id: str) -> Optional[ServerStatusResponse]:
        return self._repo.get_server_status(server_id)

    # ===== FINANCE =====
    def get_balances(self) -> List[BalanceResponse]:
        return self._repo.get_balances()

    def list_transactions(
        self,
        account: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> TransactionListResponse:
        return self._repo.list_transactions(account, limit, offset)

    # ===== EMAIL =====
    def list_emails(
        self,
        is_read: Optional[bool] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> EmailListResponse:
        return self._repo.list_emails(is_read, limit, offset)

    # ===== HEALTH =====
    def health_check(self) -> dict:
        return self._repo.health_check(self._settings.app_version)


# Global instance
_service: Optional[AthenaService] = None

def get_service() -> AthenaService:
    global _service
    if _service is None:
        _service = AthenaService()
    return _service