"""Тесты Athena: токен, логика задач и (если установлены fastapi + httpx) HTTP-слой."""
from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path

import pytest

from polaris.modules.athena import logic

MIGRATIONS = Path(__file__).resolve().parents[1] / "database" / "migrations"


@contextlib.contextmanager
def temp_db(tmp_path, monkeypatch, migrations=("001_create_tasks.sql", "008_create_task_reminders.sql", "009_create_agent_token.sql")):
    """Пустая SQLite-база с нужными миграциями, подставленная вместо рабочей."""
    from polaris.infra import database

    db_file = tmp_path / "test.db"
    monkeypatch.setattr(database, "DB_PATH", db_file)
    conn = sqlite3.connect(db_file)
    for name in migrations:
        conn.executescript((MIGRATIONS / name).read_text(encoding="utf-8"))
    conn.close()
    yield db_file


def task(**kw):
    base = {
        "id": "x", "title": "t", "priority": "normal", "status": "todo", "date": "", "time": "",
        "tags": [], "project": "", "created_at": "2026-01-01T00:00:00Z",
    }
    return {**base, **kw}


# ───────────── logic ─────────────


def test_validate_date_and_time():
    assert logic.validate_date("") == ""
    assert logic.validate_date("2026-10-31") == "2026-10-31"
    assert logic.validate_time("09:05") == "09:05"
    for bad in ("31.10.2026", "2026-13-01", "2026-02-30", "tomorrow"):
        with pytest.raises(logic.ValidationFailed):
            logic.validate_date(bad)
    for bad in ("9:05", "25:00", "12:60", "noon"):
        with pytest.raises(logic.ValidationFailed):
            logic.validate_time(bad)


def test_clean_tags_strips_hash_and_duplicates():
    assert logic.clean_tags([" #work ", "work", "", "home"]) == ["work", "home"]
    with pytest.raises(logic.ValidationFailed):
        logic.clean_tags(["x" * 41])


def test_build_payload_defaults_and_project_color():
    payload = logic.build_create_payload(
        {"title": "  Купить хлеб "}, [{"name": "Дом", "color": "#fff"}]
    )
    assert payload["title"] == "Купить хлеб"
    assert (payload["priority"], payload["status"], payload["energy"], payload["repeat"]) == ("normal", "todo", "medium", "never")

    payload = logic.build_create_payload(
        {"title": "a", "project": "дом", "checklist": ["один", " ", "два"], "date": "2026-11-01", "time": "18:30"},
        [{"name": "Дом", "color": "#abc"}],
    )
    assert payload["project"] == "Дом" and payload["project_color"] == "#abc"
    assert [c["text"] for c in payload["checklist"]] == ["один", "два"]
    assert (payload["date"], payload["time"]) == ("2026-11-01", "18:30")


@pytest.mark.parametrize(
    "data",
    [{"title": ""}, {"title": "   "}, {"title": "a", "priority": "urgent"}, {"title": "a", "energy": "huge"},
     {"title": "a", "status": "open"}, {"title": "a", "date": "завтра"}],
)
def test_build_payload_rejects(data):
    with pytest.raises(logic.ValidationFailed):
        logic.build_create_payload(data, [])


def test_important_means_fire_or_important_and_not_done():
    assert logic.is_important(task(priority="fire"))
    assert logic.is_important(task(priority="important"))
    assert not logic.is_important(task(priority="normal"))
    assert not logic.is_important(task(priority="fire", status="done"))


def test_filter_status_modes():
    tasks = [task(id="1"), task(id="2", status="done"), task(id="3", status="waiting")]
    ids = lambda items: sorted(t["id"] for t in items)  # noqa: E731
    assert ids(logic.filter_tasks(tasks)) == ["1", "3"]  # по умолчанию без выполненных
    assert ids(logic.filter_tasks(tasks, status="all")) == ["1", "2", "3"]
    assert ids(logic.filter_tasks(tasks, status="done")) == ["2"]
    with pytest.raises(logic.ValidationFailed):
        logic.filter_tasks(tasks, status="open")


def test_filter_by_fields_and_dates():
    tasks = [
        task(id="1", priority="fire", tags=["Work"], project="Дом", date="2026-11-01"),
        task(id="2", tags=["home"], date="2026-11-10"),
        task(id="3"),  # без даты
    ]
    ids = lambda items: [t["id"] for t in items]  # noqa: E731
    assert ids(logic.filter_tasks(tasks, priority="fire")) == ["1"]
    assert ids(logic.filter_tasks(tasks, tag="#work")) == ["1"]
    assert ids(logic.filter_tasks(tasks, project="дом")) == ["1"]
    assert ids(logic.filter_tasks(tasks, date_from="2026-11-05")) == ["2"]
    assert ids(logic.filter_tasks(tasks, date_to="2026-11-05")) == ["1"]  # без даты в выборку по датам не попадают


def test_sorted_by_urgency_dated_first_then_time_then_priority():
    tasks = [
        task(id="nodate"),
        task(id="later", date="2026-11-09"),
        task(id="soon-late", date="2026-11-01", time="18:00"),
        task(id="soon-early", date="2026-11-01", time="09:00"),
        task(id="soon-notime-fire", date="2026-11-01", priority="fire"),
    ]
    order = [t["id"] for t in logic.filter_tasks(tasks)]
    assert order == ["soon-early", "soon-late", "soon-notime-fire", "later", "nodate"]


