"""Athena API: изменение и завершение задач, напоминания, события — логика, сервис на настоящей SQLite и обработчики.

Обработчики вызываются напрямую (без HTTP): FastAPI-часть здесь заменена заглушкой из fastapi_stub,
HTTP-уровень с настоящим TestClient покрыт в test_athena.py."""
from __future__ import annotations

import contextlib
import importlib
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from polaris.infra import database
from polaris.modules.athena import logic
from tests import fastapi_stub

MIGRATIONS = Path(__file__).resolve().parents[1] / "database" / "migrations"
NEEDED = ("001_create_tasks.sql", "002_create_events.sql", "003_create_recurring_payments.sql",
          "008_create_task_reminders.sql", "009_create_agent_token.sql")


def load_api():
    """polaris.modules.athena.api поверх заглушки FastAPI."""
    names = ("fastapi", "fastapi.responses", "fastapi.concurrency", "polaris.shared.auth", "polaris.modules.athena.api")
    saved = {n: sys.modules.get(n) for n in names}
    sys.modules["fastapi"], sys.modules["fastapi.responses"], sys.modules["fastapi.concurrency"] = fastapi_stub._stub_modules()
    for n in names[3:]:
        sys.modules.pop(n, None)
    try:
        return importlib.import_module("polaris.modules.athena.api")
    finally:
        for n, module in saved.items():
            if module is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = module


api = load_api()
from polaris.modules.athena import models, service  # noqa: E402


class DbCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        db_file = Path(self._tmp.name) / "t.db"
        for patch in (mock.patch.object(database, "DB_PATH", db_file), mock.patch.object(database, "_wal_ready", set())):
            patch.start()
            self.addCleanup(patch.stop)
        conn = sqlite3.connect(db_file)
        for name in NEEDED:
            conn.executescript((MIGRATIONS / name).read_text(encoding="utf-8"))
        conn.close()

    def new_task(self, **kw):
        return service.create_task({"title": "Задача", **kw})


# ───────────── логика ─────────────


class UpdatePayloadTests(unittest.TestCase):
    def test_only_passed_fields_and_null_means_no_change(self):
        out = logic.build_update_payload({"title": " Новое ", "priority": "fire", "date": None}, [])
        self.assertEqual(out, {"title": "Новое", "priority": "fire"})

    def test_empty_string_clears_date_time_project(self):
        out = logic.build_update_payload({"date": "", "time": "", "project": ""}, [{"name": "P", "color": "#111"}])
        self.assertEqual(out, {"date": "", "time": "", "project": "", "project_color": ""})

    def test_nothing_to_change_and_bad_values_are_explained(self):
        for bad in ({}, {"date": None}):
            with self.assertRaisesRegex(logic.ValidationFailed, "нет полей"):
                logic.build_update_payload(bad, [])
        for bad, text in (({"title": "  "}, "title"), ({"date": "31.10.2026"}, "date"), ({"time": "25:00"}, "time"),
                          ({"priority": "urgent"}, "priority"), ({"status": "finished"}, "status"),
                          ({"energy": "huge"}, "energy"), ({"repeat": "hourly"}, "repeat"), ({"tags": ["x" * 41]}, "tags")):
            with self.assertRaisesRegex(logic.ValidationFailed, text):
                logic.build_update_payload(bad, [])

    def test_project_takes_existing_spelling_and_color(self):
        out = logic.build_update_payload({"project": "polaris"}, [{"name": "Polaris", "color": "#5ab8ff"}])
        self.assertEqual((out["project"], out["project_color"]), ("Polaris", "#5ab8ff"))

    def test_checklist_accepts_strings_and_api_objects_keeping_done(self):
        out = logic.build_update_payload({"checklist": ["a", {"text": "b", "done": True}, "", {"text": " "}]}, [])
        self.assertEqual(out["checklist"], [{"text": "a", "done": False}, {"text": "b", "done": True}])


