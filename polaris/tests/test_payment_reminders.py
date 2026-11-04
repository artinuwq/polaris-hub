"""Напоминания об оплате: за 3 дня (15:00), за день (15:00), в день платежа (10:00)."""
from __future__ import annotations

import contextlib
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from polaris.modules.finance import reminders

TZ = ZoneInfo("Europe/Helsinki")
MIGRATION = Path(__file__).resolve().parents[1] / "database" / "migrations" / "010_create_notification_log.sql"


def payment(due="2026-11-09", **kw):
    base = {"id": "p1", "name": "Аренда", "status": "active", "next_payment_date": due,
            "created_at": "2026-01-01T00:00:00.000Z", "amount": 240, "currency": "RUB"}
    return {**base, **kw}


def at(day: str, hh: int, mm: int = 0) -> datetime:
    return datetime.fromisoformat(f"{day}T{hh:02d}:{mm:02d}:00").replace(tzinfo=TZ)


def stages(payments, now):
    return [r["stage"] for r in reminders.due_reminders(payments, now)]


def test_three_days_before_only_from_15():
    p = [payment("2026-11-09")]
    assert stages(p, at("2026-11-06", 14, 59)) == []
    assert stages(p, at("2026-11-06", 15, 0)) == [3]
    assert stages(p, at("2026-11-06", 23, 30)) == [3]  # бот был выключен — догоняем в тот же день


def test_one_day_before_from_15():
    p = [payment("2026-11-09")]
    assert stages(p, at("2026-11-08", 9, 0)) == []
    assert stages(p, at("2026-11-08", 15, 0)) == [1]


def test_on_the_day_from_10():
    p = [payment("2026-11-09")]
    assert stages(p, at("2026-11-09", 9, 59)) == []
    assert stages(p, at("2026-11-09", 10, 0)) == [0]


def test_other_days_and_statuses_are_silent():
    for day in ("2026-11-04", "2026-11-05", "2026-11-07", "2026-11-10", "2026-11-12"):  # 5, 4, 2 дня; просрочено
        assert stages([payment("2026-11-09")], at(day, 16)) == [], day
    assert stages([payment(status="paused")], at("2026-11-06", 16)) == []
    assert stages([payment(next_payment_date="не дата")], at("2026-11-06", 16)) == []


def test_key_is_stable_and_unique_per_cycle():
    first = reminders.due_reminders([payment("2026-11-09")], at("2026-11-06", 15))[0]["key"]
    again = reminders.due_reminders([payment("2026-11-09")], at("2026-11-06", 20))[0]["key"]
    next_month = reminders.due_reminders([payment("2026-12-09")], at("2026-12-06", 15))[0]["key"]
    assert first == again == "payment:p1:2026-11-09:3"
    assert next_month != first  # в следующем месяце напоминание придёт снова


def test_new_payment_does_not_get_reminders_for_slots_before_it_was_added():
    # платёж на 9-е добавили 6-го в 16:00 — напоминание «за 3 дня» в 15:00 уже в прошлом
    created = at("2026-11-06", 16).astimezone(timezone.utc).isoformat()
    assert stages([payment("2026-11-09", created_at=created)], at("2026-11-06", 17)) == []
    # а «за день» и «в день» по-прежнему придут
    assert stages([payment("2026-11-09", created_at=created)], at("2026-11-08", 15)) == [1]
    # добавили до 15:00 — «за 3 дня» придёт как обычно
    early = at("2026-11-06", 12).astimezone(timezone.utc).isoformat()
    assert stages([payment("2026-11-09", created_at=early)], at("2026-11-06", 15)) == [3]


def test_full_timeline_sends_each_reminder_exactly_once_at_the_right_time():
    """Прогоняем минуту за минутой с 5 по 10 ноября: ровно три напоминания в нужное время."""
    p = [payment("2026-11-09")]
    sent: set[str] = set()
    log: list[tuple[str, str]] = []
    moment = at("2026-11-05", 0, 0)
    end = at("2026-11-10", 23, 59)
    while moment <= end:
        for r in reminders.due_reminders(p, moment):
            if r["key"] not in sent:
                sent.add(r["key"])
                log.append((moment.strftime("%d.%m %H:%M"), f"за {r['stage']}"))
        moment += timedelta(minutes=1)
    assert log == [("06.11 15:00", "за 3"), ("08.11 15:00", "за 1"), ("09.11 10:00", "за 0")]


@contextlib.contextmanager
def log_db(tmp_path, monkeypatch):
    from polaris.infra import database

    db_file = tmp_path / "log.db"
    monkeypatch.setattr(database, "DB_PATH", db_file)
    conn = sqlite3.connect(db_file)
    conn.executescript(MIGRATION.read_text(encoding="utf-8"))
    conn.close()
    yield db_file


def test_log_survives_restart_and_is_per_recipient(tmp_path, monkeypatch):
    with log_db(tmp_path, monkeypatch):
        assert reminders.sent_keys() == set()
        reminders.mark_sent("payment:p1:2026-11-09:3:111")
        reminders.mark_sent("payment:p1:2026-11-09:3:111")  # повторная отметка безвредна
        reminders.mark_sent("payment:p1:2026-11-09:3:222")
        assert reminders.sent_keys() == {"payment:p1:2026-11-09:3:111", "payment:p1:2026-11-09:3:222"}
        assert reminders.sent_keys("other:") == set()


def test_prune_removes_only_old_entries(tmp_path, monkeypatch):
    with log_db(tmp_path, monkeypatch) as db_file:
        reminders.mark_sent("payment:new")
        conn = sqlite3.connect(db_file)
        conn.execute("INSERT INTO notification_log (key, sent_at) VALUES ('payment:old', '2020-01-01T00:00:00.000Z')")
        conn.commit()
        conn.close()
        assert reminders.prune(120) == 1
        assert reminders.sent_keys() == {"payment:new"}
