"""SQLite-слой модуля Mail. Все функции открывают короткое соединение (get_db)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from polaris.infra.database import get_db, run_migration

MIGRATION_FILE = Path(__file__).resolve().parents[4] / "database" / "migrations" / "006_create_mail.sql"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_in(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_schema() -> None:
    """Демон работает в процессе бота и может стартовать раньше web — создаём таблицы сами."""
    run_migration(MIGRATION_FILE.read_text(encoding="utf-8"))


# ───────────────────────────── accounts ─────────────────────────────

def _account_row(row, include_secret: bool) -> dict[str, Any]:
    data = dict(row)
    data["enabled"] = bool(data["enabled"])
    if not include_secret:
        data.pop("password", None)
    return data


def list_accounts(include_secret: bool = False) -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT a.*, (SELECT COUNT(*) FROM mail_messages m WHERE m.account_id = a.id) AS message_count
               FROM mail_accounts a ORDER BY a.id"""
        ).fetchall()
    return [_account_row(r, include_secret) for r in rows]


def get_account(account_id: int, include_secret: bool = False) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM mail_accounts WHERE id = ?", (account_id,)).fetchone()
    return _account_row(row, include_secret) if row else None


def create_account(provider: str, address: str, label: str, password: str) -> dict[str, Any]:
    now = now_iso()
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO mail_accounts (provider, email, label, password, enabled, created_at, updated_at)
               VALUES (?, ?, ?, ?, 1, ?, ?)""",
            (provider, address, label, password, now, now),
        )
        account_id = cur.lastrowid
    return get_account(account_id) or {}


def update_account(account_id: int, fields: dict[str, Any]) -> dict[str, Any] | None:
    allowed = {"label", "password", "enabled"}
    sets, values = [], []
    for key, value in fields.items():
        if key not in allowed:
            continue
        sets.append(f"{key} = ?")
        values.append(int(value) if key == "enabled" else value)
    if not sets:
        return get_account(account_id)
    # смена пароля/включение — статус сбрасываем, воркер перепроверит
    if "password" in fields or fields.get("enabled"):
        sets += ["last_status = 'pending'", "last_error = ''"]
    sets.append("updated_at = ?")
    values.append(now_iso())
    values.append(account_id)
    with get_db() as conn:
        conn.execute(f"UPDATE mail_accounts SET {', '.join(sets)} WHERE id = ?", values)
    return get_account(account_id)


def delete_account(account_id: int) -> bool:
    with get_db() as conn:
        cur = conn.execute("DELETE FROM mail_accounts WHERE id = ?", (account_id,))
        return cur.rowcount > 0


def set_account_status(account_id: int, status: str, error: str = "") -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE mail_accounts SET last_status = ?, last_error = ?, last_checked_at = ? WHERE id = ?",
            (status, error[:300], now_iso(), account_id),
        )


# ───────────────────────────── read position ─────────────────────────────

def get_state(account_id: int) -> tuple[str, int] | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT uidvalidity, last_uid FROM mail_account_state WHERE account_id = ?", (account_id,)
        ).fetchone()
    return (row["uidvalidity"], int(row["last_uid"])) if row else None


def _save_state(conn, account_id: int, uidvalidity: str, last_uid: int) -> None:
    conn.execute(
        """INSERT INTO mail_account_state (account_id, uidvalidity, last_uid) VALUES (?, ?, ?)
           ON CONFLICT(account_id) DO UPDATE SET uidvalidity = excluded.uidvalidity, last_uid = excluded.last_uid""",
        (account_id, str(uidvalidity), int(last_uid)),
    )


def save_state(account_id: int, uidvalidity: str, last_uid: int) -> None:
    with get_db() as conn:
        _save_state(conn, account_id, uidvalidity, last_uid)


# ───────────────────────────── destinations ─────────────────────────────

_DEST_SELECT = """
    SELECT d.*,
      (SELECT COUNT(*) FROM mail_deliveries x WHERE x.destination_id = d.id AND x.status = 'sent')    AS sent_count,
      (SELECT COUNT(*) FROM mail_deliveries x WHERE x.destination_id = d.id AND x.status = 'pending') AS pending_count,
      (SELECT COUNT(*) FROM mail_deliveries x WHERE x.destination_id = d.id AND x.status = 'failed')  AS failed_count
    FROM mail_destinations d
"""


def _dest_row(row) -> dict[str, Any]:
    data = dict(row)
    data["enabled"] = bool(data["enabled"])
    return data


def list_destinations() -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(_DEST_SELECT + " ORDER BY d.id").fetchall()
    return [_dest_row(r) for r in rows]


def get_destination(destination_id: int) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute(_DEST_SELECT + " WHERE d.id = ?", (destination_id,)).fetchone()
    return _dest_row(row) if row else None


def create_destination(label: str, raw: str, chat_id: str, thread_id: int | None, kind: str) -> dict[str, Any]:
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO mail_destinations (label, raw, chat_id, thread_id, kind, enabled, created_at)
               VALUES (?, ?, ?, ?, ?, 1, ?)""",
            (label, raw, chat_id, thread_id, kind, now_iso()),
        )
        destination_id = cur.lastrowid
    return get_destination(destination_id) or {}


def update_destination(destination_id: int, fields: dict[str, Any]) -> dict[str, Any] | None:
    sets, values = [], []
    for key in ("label", "enabled"):
        if key in fields:
            sets.append(f"{key} = ?")
            values.append(int(fields[key]) if key == "enabled" else fields[key])
    if sets:
        values.append(destination_id)
        with get_db() as conn:
            conn.execute(f"UPDATE mail_destinations SET {', '.join(sets)} WHERE id = ?", values)
    return get_destination(destination_id)


