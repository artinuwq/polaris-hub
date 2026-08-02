from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from polaris.infra.database import get_db


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def generate_uuid() -> str:
    return str(uuid.uuid4())


def get_all_payments() -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM recurring_payments ORDER BY next_payment_date ASC"
        ).fetchall()
        return [dict(row) for row in rows]


def get_payment_by_id(payment_id: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM recurring_payments WHERE id = ?", (payment_id,)
        ).fetchone()
        return dict(row) if row else None


def create_payment(data: dict[str, Any]) -> dict[str, Any]:
    payment_id = generate_uuid()
    now = now_iso()

    with get_db() as conn:
        conn.execute(
            """INSERT INTO recurring_payments
               (id, name, description, amount, currency, billing_period,
                custom_interval_days, next_payment_date, start_date, end_date,
                category, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                payment_id,
                data.get("name", ""),
                data.get("description", ""),
                data.get("amount", 0),
                data.get("currency", "EUR"),
                data.get("billing_period", "monthly"),
                data.get("custom_interval_days"),
                data.get("next_payment_date", ""),
                data.get("start_date", ""),
                data.get("end_date"),
                data.get("category", "Other"),
                data.get("status", "active"),
                now,
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM recurring_payments WHERE id = ?", (payment_id,)
        ).fetchone()
        return dict(row)


_UPDATABLE_FIELDS = (
    "name", "description", "amount", "currency", "billing_period",
    "custom_interval_days", "next_payment_date", "start_date", "end_date",
    "category", "status",
)


def update_payment(payment_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
    existing = get_payment_by_id(payment_id)
    if not existing:
        return None

    updates = [(field, data[field]) for field in _UPDATABLE_FIELDS if field in data]
    if not updates:
        return existing

    updates.append(("updated_at", now_iso()))
    set_clause = ", ".join(f"{field} = ?" for field, _ in updates)
    values = [val for _, val in updates] + [payment_id]

    with get_db() as conn:
        conn.execute(
            f"UPDATE recurring_payments SET {set_clause} WHERE id = ?", values
        )
        row = conn.execute(
            "SELECT * FROM recurring_payments WHERE id = ?", (payment_id,)
        ).fetchone()
        return dict(row) if row else None


def delete_payment(payment_id: str) -> bool:
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM recurring_payments WHERE id = ?", (payment_id,)
        )
        return cursor.rowcount > 0
