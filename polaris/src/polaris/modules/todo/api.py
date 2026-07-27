from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from polaris.integrations.telegram.auth import TelegramWebAppUser
from polaris.api.app import require_admin
from polaris.modules.todo.models import TaskCreate, TaskUpdate
from polaris.modules.todo import service

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("")
def list_tasks(_user: TelegramWebAppUser | None = Depends(require_admin)):
    """Get all tasks, projects, and tags."""
    result = service.get_full_task_list()
    return {
        "success": True,
        "data": result.model_dump(),
    }


@router.post("")
def create_task(body: TaskCreate, _user: TelegramWebAppUser | None = Depends(require_admin)):
    """Create a new task."""
    try:
        task = service.create_task(body)
        return {
            "success": True,
            "data": task.model_dump(),
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{task_id}")
def get_task(task_id: str, _user: TelegramWebAppUser | None = Depends(require_admin)):
    """Get a single task by ID."""
    task = service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        "success": True,
        "data": task.model_dump(),
    }


@router.patch("/{task_id}")
def update_task(
    task_id: str,
    body: TaskUpdate,
    _user: TelegramWebAppUser | None = Depends(require_admin),
):
    """Update a task."""
    task = service.update_task(task_id, body)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        "success": True,
        "data": task.model_dump(),
    }


@router.delete("/{task_id}")
def delete_task(task_id: str, _user: TelegramWebAppUser | None = Depends(require_admin)):
    """Delete a task."""
    deleted = service.delete_task(task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        "success": True,
        "message": "Task deleted",
    }


@router.post("/projects")
def create_project(
    body: dict,
    _user: TelegramWebAppUser | None = Depends(require_admin),
):
    """Create a new project."""
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Project name is required")
    color = body.get("color", "#5ab8ff")
    project = service.create_project(name, color)
    return {
        "success": True,
        "data": project.model_dump(),
    }


@router.post("/tags")
def create_tag(
    body: dict,
    _user: TelegramWebAppUser | None = Depends(require_admin),
):
    """Create a new tag."""
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Tag name is required")
    tag = service.create_tag(name)
    return {
        "success": True,
        "data": tag.model_dump(),
    }