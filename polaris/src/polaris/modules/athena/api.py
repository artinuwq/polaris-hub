"""Athena API.

  Для агента (по токену):  /api/athena/*
  Для владельца (из настроек, как остальной интерфейс): /api/agent/*
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from polaris.modules.athena import logic, service, tokens
from polaris.modules.athena.models import (
    AthenaCalendarDayResponse,
    AthenaCalendarNext,
    AthenaCalendarPeriod,
    AthenaTask,
    AthenaTaskCreate,
    AthenaTaskList,
)
from polaris.shared.auth import require_admin

router = APIRouter(prefix="/api/athena", tags=["athena"])
admin_router = APIRouter(prefix="/api/agent", tags=["agent"])


def require_agent(
    authorization: str | None = Header(default=None),
    x_api_token: str | None = Header(default=None, alias="X-API-Token"),
) -> None:
    """Токен принимается как `Authorization: Bearer <token>` или `X-API-Token: <token>`."""
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    elif x_api_token:
        token = x_api_token.strip()
    if not tokens.verify(token):
        raise HTTPException(status_code=401, detail="Invalid or missing API token")


# ─── для агента ───


@router.get("/health")
def health():
    """Без токена: жив ли сервис и задан ли токен."""
    return {"status": "ok", "service": "polaris", "token_configured": tokens.status()["configured"]}


@router.get("/auth/verify")
def verify(_: None = Depends(require_agent)):
    """Проверка токена: 200, если токен верный."""
    return {"valid": True}


@router.get("/tasks/important", response_model=AthenaTaskList)
def important_tasks(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: None = Depends(require_agent),
):
    """Невыполненные задачи с приоритетом «срочно» или «важно», по срочности."""
    return service.list_tasks(important_only=True, limit=limit, offset=offset)


@router.get("/tasks", response_model=AthenaTaskList)
def list_tasks(
    status: str = Query(default="active", description="active (по умолчанию) | all | todo | in-progress | waiting | done"),
    priority: str | None = Query(default=None, description="fire | important | normal | someday"),
    project: str | None = None,
    tag: str | None = None,
    date_from: str | None = Query(default=None, description="YYYY-MM-DD"),
    date_to: str | None = Query(default=None, description="YYYY-MM-DD"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: None = Depends(require_agent),
):
    """Все задачи (по умолчанию без выполненных), по срочности."""
    try:
        return service.list_tasks(
            status=status, priority=priority, project=project, tag=tag,
            date_from=date_from, date_to=date_to, limit=limit, offset=offset,
        )
    except logic.ValidationFailed as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/tasks/{task_id}", response_model=AthenaTask)
def get_task(task_id: str, _: None = Depends(require_agent)):
    task = service.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.post("/tasks", response_model=AthenaTask, status_code=201)
def create_task(body: AthenaTaskCreate, _: None = Depends(require_agent)):
    """Создать задачу. Обязателен только title; дата, время, приоритет и т.д. — по желанию."""
    try:
        return service.create_task(body.model_dump())
    except logic.ValidationFailed as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ─── календарь (только чтение) ───


def _calendar(call, *args, types: str | None, important: bool, include_done: bool, **kw):
    try:
        return call(*args, types=logic.parse_types(types), important_only=important, include_done=include_done, **kw)
    except logic.ValidationFailed as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


_TYPES_DOC = "Типы через запятую: event, task, reminder, payment, subscription. Пусто — все"


@router.get("/calendar/day", response_model=AthenaCalendarDayResponse)
def calendar_day(
    date: str = Query(..., description="YYYY-MM-DD"),
    types: str | None = Query(default=None, description=_TYPES_DOC),
    important: bool = Query(default=False, description="Только важные"),
    include_done: bool = Query(default=False, description="Включить выполненные задачи"),
    _: None = Depends(require_agent),
):
    """Всё на конкретный день: события, задачи с этой датой, напоминания, платежи, подписки."""
    return _calendar(service.calendar_day, date, types=types, important=important, include_done=include_done)


@router.get("/calendar/month", response_model=AthenaCalendarPeriod)
def calendar_month(
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    types: str | None = Query(default=None, description=_TYPES_DOC),
    important: bool = Query(default=False, description="Только важные"),
    include_done: bool = Query(default=False, description="Включить выполненные задачи"),
    _: None = Depends(require_agent),
):
    """Весь месяц, сгруппированный по дням (только дни, где что-то есть)."""
    return _calendar(service.calendar_month, year, month, types=types, important=important, include_done=include_done)


@router.get("/calendar/upcoming", response_model=AthenaCalendarPeriod)
def calendar_upcoming(
    days: int = Query(default=30, ge=1, le=90, description="Сколько дней вперёд, считая сегодня"),
    types: str | None = Query(default=None, description=_TYPES_DOC),
    important: bool = Query(default=False, description="Только важные"),
    include_done: bool = Query(default=False, description="Включить выполненные задачи"),
    _: None = Depends(require_agent),
):
    """Следующие N дней (по умолчанию 30), сгруппировано по дням."""
    return _calendar(service.calendar_upcoming, days, types=types, important=important, include_done=include_done)


@router.get("/calendar/next", response_model=AthenaCalendarNext)
def calendar_next(
    limit: int = Query(default=5, ge=1, le=50, description="Сколько ближайших записей вернуть"),
    types: str | None = Query(default=None, description=_TYPES_DOC),
    important: bool = Query(default=False, description="Только важные"),
    include_done: bool = Query(default=False, description="Включить выполненные задачи"),
    _: None = Depends(require_agent),
):
    """Что ближайшее: одним списком по времени (например, types=event — ближайшие события)."""
    return _calendar(service.calendar_next, limit, types=types, important=important, include_done=include_done)


# ─── для владельца (экран настроек «Агент») ───


@admin_router.get("")
def agent_status(_user=Depends(require_admin)):
    return {"success": True, "data": tokens.status()}


@admin_router.post("/token")
def agent_generate_token(_user=Depends(require_admin)):
    """Новый токен (старый перестаёт работать). Токен возвращается один раз."""
    return {"success": True, "data": tokens.generate()}


@admin_router.delete("/token")
def agent_revoke_token(_user=Depends(require_admin)):
    tokens.revoke()
    return {"success": True, "data": tokens.status()}