class RemindAtTests(unittest.TestCase):
    def test_naive_value_is_server_wall_time(self):
        out = logic.normalize_remind_at("2026-11-05T09:30")
        parsed = datetime.fromisoformat(out)
        self.assertIsNotNone(parsed.tzinfo)
        self.assertEqual(parsed.replace(tzinfo=None), datetime(2026, 11, 5, 9, 30))

    def test_aware_value_keeps_the_same_instant(self):
        for raw in ("2026-11-05T09:30:00+03:00", "2026-11-05T06:30:00Z"):
            self.assertEqual(datetime.fromisoformat(logic.normalize_remind_at(raw)),
                             datetime.fromisoformat("2026-11-05T06:30:00+00:00"))

    def test_garbage_and_date_only_are_rejected(self):
        for bad in ("", "завтра", "2026-11-05", "05.11.2026 09:00", "2026-13-01T10:00"):
            with self.assertRaises(logic.ValidationFailed, msg=bad):
                logic.normalize_remind_at(bad)


class EventPayloadTests(unittest.TestCase):
    def test_create_requires_title_and_date_and_validates_times(self):
        ok = logic.build_event_payload({"title": " Созвон ", "date": "2026-11-05", "time": "10:00", "end_time": "11:00", "tags": ["#a"]}, [])
        self.assertEqual((ok["title"], ok["time"], ok["end_time"], ok["tags"]), ("Созвон", "10:00", "11:00", ["a"]))
        for bad, text in (({"date": "2026-11-05"}, "title"), ({"title": "x"}, "date"), ({"title": "x", "date": "2026-02-30"}, "date"),
                          ({"title": "x", "date": "2026-11-05", "time": "9:00"}, "time"),
                          ({"title": "x", "date": "2026-11-05", "end_time": "10:00"}, "end_time"),
                          ({"title": "x", "date": "2026-11-05", "time": "10:00", "end_time": "09:00"}, "позже"),
                          ({"title": "x", "date": "2026-11-05", "time": "10:00", "end_time": "10:00"}, "позже")):
            with self.assertRaisesRegex(logic.ValidationFailed, text):
                logic.build_event_payload(bad, [])

    def test_all_day_event_is_allowed(self):
        out = logic.build_event_payload({"title": "День рождения", "date": "2026-11-05"}, [])
        self.assertEqual((out["time"], out["end_time"]), ("", ""))

    def test_update_checks_times_only_when_they_change(self):
        existing = {"time": "10:00", "end_time": "09:00"}  # так могло получиться в интерфейсе
        self.assertEqual(logic.build_event_update({"title": "Новое"}, [], existing), {"title": "Новое"})
        with self.assertRaisesRegex(logic.ValidationFailed, "позже"):
            logic.build_event_update({"end_time": "09:30"}, [], {"time": "10:00", "end_time": ""})
        with self.assertRaisesRegex(logic.ValidationFailed, "end_time"):
            logic.build_event_update({"time": ""}, [], {"time": "10:00", "end_time": "11:00"})
        self.assertEqual(logic.build_event_update({"time": "", "end_time": ""}, [], {"time": "10:00", "end_time": "11:00"}),
                         {"time": "", "end_time": ""})
        with self.assertRaisesRegex(logic.ValidationFailed, "date"):
            logic.build_event_update({"date": ""}, [], existing)


# ───────────── сервис на SQLite ─────────────


