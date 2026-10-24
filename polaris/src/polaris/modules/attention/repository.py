"""Repository for Attention Engine: aggregates items from sources and stores user actions."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from polaris.infra.database import get_db
from polaris.modules.attention.models import (
    AttentionItem,
    AttentionFeed,
    AttentionSource,
    AttentionType,
    Urgency,
    UserAction,
    calculate_score,
)


# ─── DB schema for user actions ───
def _ensure_actions_table() -> None:
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS attention_actions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                item_id TEXT NOT NULL,
                action TEXT NOT NULL,          -- dismiss, snooze, pin
                snoozed_until TEXT,            -- ISO datetime for snooze
                created_at TEXT NOT NULL,
                UNIQUE(user_id, item_id, action)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_attention_actions_user_item ON attention_actions(user_id, item_id)"
        )


def _row_to_action(row) -> dict[str, Any]:
    return dict(row)


# ─── Public API ───
def get_user_actions(user_id: str) -> list[dict[str, Any]]:
    _ensure_actions_table()
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM attention_actions WHERE user_id = ?", (user_id,)
        ).fetchall()
        return [_row_to_action(r) for r in rows]


def set_user_action(
    user_id: str, item_id: str, action: UserAction, snoozed_until: Optional[str] = None
) -> dict[str, Any]:
    _ensure_actions_table()
    now = datetime.now(timezone.utc).isoformat()
    action_id = str(uuid.uuid4())
    with get_db() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO attention_actions
            (id, user_id, item_id, action, snoozed_until, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (action_id, user_id, item_id, action.value, snoozed_until, now),
        )
        row = conn.execute(
            "SELECT * FROM attention_actions WHERE id = ?", (action_id,)
        ).fetchone()
        return _row_to_action(row)


def delete_user_action(user_id: str, item_id: str, action: UserAction) -> bool:
    _ensure_actions_table()
    with get_db() as conn:
        cur = conn.execute(
            "DELETE FROM attention_actions WHERE user_id = ? AND item_id = ? AND action = ?",
            (user_id, item_id, action.value),
        )
        return cur.rowcount > 0


# ─── Aggregation from source modules ───
def _calendar_to_items(user_id: str) -> list[AttentionItem]:
    from polaris.modules.calendar.service import get_upcoming_days
    from polaris.modules.calendar.models import CalendarEvent

    items: list[AttentionItem] = []
    upcoming = get_upcoming_days(14)
    for day in upcoming:
        for ev_dict in day.get("events", []):
            ev = CalendarEvent(**ev_dict)
            if ev.type.value in ("task", "reminder", "event"):
                atype = {
                    "task": AttentionType.TASK,
                    "reminder": AttentionType.REMINDER,
                    "event": AttentionType.EVENT,
                }[ev.type.value]
            elif ev.type.value == "payment":
                atype = AttentionType.PAYMENT
            elif ev.type.value == "subscription":
                atype = AttentionType.SUBSCRIPTION
            else:
                atype = AttentionType.TASK

            item = AttentionItem(
                id=f"cal-{ev.id}",
                source=AttentionSource.CALENDAR,
                type=atype,
                title=ev.title,
                description=ev.description,
                due_date=ev.date,
                due_time=ev.time,
                payload={"source_id": ev.source_id, "url": ev.url},
                url=ev.url,
            )
            item.score = calculate_score(item)
            items.append(item)
    return items


def _finance_to_items(user_id: str) -> list[AttentionItem]:
    from polaris.modules.finance.service import get_attention_candidates
    from polaris.modules.finance.models import AttentionCandidate

    items: list[AttentionItem] = []
    for cand in get_attention_candidates():
        if cand.overdue:
            atype = AttentionType.PAYMENT_OVERDUE
            urgency = Urgency.CRITICAL
        else:
            atype = AttentionType.PAYMENT_DUE
            urgency = Urgency.HIGH if cand.days_until <= 3 else Urgency.MEDIUM

        item = AttentionItem(
            id=f"fin-{cand.id}",
            source=AttentionSource.FINANCE,
            type=atype,
            title=cand.name,
            description=f"{cand.amount} {cand.currency}",
            urgency=urgency,
            due_date=cand.next_payment_date,
            payload={"amount": cand.amount, "currency": cand.currency, "days_until": cand.days_until},
            url=f"/finance#payment-{cand.id}",
        )
        item.score = calculate_score(item)
        items.append(item)
    return items


def _servers_to_items(user_id: str) -> list[AttentionItem]:
    from polaris.modules.servers.service import get_attention_candidates

    items: list[AttentionItem] = []
    for cand in get_attention_candidates():
        if cand["type"] == "agent_offline":
            atype = AttentionType.SERVER_OFFLINE
            urgency = Urgency.CRITICAL
        elif cand["type"] == "disk_threshold":
            severity = cand.get("severity", "warning")
            atype = AttentionType.DISK_CRITICAL if severity == "critical" else AttentionType.DISK_WARNING
            urgency = Urgency.CRITICAL if severity == "critical" else Urgency.HIGH
        elif cand["type"] == "service_down":
            atype = AttentionType.SERVICE_FAILED if cand.get("severity") == "critical" else AttentionType.SERVICE_DOWN
            urgency = Urgency.CRITICAL if cand.get("severity") == "critical" else Urgency.HIGH
        else:
            continue

        item = AttentionItem(
            id=f"srv-{cand['server_id']}-{cand['type']}",
            source=AttentionSource.SERVERS,
            type=atype,
            title=f"{cand['server_name']}: {cand['type']}",
            description="",
            urgency=urgency,
            payload=cand,
            url=f"/servers/{cand['server_id']}",
        )
        item.score = calculate_score(item)
        items.append(item)
    return items


def get_feed(user_id: str) -> AttentionFeed:
    """Collect, filter by user actions, score, sort."""
    all_items: list[AttentionItem] = []
    all_items.extend(_calendar_to_items(user_id))
    all_items.extend(_finance_to_items(user_id))
    all_items.extend(_servers_to_items(user_id))

    # Load user actions
    actions = get_user_actions(user_id)
    dismissed = {a["item_id"] for a in actions if a["action"] == UserAction.DISMISS.value}
    snoozed = {
        a["item_id"]: a["snoozed_until"]
        for a in actions
        if a["action"] == UserAction.SNOOZE.value and a["snoozed_until"]
    }
    pinned = {a["item_id"] for a in actions if a["action"] == UserAction.PIN.value}

    now_iso = datetime.now(timezone.utc).isoformat()

    filtered: list[AttentionItem] = []
    for item in all_items:
        if item.id in dismissed:
            continue
        if item.id in snoozed:
            try:
                if datetime.fromisoformat(snoozed[item.id].replace("Z", "+00:00")) > datetime.now(timezone.utc):
                    continue
            except Exception:
                pass
        if item.id in pinned:
            item.pinned = True
        filtered.append(item)

    # Sort by score desc, then due_date asc
    filtered.sort(key=lambda x: (-x.score, x.due_date or "9999-99-99"))

    critical = sum(1 for i in filtered if i.urgency == Urgency.CRITICAL)
    high = sum(1 for i in filtered if i.urgency == Urgency.HIGH)

    return AttentionFeed(items=filtered, total=len(filtered), critical_count=critical, high_count=high)