def delete_destination(destination_id: int) -> bool:
    with get_db() as conn:
        return conn.execute("DELETE FROM mail_destinations WHERE id = ?", (destination_id,)).rowcount > 0


def set_destination_status(destination_id: int, status: str, error: str = "") -> None:
    with get_db() as conn:
        if status == "ok":
            conn.execute(
                "UPDATE mail_destinations SET last_status = 'ok', last_error = '', last_sent_at = ? WHERE id = ?",
                (now_iso(), destination_id),
            )
        else:
            conn.execute(
                "UPDATE mail_destinations SET last_status = ?, last_error = ? WHERE id = ?",
                (status, error[:300], destination_id),
            )


def enabled_destination_ids() -> list[int]:
    with get_db() as conn:
        rows = conn.execute("SELECT id FROM mail_destinations WHERE enabled = 1 ORDER BY id").fetchall()
    return [int(r["id"]) for r in rows]


# ───────────────────────────── messages ─────────────────────────────

def ingest_message(
    account_id: int,
    uidvalidity: str,
    uid: int,
    parsed: dict[str, Any],
    destination_ids: list[int],
    advance: bool = True,
) -> int | None:
    """Сохраняет письмо, ставит его в очередь доставки и двигает позицию чтения — одной транзакцией.

    Позиция чтения уходит вперёд только вместе с сохранённым письмом: сбой Telegram
    письмо не теряет (доставка повторяется из очереди независимо от IMAP).
    """
    with get_db() as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO mail_messages
               (account_id, uidvalidity, uid, subject, sender, recipient, date_text, received_at, body, attachments)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                account_id, str(uidvalidity), int(uid),
                parsed.get("subject", ""), parsed.get("sender", ""), parsed.get("recipient", ""),
                parsed.get("date_text", ""), now_iso(), parsed.get("body", ""),
                json.dumps(parsed.get("attachments") or [], ensure_ascii=False),
            ),
        )
        message_id: int | None = cur.lastrowid if cur.rowcount else None
        if message_id is not None:
            now = now_iso()
            for destination_id in destination_ids:
                conn.execute(
                    """INSERT OR IGNORE INTO mail_deliveries (message_id, destination_id, next_attempt_at)
                       VALUES (?, ?, ?)""",
                    (message_id, destination_id, now),
                )
        if advance:
            _save_state(conn, account_id, uidvalidity, uid)
    return message_id


def prune_messages(account_id: int, keep: int) -> None:
    with get_db() as conn:
        conn.execute(
            """DELETE FROM mail_messages WHERE account_id = ? AND id NOT IN
               (SELECT id FROM mail_messages WHERE account_id = ? ORDER BY id DESC LIMIT ?)""",
            (account_id, account_id, keep),
        )


def _message_row(row) -> dict[str, Any]:
    data = dict(row)
    try:
        data["attachments"] = json.loads(data.get("attachments") or "[]")
    except ValueError:
        data["attachments"] = []
    return data


def list_messages(account_id: int | None = None, limit: int = 30, before_id: int | None = None) -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT m.*, a.email AS account_email, a.label AS account_label, a.provider AS provider,
                 (SELECT COUNT(*) FROM mail_deliveries d WHERE d.message_id = m.id AND d.status = 'sent')    AS sent_count,
                 (SELECT COUNT(*) FROM mail_deliveries d WHERE d.message_id = m.id AND d.status = 'pending') AS pending_count,
                 (SELECT COUNT(*) FROM mail_deliveries d WHERE d.message_id = m.id AND d.status = 'failed')  AS failed_count
               FROM mail_messages m JOIN mail_accounts a ON a.id = m.account_id
               WHERE (? IS NULL OR m.account_id = ?) AND (? IS NULL OR m.id < ?)
               ORDER BY m.id DESC LIMIT ?""",
            (account_id, account_id, before_id, before_id, limit),
        ).fetchall()
    return [_message_row(r) for r in rows]


# ───────────────────────────── deliveries ─────────────────────────────

def due_deliveries(limit: int = 10) -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT d.id AS delivery_id, d.attempts, d.destination_id,
                      dest.chat_id, dest.thread_id, dest.kind,
                      m.subject, m.sender, m.recipient, m.date_text, m.body, m.attachments,
                      a.email AS account_email, a.label AS account_label
               FROM mail_deliveries d
               JOIN mail_destinations dest ON dest.id = d.destination_id AND dest.enabled = 1
               JOIN mail_messages m ON m.id = d.message_id
               JOIN mail_accounts a ON a.id = m.account_id
               WHERE d.status = 'pending' AND d.next_attempt_at <= ?
               ORDER BY d.id LIMIT ?""",
            (now_iso(), limit),
        ).fetchall()
    return [_message_row(r) for r in rows]


def mark_delivery_sent(delivery_id: int) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE mail_deliveries SET status = 'sent', sent_at = ?, last_error = '' WHERE id = ?",
            (now_iso(), delivery_id),
        )


def mark_delivery_retry(delivery_id: int, attempts: int, delay_seconds: int, error: str) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE mail_deliveries SET attempts = ?, next_attempt_at = ?, last_error = ? WHERE id = ?",
            (attempts, iso_in(delay_seconds), error[:300], delivery_id),
        )


def mark_delivery_failed(delivery_id: int, attempts: int, error: str) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE mail_deliveries SET status = 'failed', attempts = ?, last_error = ? WHERE id = ?",
            (attempts, error[:300], delivery_id),
        )
