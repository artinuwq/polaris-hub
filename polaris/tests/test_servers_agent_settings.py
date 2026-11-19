"""Hub: настройки агента, команда «обновись», директива в ответе на heartbeat."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from polaris.infra import database, migrations
from polaris.modules.servers import api, repository as repo, service
from polaris.shared.auth import require_admin


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "data" / "polaris.db")
    monkeypatch.setattr(database, "_wal_ready", set())
    migrations.migrate()
    migrations.migrate()  # миграция 014 идемпотентна
    return tmp_path


def _server(agent_version="0.2.0", registered=True):
    row = repo.create_server("web-1")
    if registered:
        repo.update_server(row["id"], {"agent_id": f"agent-{row['id']}", "agent_version": agent_version, "status": "online"})
    return row["id"]


def test_new_server_has_no_managed_config(db):
    directive = service.record_heartbeat(_server(), agent_version="0.3.0")
    assert directive == {"config_version": 0, "update": False}  # настроек нет — агент остаётся на локальных


def test_saved_settings_reach_agent_until_it_confirms_the_version(db):
    sid = _server()
    saved = service.update_agent_settings(sid, {"services": ["nginx", "docker", "nginx"], "heartbeat_interval": 20, "metrics_interval": 45})
    assert saved.agent_config == {"services": ["nginx", "docker"], "heartbeat_interval": 20, "metrics_interval": 45}
    assert saved.agent_config_version == 1

    first = service.record_heartbeat(sid, config_version=0)
    assert first["config_version"] == 1 and first["config"]["services"] == ["nginx", "docker"]
    assert "config" not in service.record_heartbeat(sid, config_version=1)  # уже применено — повторно не шлём

    service.update_agent_settings(sid, {"services": [], "heartbeat_interval": 30, "metrics_interval": 60})
    again = service.record_heartbeat(sid, config_version=1)
    assert again["config_version"] == 2 and again["config"]["services"] == []


@pytest.mark.parametrize("payload", [
    {"services": ["bad name"]},
    {"services": ["-rf"]},
    {"services": ["a;b"]},
    {"services": [f"s{i}" for i in range(40)]},
    {"heartbeat_interval": 5},
    {"metrics_interval": 10},
    {"heartbeat_interval": 99999},
])
def test_invalid_settings_are_rejected_and_not_saved(db, payload):
    sid = _server()
    with pytest.raises(service.SettingsError):
        service.update_agent_settings(sid, {"heartbeat_interval": 30, "metrics_interval": 60, **payload})
    assert repo.get_agent_settings(sid)["config_version"] == 0


def test_update_request_lifecycle(db):
    sid = _server(agent_version="0.1.0")
    latest = service.agent_bundled_version()
    assert service.get_server(sid).update_available is True  # в клоне Hub лежит более новая версия

    server = service.request_agent_update(sid)
    assert server.update_pending is True
    assert service.record_heartbeat(sid, agent_version="0.1.0")["update"] is True  # команда доставляется с heartbeat

    # агент сообщил итог: флаг снят уже в ответе на тот же heartbeat
    directive = service.record_heartbeat(sid, agent_version=latest, update_result="updated", update_detail=f"0.1.0 → {latest}")
    assert directive["update"] is False
    server = service.get_server(sid)
    assert (server.update_pending, server.update_result, server.update_detail) == (False, "updated", f"0.1.0 → {latest}")
    assert server.agent_version == latest and server.update_available is False


def test_update_request_expires_if_agent_never_answers(db):
    sid = _server()
    service.request_agent_update(sid)
    with database.get_db() as conn:
        conn.execute("UPDATE server_agent_settings SET update_requested_at = '2020-01-01T00:00:00.000Z' WHERE server_id = ?", (sid,))
    assert service.record_heartbeat(sid)["update"] is False
    assert service.get_server(sid).update_pending is False


def test_update_needs_registered_agent_and_update_all_skips_current(db):
    with pytest.raises(service.SettingsError):
        service.request_agent_update(_server(registered=False))
    old, current, pending = _server("0.1.0"), _server(service.agent_bundled_version()), _server(registered=False)
    assert service.request_agent_update_all() == 1
    assert service.record_heartbeat(old)["update"] is True
    assert service.record_heartbeat(current)["update"] is False
    assert repo.get_agent_settings(pending)["update_requested_at"] is None


def test_unknown_server_and_cascade_delete(db):
    assert service.update_agent_settings("nope", {}) is None and service.request_agent_update("nope") is None
    sid = _server()
    service.update_agent_settings(sid, {"services": ["nginx"]})
    assert repo.delete_server(sid)
    with database.get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM server_agent_settings").fetchone()[0] == 0


def test_http_endpoints(db):
    sid = _server()
    app = FastAPI()
    app.include_router(api.admin_router)
    app.include_router(api.agent_router)
    app.dependency_overrides[require_admin] = lambda: None
    app.dependency_overrides[api.require_agent] = lambda: {"id": sid}
    client = TestClient(app)

    r = client.put(f"/api/v1/servers/{sid}/agent-settings", json={"services": ["nginx"], "heartbeat_interval": 20, "metrics_interval": 45})
    assert r.status_code == 200 and r.json()["data"]["agent_config"]["services"] == ["nginx"]
    assert client.put(f"/api/v1/servers/{sid}/agent-settings", json={"services": ["bad name"]}).status_code == 400
    assert client.put(f"/api/v1/servers/{sid}/agent-settings", json={"heartbeat_interval": 1}).status_code == 422
    assert client.put("/api/v1/servers/nope/agent-settings", json={}).status_code == 404

    assert client.post(f"/api/v1/servers/{sid}/agent-update").json()["data"]["update_pending"] is True
    assert client.post("/api/v1/servers/nope/agent-update").status_code == 404
    assert client.post("/api/v1/servers/agent-update-all").json()["data"]["requested"] == 1

    hb = client.post("/api/v1/agent/heartbeat", json={"agent_version": "0.2.0", "config_version": 0}).json()
    assert hb["success"] is True
    assert hb["data"]["update"] is True and hb["data"]["config"]["services"] == ["nginx"]
    # старый агент, не знающий новых полей, продолжает работать
    assert client.post("/api/v1/agent/heartbeat", json={"agent_version": "0.2.0"}).status_code == 200
