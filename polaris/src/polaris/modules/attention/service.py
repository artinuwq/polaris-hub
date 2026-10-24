"""Service layer for Attention Engine."""

from __future__ import annotations

from typing import Any

from polaris.modules.attention import repository as repo
from polaris.modules.attention.models import (
    AttentionFeed,
    AttentionItem,
    UserAction,
    UserActionRequest,
)


def get_attention_feed(user_id: str) -> AttentionFeed:
    """Get ranked attention feed for user."""
    return repo.get_feed(user_id)


def perform_user_action(user_id: str, request: UserActionRequest) -> dict[str, Any]:
    """Execute user action (dismiss/snooze/pin) on an item."""
    action = request.action
    if action == UserAction.SNOOZE and not request.snooze_until:
        raise ValueError("snooze_until required for snooze action")

    repo.set_user_action(user_id, request.item_id, action, request.snooze_until)
    return {"ok": True, "action": action.value, "item_id": request.item_id}


def remove_user_action(user_id: str, item_id: str, action: UserAction) -> bool:
    """Remove a user action (e.g., unpin, unsnooze)."""
    return repo.delete_user_action(user_id, item_id, action)