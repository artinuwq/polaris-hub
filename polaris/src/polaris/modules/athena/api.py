"""Athena API router - token-based authentication."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Header, Query, status
from datetime import datetime
from typing import Optional, List

from polaris.infra.settings import Settings
from polaris.modules.athena import service
from polaris.modules.athena.models import (
    TaskStatus, TaskImportance, ImprintType,
    TaskCreate, TaskUpdate, TaskResponse, TaskListResponse,
    EventCreate, EventResponse, EventListResponse,
    FocusSet, FocusState,
    EntryCreate, EntryResponse, EntryListResponse,
    ServerResponse, ServerStatusResponse, ServerListResponse,
    BalanceResponse, TransactionResponse, TransactionListResponse,
    EmailResponse, EmailListResponse,
    HealthResponse, TokenVerify,
    Pagination,
)


settings = Settings.from_env()
router = APIRouter(prefix="/api/athena", tags=["athena"])


# ===== AUTH DEPENDENCY =====
async def verify_api_token(
    authorization: str = Header(default=None, alias="Authorization"),
    x_api_token: str = Header(default=None, alias="X-API-Token"),
) -> TokenVerify:
    """Verify API token from Authorization header or X-API-Token header."""
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
    elif x_api_token:
        token = x_api_token
    
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API token. Use Authorization: Bearer <token> or X-API-Token header",
        )
    
    result = service.get_service().verify_token(token)
    if not result.valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=result.message,
        )
    return result


# ===== HEALTH =====
@router.get("/health", response_model=HealthResponse, security=[])
async def health_check():
    """Public health check endpoint."""
    svc = service.get_service()
    health = svc.health_check()
    return HealthResponse(**health)


# ===== TASKS =====
@router.get("/tasks", response_model=TaskListResponse)
async def list_tasks(
    status: Optional[TaskStatus] = Query(None),
    project: Optional[str] = Query(None),
    due_before: Optional[datetime] = Query(None),
    due_after: Optional[datetime] = Query(None),
    tags: Optional[str] = Query(None, description="Comma-separated tags"),
    importance: Optional[TaskImportance] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _auth: TokenVerify = Depends(verify_api_token),
):
    """List tasks with optional filters."""
    return service.get_service().list_tasks(
        status, project, due_before, due_after, tags, importance, limit, offset
    )


@router.get("/tasks/important", response_model=TaskListResponse)
async def list_important_tasks(
    limit: int = Query(20, ge=1, le=50),
    _auth: TokenVerify = Depends(verify_api_token),
):
    """Important tasks: high importance AND (due <= tomorrow 23:59 OR no due date)."""
    from datetime import timedelta
    tomorrow_end = datetime.utcnow().replace(hour=23, minute=59, second=59, microsecond=0) + timedelta(days=1)
    
    svc = service.get_service()
    return svc.list_tasks(
        importance=TaskImportance.high,
        due_before=tomorrow_end,
        limit=limit,
        offset=0,
    )


@router.post("/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    task_in: TaskCreate,
    _auth: TokenVerify = Depends(verify_api_token),
):
    """Create a new task."""
    return service.get_service().create_task(task_in)


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: str,
    _auth: TokenVerify = Depends(verify_api_token),
):
    """Get a single task by ID."""
    task = service.get_service().get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.patch("/tasks/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: str,
    task_update: TaskUpdate,
    _auth: TokenVerify = Depends(verify_api_token),
):
    """Update a task."""
    task = service.get_service().update_task(task_id, task_update)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.delete("/tasks/{task_id}")
async def delete_task(
    task_id: str,
    _auth: TokenVerify = Depends(verify_api_token),
):
    """Delete (cancel) a task."""
    deleted = service.get_service().delete_task(task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"success": True, "message": "Task cancelled"}


@router.post("/tasks/{task_id}/complete", response_model=TaskResponse)
async def complete_task(
    task_id: str,
    _auth: TokenVerify = Depends(verify_api_token),
):
    """Mark task as done."""
    task = service.get_service().complete_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


# ===== EVENTS =====
@router.get("/events", response_model=EventListResponse)
async def list_events(
    calendar: Optional[str] = Query(None),
    start_after: Optional[datetime] = Query(None),
    start_before: Optional[datetime] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _auth: TokenVerify = Depends(verify_api_token),
):
    """List events with optional filters."""
    return service.get_service().list_events(calendar, start_after, start_before, limit, offset)


@router.post("/events", response_model=EventResponse, status_code=status.HTTP_201_CREATED)
async def create_event(
    event_in: EventCreate,
    _auth: TokenVerify = Depends(verify_api_token),
):
    """Create a new event."""
    return service.get_service().create_event(event_in)


@router.get("/events/{event_id}", response_model=EventResponse)
async def get_event(
    event_id: str,
    _auth: TokenVerify = Depends(verify_api_token),
):
    """Get a single event by ID."""
    event = service.get_service().get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


# ===== FOCUS / ATTENTION =====
@router.get("/focus", response_model=FocusState)
async def get_focus(_auth: TokenVerify = Depends(verify_api_token)):
    """Get current focus state."""
    focus = service.get_service().get_focus()
    if not focus:
        return FocusState(
            current_focus=None,
            context=None,
            since=datetime.utcnow(),
            previous=[],
        )
    return focus


@router.post("/focus", response_model=FocusState)
async def set_focus(
    focus_in: FocusSet,
    _auth: TokenVerify = Depends(verify_api_token),
):
    """Set current focus."""
    return service.get_service().set_focus(focus_in)


# ===== IMPRINTS / INBOX =====
@router.get("/inbox", response_model=EntryListResponse)
async def list_entries(
    type: Optional[ImprintType] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _auth: TokenVerify = Depends(verify_api_token),
):
    """List inbox entries."""
    return service.get_service().list_entries(type, limit, offset)


@router.post("/inbox", response_model=EntryResponse, status_code=status.HTTP_201_CREATED)
async def create_entry(
    entry_in: EntryCreate,
    _auth: TokenVerify = Depends(verify_api_token),
):
    """Create a new inbox entry."""
    return service.get_service().create_entry(entry_in)


# ===== SERVERS =====
@router.get("/servers", response_model=ServerListResponse)
async def list_servers(_auth: TokenVerify = Depends(verify_api_token)):
    """List all servers."""
    return service.get_service().list_servers()


@router.get("/servers/{server_id}/status", response_model=ServerStatusResponse)
async def get_server_status(
    server_id: str,
    _auth: TokenVerify = Depends(verify_api_token),
):
    """Get server status."""
    status = service.get_service().get_server_status(server_id)
    if not status:
        raise HTTPException(status_code=404, detail="Server not found")
    return status


# ===== FINANCE =====
@router.get("/finance/balances", response_model=List[BalanceResponse])
async def get_balances(_auth: TokenVerify = Depends(verify_api_token)):
    """Get all account balances."""
    return service.get_service().get_balances()


@router.get("/finance/transactions", response_model=TransactionListResponse)
async def list_transactions(
    account: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _auth: TokenVerify = Depends(verify_api_token),
):
    """List transactions."""
    return service.get_service().list_transactions(account, limit, offset)


# ===== EMAIL =====
@router.get("/emails", response_model=EmailListResponse)
async def list_emails(
    is_read: Optional[bool] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _auth: TokenVerify = Depends(verify_api_token),
):
    """List emails."""
    return service.get_service().list_emails(is_read, limit, offset)


# ===== TOKEN VERIFY =====
@router.post("/auth/verify", response_model=TokenVerify)
async def verify_token_endpoint(_auth: TokenVerify = Depends(verify_api_token)):
    """Verify API token validity."""
    return _auth