def test_paginate():
    assert logic.paginate(list(range(10)), 3, 4) == [4, 5, 6]
    assert logic.paginate(list(range(3)), 5, 10) == []


# ───────────── токен ─────────────


def test_token_lifecycle(tmp_path, monkeypatch):
    from polaris.modules.athena import tokens

    monkeypatch.delenv("API_TOKEN", raising=False)
    with temp_db(tmp_path, monkeypatch) as db_file:
        assert tokens.status()["configured"] is False
        assert tokens.verify("anything") is False and tokens.verify("") is False

        first = tokens.generate()
        assert first["token"].startswith(tokens.TOKEN_PREFIX)
        assert tokens.verify(first["token"]) is True
        assert tokens.verify(first["token"] + "x") is False
        st = tokens.status()
        assert st["configured"] and st["source"] == "db" and st["hint"] == first["token"][-4:]
        assert "token" not in st  # сам токен из статуса не отдаётся

        # в базе нет открытого токена, только хеш
        raw = sqlite3.connect(db_file).execute("SELECT token_hash, token_hint FROM agent_token").fetchone()
        assert first["token"] not in raw[0] and len(raw[0]) == 64

        second = tokens.generate()
        assert tokens.verify(first["token"]) is False  # старый перестал работать
        assert tokens.verify(second["token"]) is True

        tokens.revoke()
        assert tokens.verify(second["token"]) is False
        assert tokens.status()["configured"] is False


def test_env_token_is_fallback_only(tmp_path, monkeypatch):
    from polaris.modules.athena import tokens

    monkeypatch.setenv("API_TOKEN", "env-secret-1234")
    with temp_db(tmp_path, monkeypatch):
        assert tokens.verify("env-secret-1234") is True
        assert tokens.status() == {"configured": True, "source": "env", "hint": "1234", "created_at": ""}

        generated = tokens.generate()  # токен из интерфейса вытесняет переменную окружения
        assert tokens.verify("env-secret-1234") is False
        assert tokens.verify(generated["token"]) is True

        tokens.revoke()  # а после отзыва env-токен не «оживает»
        assert tokens.verify("env-secret-1234") is False
        assert tokens.status()["configured"] is False


def test_revoke_without_existing_token_blocks_env(tmp_path, monkeypatch):
    from polaris.modules.athena import tokens

    monkeypatch.setenv("API_TOKEN", "env-secret-1234")
    with temp_db(tmp_path, monkeypatch):
        tokens.revoke()
        assert tokens.verify("env-secret-1234") is False


# ───────────── HTTP-слой (нужны fastapi, httpx, pydantic) ─────────────


@pytest.fixture
def client(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    pytest.importorskip("pydantic")
    from fastapi.testclient import TestClient

    from polaris.api.app import app
    from polaris.modules.athena import tokens

    monkeypatch.delenv("API_TOKEN", raising=False)
    with temp_db(tmp_path, monkeypatch):
        token = tokens.generate()["token"]
        with TestClient(app) as test_client:
            test_client.headers.update({"Authorization": f"Bearer {token}"})
            yield test_client


def test_health_is_public(client):
    r = client.get("/api/athena/health", headers={"Authorization": ""})
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_tasks_require_token(client):
    assert client.get("/api/athena/tasks", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/api/athena/tasks", headers={"Authorization": ""}).status_code == 401


def test_x_api_token_header_also_works(client):
    token = client.headers["Authorization"].split(" ", 1)[1]
    r = client.get("/api/athena/auth/verify", headers={"Authorization": "", "X-API-Token": token})
    assert r.status_code == 200 and r.json() == {"valid": True}


def test_create_read_and_important_flow(client):
    created = client.post("/api/athena/tasks", json={
        "title": "Сдать отчёт", "priority": "fire", "date": "2026-11-01", "time": "12:00",
        "tags": ["#work"], "checklist": ["черновик", "правки"],
    })
    assert created.status_code == 201
    body = created.json()
    assert body["important"] is True and body["tags"] == ["work"] and len(body["checklist"]) == 2

    client.post("/api/athena/tasks", json={"title": "Обычная"})

    important = client.get("/api/athena/tasks/important").json()
    assert [t["title"] for t in important["items"]] == ["Сдать отчёт"] and important["total"] == 1

    everything = client.get("/api/athena/tasks").json()
    assert everything["total"] == 2 and everything["items"][0]["title"] == "Сдать отчёт"  # по срочности

    assert client.get(f"/api/athena/tasks/{body['id']}").json()["title"] == "Сдать отчёт"
    assert client.get("/api/athena/tasks/missing").status_code == 404


def test_create_rejects_bad_input(client):
    assert client.post("/api/athena/tasks", json={"title": "a", "date": "31.10.2026"}).status_code == 422
    assert client.post("/api/athena/tasks", json={"title": "a", "priority": "urgent"}).status_code == 422
    assert client.post("/api/athena/tasks", json={"title": ""}).status_code == 422
    assert client.get("/api/athena/tasks?status=open").status_code == 422