class TaskEditTests(DbCase):
    def test_partial_update_changes_only_given_fields(self):
        task = self.new_task(description="d", priority="normal", tags=["a"], project="P", date="2026-11-05")
        updated = service.update_task(task["id"], {"title": "Другое", "priority": "fire"})
        self.assertEqual((updated["title"], updated["priority"], updated["important"]), ("Другое", "fire", True))
        self.assertEqual((updated["description"], updated["tags"], updated["project"], updated["date"]), ("d", ["a"], "P", "2026-11-05"))
        self.assertEqual(service.get_task(task["id"])["title"], "Другое")

    def test_clearing_and_replacing_lists(self):
        task = self.new_task(date="2026-11-05", time="10:00", tags=["a", "b"], checklist=["x"])
        updated = service.update_task(task["id"], {"date": "", "time": "", "tags": ["c"], "checklist": [{"text": "y", "done": True}]})
        self.assertEqual((updated["date"], updated["time"], updated["tags"]), ("", "", ["c"]))
        self.assertEqual(updated["checklist"], [{"text": "y", "done": True}])

    def test_project_color_follows_existing_project(self):
        from polaris.modules.todo import repository as repo
        task = self.new_task()
        service.update_task(task["id"], {"project": "дом"})  # проект «Дом» создаётся миграцией, цвет #ff5d73
        row = repo.get_task_by_id(task["id"])
        self.assertEqual((row["project"], row["project_color"]), ("Дом", "#ff5d73"))
        service.update_task(task["id"], {"project": "Новый проект"})  # неизвестный проект: название как есть, цвета нет
        row = repo.get_task_by_id(task["id"])
        self.assertEqual((row["project"], row["project_color"]), ("Новый проект", ""))
        service.update_task(task["id"], {"project": ""})
        self.assertEqual(repo.get_task_by_id(task["id"])["project"], "")

    def test_complete_reopen_and_idempotence(self):
        task = self.new_task(priority="fire")
        done = service.complete_task(task["id"])
        self.assertEqual((done["status"], done["important"]), ("done", False))
        self.assertTrue(done["done_at"])
        again = service.complete_task(task["id"])
        self.assertEqual(again["done_at"], done["done_at"], "повторное завершение не должно сдвигать время")
        reopened = service.update_task(task["id"], {"status": "todo"})
        self.assertEqual((reopened["status"], reopened["done_at"], reopened["important"]), ("todo", "", True))

    def test_validation_errors_do_not_touch_the_task(self):
        task = self.new_task(title="Исходная")
        with self.assertRaises(logic.ValidationFailed):
            service.update_task(task["id"], {"title": "Новая", "priority": "bad"})
        self.assertEqual(service.get_task(task["id"])["title"], "Исходная")

    def test_missing_task_and_delete(self):
        self.assertIsNone(service.update_task("нет", {"title": "x"}))
        self.assertIsNone(service.complete_task("нет"))
        task = self.new_task()
        self.assertTrue(service.delete_task(task["id"]))
        self.assertIsNone(service.get_task(task["id"]))
        self.assertFalse(service.delete_task(task["id"]))


class ReminderTests(DbCase):
    def test_deleting_a_task_removes_its_reminders(self):
        task = self.new_task()
        service.add_reminder(task["id"], "2099-01-01T10:00")
        service.delete_task(task["id"])
        with database.get_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM task_reminders").fetchone()[0], 0)

    def test_add_list_delete_and_main_reminder_sync(self):
        task = self.new_task()
        later = service.add_reminder(task["id"], "2099-01-02T10:00")
        sooner = service.add_reminder(task["id"], "2099-01-01T09:00")
        listing = service.list_reminders(task["id"])
        self.assertEqual([r["id"] for r in listing], [sooner["id"], later["id"]])  # по времени
        self.assertEqual(set(later), {"id", "remind_at", "created_at"})
        from polaris.modules.todo import repository as repo
        self.assertEqual(repo.get_task_by_id(task["id"])["remind_at"], sooner["remind_at"])
        self.assertTrue(service.delete_reminder(task["id"], sooner["id"]))
        self.assertEqual(repo.get_task_by_id(task["id"])["remind_at"], later["remind_at"])
        self.assertFalse(service.delete_reminder(task["id"], sooner["id"]))
        self.assertFalse(service.delete_reminder("нет", later["id"]))

    def test_reminder_shows_in_calendar_at_the_requested_wall_time(self):
        task = self.new_task(title="Позвонить")
        service.add_reminder(task["id"], "2099-03-04T09:15")
        day = service.calendar_day("2099-03-04", types={"reminder"})
        self.assertEqual([(e["title"].endswith("Позвонить"), e["time"]) for e in day["events"]], [(True, "09:15")])

    def test_aware_value_is_shown_in_server_time(self):
        task = self.new_task()
        created = service.add_reminder(task["id"], "2099-03-04T12:00:00Z")
        expected = datetime.fromisoformat("2099-03-04T12:00:00+00:00").astimezone()
        day = service.calendar_day(expected.strftime("%Y-%m-%d"), types={"reminder"})
        self.assertEqual([e["time"] for e in day["events"]], [expected.strftime("%H:%M")])
        self.assertTrue(created["remind_at"])

    def test_errors_and_limit(self):
        task = self.new_task()
        self.assertIsNone(service.add_reminder("нет", "2099-01-01T10:00"))
        self.assertIsNone(service.list_reminders("нет"))
        with self.assertRaises(logic.ValidationFailed):
            service.add_reminder(task["id"], "когда-нибудь")
        for i in range(logic.MAX_REMINDERS):
            service.add_reminder(task["id"], f"2099-01-{i + 1:02d}T10:00")
        with self.assertRaisesRegex(logic.ValidationFailed, "предел"):
            service.add_reminder(task["id"], "2099-02-01T10:00")
        self.assertEqual(len(service.list_reminders(task["id"])), logic.MAX_REMINDERS)


