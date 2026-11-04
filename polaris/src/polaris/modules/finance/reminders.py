"""Напоминания об оплате: за 3 дня (15:00), за день (15:00) и в день платежа (10:00).

Логика расписания — чистая функция `due_reminders`; журнал отправленного хранится в таблице
notification_log, поэтому повторов нет даже после перезапуска бота.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from polaris.infra.database import get_db

# за сколько дней до платежа -> во сколько (по часовому поясу NOTIFY_TIMEZONE) отправить
SCHEDULE: dict[int, time] = {
    3: time(15, 0),
    1: time(15, 0),
    0: time(10, 0),
}

LOG_KEEP_DAYS = 120


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def due_reminders(payments: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """Какие напоминания пора отправить сейчас. `now` — с часовым поясом пользователя.

    Напоминание отправляется в своё время и «догоняется» до конца того же дня, если бот был
    выключен. Не шлём напоминание, время которого было раньше, чем платёж добавили."""
    today = now.date()
    result: list[dict[str, Any]] = []
    for payment in payments:
        if payment.get("status") != "active":
            continue
        try:
            due = date.fromisoformat(str(payment.get("next_payment_date", ""))[:10])
        except ValueError:
            continue
        stage = (due - today).days
        slot = SCHEDULE.get(stage)
        if slot is None or now.time() < slot:
            continue
        slot_at = datetime.combine(today, slot, tzinfo=now.tzinfo)
        created = _parse_iso(payment.get("created_at"))
        if created and created > slot_at:
            continue  # платёж добавили уже после этого времени — «задним числом» не напоминаем
        result.append({
            "key": f"payment:{payment['id']}:{due.isoformat()}:{stage}",
            "payment": payment,
            "stage": stage,
            "due_date": due.isoformat(),
        })
    return result


# ───────── журнал отправленного ─────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def sent_keys(prefix: str = "payment:") -> set[str]:
    with get_db() as conn:
        rows = conn.execute("SELECT key FROM notification_log WHERE key LIKE ?", (prefix + "%",)).fetchall()
        return {r["key"] for r in rows}


def mark_sent(key: str) -> None:
    with get_db() as conn:
        conn.execute("INSERT OR IGNORE INTO notification_log (key, sent_at) VALUES (?, ?)", (key, _now_iso()))


def prune(older_than_days: int = LOG_KEEP_DAYS) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    with get_db() as conn:
        return conn.execute("DELETE FROM notification_log WHERE sent_at < ?", (cutoff,)).rowcount
