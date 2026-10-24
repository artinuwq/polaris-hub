"""API router for Attention Engine."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from polaris.integrations.telegram.auth import TelegramWebAppUser
from polaris.shared.auth import require_admin
from polaris.modules.attention import service
from polaris.modules.attention.models import (
    AttentionFeed,
    UserActionRequest,
    UserAction,
)

router = APIRouter(prefix="/api/attention", tags=["attention"])


@router.get("/feed")
def attention_feed(
    _user: TelegramWebAppUser = Depends(require_admin),
):
    """Get ranked attention feed for current user."""
    feed: AttentionFeed = service.get_attention_feed(str(_user.id))
    return {
        "success": True,
        "data": feed.model_dump(),
    }


@router.post("/action")
def attention_action(
    request: UserActionRequest,
    _user: TelegramWebAppUser = Depends(require_admin),
):
    """Perform user action on an attention item (dismiss/snooze/pin)."""
    try:
        result = service.perform_user_action(str(_user.id), request)
        return {"success": True, "data": result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/action/{item_id}/{action}")
def attention_remove_action(
    item_id: str,
    action: UserAction,
    _user: TelegramWebAppUser = Depends(require_admin),
):
    """Remove a user action (unpin, unsnooze, undismiss)."""
    ok = service.remove_user_action(str(_user.id), item_id, action)
    return {"success": True, "data": {"removed": ok}}