class EventTests(DbCase):
    def test_create_get_update_delete_and_calendar_link(self):
        ev = service.create_event({"title": "Созвон", "date": "2099-05-05", "time": "10:00", "end_time": "11:00", "tags": ["work"]})
        self.assertEqual((ev["title"], ev["time"], ev["end_time"], ev["tags"]), ("Созвон", "10:00", "11:00", ["work"]))
        self.assertNotIn("project_color", ev)
        day = service.calendar_day("2099-05-05")
        calendar_event = next(e for e in day["events"] if e["type"] == "event")
        self.assertEqual(calendar_event["source_id"], ev["id"])  # по нему агент находит событие в /events/{id}
        self.assertTrue(calendar_event["important"])
        self.assertEqual(service.get_event(ev["id"])["title"], "Созвон")
        updated = service.update_event(ev["id"], {"title": "Созвон с командой", "time": "", "end_time": ""})
        self.assertEqual((updated["title"], updated["time"], updated["end_time"], updated["date"]), ("Созвон с командой", "", "", "2099-05-05"))
        self.assertTrue(service.delete_event(ev["id"]))
        self.assertIsNone(service.get_event(ev["id"]))
        self.assertFalse(service.delete_event(ev["id"]))

    def test_listing_filters_sorting_and_pagination(self):
        for title, day, time in (("В", "2099-05-07", "09:00"), ("А", "2099-05-05", ""), ("Б", "2099-05-05", "08:00"), ("Г", "2099-06-01", "")):
            service.create_event({"title": title, "date": day, "time": time})
        everything = service.list_events()
        self.assertEqual([e["title"] for e in everything["items"]], ["Б", "А", "В", "Г"])  # без времени — после остальных дня
        ranged = service.list_events(date_from="2099-05-06", date_to="2099-05-31")
        self.assertEqual(([e["title"] for e in ranged["items"]], ranged["total"]), (["В"], 1))
        page = service.list_events(limit=2, offset=2)
        self.assertEqual(([e["title"] for e in page["items"]], page["total"]), (["В", "Г"], 4))
        with self.assertRaises(logic.ValidationFailed):
            service.list_events(date_from="вчера")

    def test_only_real_events_and_missing_ones(self):
        self.new_task(date="2099-05-05")
        self.assertEqual(service.list_events()["total"], 0)  # задачи с датой сюда не попадают
        self.assertIsNone(service.update_event("нет", {"title": "x"}))
        self.assertIsNone(service.get_event("нет"))

    def test_invalid_update_does_not_change_event(self):
        ev = service.create_event({"title": "Созвон", "date": "2099-05-05", "time": "10:00", "end_time": "11:00"})
        with self.assertRaises(logic.ValidationFailed):
            service.update_event(ev["id"], {"title": "Новое", "end_time": "09:00"})
        self.assertEqual(service.get_event(ev["id"])["title"], "Созвон")


# ───────────── обработчики ─────────────


class HandlerTests(DbCase):
    def status_of(self, fn, *args, **kwargs):
        try:
            return 200, fn(*args, **kwargs)
        except api.HTTPException as exc:
            return exc.status_code, exc.detail

    def test_every_new_route_requires_the_agent_token(self):
        new = {"update_task", "complete_task", "delete_task", "list_task_reminders", "add_task_reminder", "delete_task_reminder",
               "list_events", "get_event", "create_event", "update_event", "delete_event"}
        import inspect
        for name in new:
            dependency = [getattr(p.default, "dependency", None) for p in inspect.signature(getattr(api, name)).parameters.values()]
            self.assertIn(api.require_agent, dependency, name)

    def test_expected_paths_exist(self):
        have = {(m, p) for m, p, _ in api.router.routes}
        for want in (("PATCH", "/api/athena/tasks/{task_id}"), ("POST", "/api/athena/tasks/{task_id}/complete"),
                     ("DELETE", "/api/athena/tasks/{task_id}"), ("GET", "/api/athena/tasks/{task_id}/reminders"),
                     ("POST", "/api/athena/tasks/{task_id}/reminders"), ("DELETE", "/api/athena/tasks/{task_id}/reminders/{reminder_id}"),
                     ("GET", "/api/athena/events"), ("GET", "/api/athena/events/{event_id}"), ("POST", "/api/athena/events"),
                     ("PATCH", "/api/athena/events/{event_id}"), ("DELETE", "/api/athena/events/{event_id}")):
            self.assertIn(want, have)

    def test_task_flow_and_error_mapping(self):
        task = self.new_task()
        status, body = self.status_of(api.update_task, task["id"], models.AthenaTaskUpdate(title="Новое"), None)
        self.assertEqual((status, body["title"]), (200, "Новое"))
        self.assertEqual(self.status_of(api.update_task, task["id"], models.AthenaTaskUpdate(), None)[0], 422)  # пустое изменение
        self.assertEqual(self.status_of(api.update_task, task["id"], models.AthenaTaskUpdate(priority="bad"), None)[0], 422)
        self.assertEqual(self.status_of(api.update_task, "нет", models.AthenaTaskUpdate(title="x"), None)[0], 404)
        self.assertEqual(self.status_of(api.complete_task, task["id"], None)[1]["status"], "done")
        self.assertEqual(self.status_of(api.complete_task, "нет", None)[0], 404)
        self.assertEqual(self.status_of(api.delete_task, task["id"], None), (200, {"deleted": True, "id": task["id"]}))
        self.assertEqual(self.status_of(api.delete_task, task["id"], None)[0], 404)

    def test_patch_distinguishes_unset_from_cleared(self):
        task = self.new_task(date="2026-11-05", description="keep")
        body = models.AthenaTaskUpdate(date="")  # явно очистить дату; описание не передано
        status, out = self.status_of(api.update_task, task["id"], body, None)
        self.assertEqual((out["date"], out["description"]), ("", "keep"))

    def test_reminder_flow_and_error_mapping(self):
        task = self.new_task()
        status, created = self.status_of(api.add_task_reminder, task["id"], models.AthenaReminderCreate(remind_at="2099-01-01T10:00"), None)
        self.assertEqual(status, 200)
        listing = self.status_of(api.list_task_reminders, task["id"], None)[1]
        self.assertEqual((listing["total"], listing["items"][0]["id"]), (1, created["id"]))
        self.assertEqual(self.status_of(api.add_task_reminder, task["id"], models.AthenaReminderCreate(remind_at="завтра"), None)[0], 422)
        self.assertEqual(self.status_of(api.add_task_reminder, "нет", models.AthenaReminderCreate(remind_at="2099-01-01T10:00"), None)[0], 404)
        self.assertEqual(self.status_of(api.list_task_reminders, "нет", None)[0], 404)
        self.assertEqual(self.status_of(api.delete_task_reminder, task["id"], created["id"], None)[1]["deleted"], True)
        self.assertEqual(self.status_of(api.delete_task_reminder, task["id"], created["id"], None)[0], 404)

    def test_event_flow_and_error_mapping(self):
        status, ev = self.status_of(api.create_event, models.AthenaEventCreate(title="Созвон", date="2099-05-05", time="10:00"), None)
        self.assertEqual((status, ev["title"]), (200, "Созвон"))
        self.assertEqual(self.status_of(api.create_event, models.AthenaEventCreate(title="x", date="2099-05-05", end_time="10:00"), None)[0], 422)
        self.assertEqual(self.status_of(api.get_event, ev["id"], None)[1]["id"], ev["id"])
        self.assertEqual(self.status_of(api.get_event, "нет", None), (404, "Event not found"))
        status, out = self.status_of(api.update_event, ev["id"], models.AthenaEventUpdate(time=""), None)
        self.assertEqual((status, out["time"]), (200, ""))
        self.assertEqual(self.status_of(api.update_event, ev["id"], models.AthenaEventUpdate(), None)[0], 422)
        self.assertEqual(self.status_of(api.update_event, "нет", models.AthenaEventUpdate(title="x"), None)[0], 404)
        listing = self.status_of(api.list_events, "2099-01-01", None, 50, 0, None)[1]
        self.assertEqual(listing["total"], 1)
        self.assertEqual(self.status_of(api.list_events, "вчера", None, 50, 0, None)[0], 422)
        self.assertEqual(self.status_of(api.delete_event, ev["id"], None)[1], {"deleted": True, "id": ev["id"]})
        self.assertEqual(self.status_of(api.delete_event, ev["id"], None)[0], 404)


if __name__ == "__main__":
    unittest.main()
