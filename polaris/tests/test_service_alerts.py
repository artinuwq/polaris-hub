"""Отслеживание сервисов: каталог от агента, выбор в Hub и уведомления о падении / восстановлении."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from polaris.bot.notify import build_attention_message
from polaris.infra import database, migrations
from polaris.modules.attention import repository as attention_repo
from polaris.modules.servers import alerts, api, repository as repo, service
from polaris.modules.servers.models import AgentMetricsRequest

ADMIN = 111


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "data" / "polaris.db")
    monkeypatch.setattr(database, "_wal_ready", set())
    migrations.migrate()
    return tmp_path


def _server(name="web-1"):
    row = repo.create_server(name)
    repo.update_server(row["id"], {"agent_id": f"agent-{row['id']}", "agent_version": "0.4.0", "status": "online"})
    return row["id"]


def _metrics(services=None, available=None):
    body = {"cpu": {"usage": 1}, "services": services or []}
    if available is not None:
        body["available_services"] = available
    return AgentMetricsRequest.model_validate(body)


def _report(sid, **statuses):
    service.ingest_metrics(sid, _metrics([{"name": n, "status": s} for n, s in statuses.items()]))


def _event(sid, service_name):
    return next(e for e in repo.get_open_events("service_down") if service_name in e["payload"])


# ───────────────────────── каталог установленных сервисов ─────────────────────────

CATALOG = [{"name": "xray", "state": "enabled", "running": True},
           {"name": "telemt", "state": "disabled", "running": False},
           {"name": "bad name", "state": "enabled", "running": False}]


def test_catalog_is_stored_and_shown_only_in_server_detail(db):
    sid = _server()
    service.ingest_metrics(sid, _metrics(available=CATALOG))
    detail = service.get_server(sid)
    assert [c["name"] for c in detail.available_services] == ["xray", "telemt"]  # имя с пробелом отфильтровано
    assert detail.available_services_at
    assert service.list_servers()[0].available_services == []                    # список серверов не раздуваем
    service.ingest_metrics(sid, _metrics())                                      # замер без списка его не стирает
    assert len(service.get_server(sid).available_services) == 2


def test_hub_managed_services_filter_foreign_reports(db):
    sid = _server()
    service.update_agent_settings(sid, {"services": ["xray"], "heartbeat_interval": 30, "metrics_interval": 60})
    _report(sid, xray="running", telemt="failed")  # telemt не отслеживается (агент ещё не применил настройки)
    assert [s["name"] for s in repo.get_services_for_server(sid)] == ["xray"]
    assert repo.get_open_events("service_down") == []


def test_unmanaged_server_keeps_reporting_whatever_the_agent_sends(db):
    sid = _server()
    _report(sid, xray="running", telemt="failed")
    assert {s["name"] for s in repo.get_services_for_server(sid)} == {"xray", "telemt"}


def test_untracking_a_service_forgets_it_and_closes_its_event(db):
    sid = _server()
    _report(sid, xray="failed", fail2ban="running")
    assert len(repo.get_open_events("service_down")) == 1
    detail = service.update_agent_settings(sid, {"services": ["fail2ban"], "heartbeat_interval": 30, "metrics_interval": 60})
    assert [s.name for s in detail.services] == ["fail2ban"]
    assert repo.get_open_events("service_down") == []  # снятый с отслеживания сервис больше не тревожит
    assert detail.available_services == [] and detail.agent_config["services"] == ["fail2ban"]


# ───────────────────────── уведомления ─────────────────────────

def test_down_is_sent_once_per_event_and_admin(db):
    sid = _server()
    _report(sid, xray="failed")
    first = alerts.plan([ADMIN, 222])
    assert [(d.alert.kind, d.alert.service, d.admin_id) for d in first] == [("down", "xray", ADMIN), ("down", "xray", 222)]
    alerts.confirm(first[0])
    assert [d.admin_id for d in alerts.plan([ADMIN, 222])] == [222]  # неотправленное повторяется, отправленное — нет
    alerts.confirm(first[1])
    assert alerts.plan([ADMIN, 222]) == []


def test_two_services_on_one_server_are_notified_separately(db):
    sid = _server()
    _report(sid, xray="failed", telemt="stopped", fail2ban="running")
    sent = {d.alert.service: d for d in alerts.plan([ADMIN])}
    assert set(sent) == {"xray", "telemt"}
    assert sent["xray"].alert.severity == "critical" and sent["telemt"].alert.severity == "warning"


def test_recovery_follows_only_a_delivered_down_message(db):
    sid = _server()
    _report(sid, xray="failed")
    down = alerts.plan([ADMIN])[0]
    alerts.confirm(down)
    _report(sid, xray="running")
    up = alerts.plan([ADMIN])
    assert [(d.alert.kind, d.alert.service) for d in up] == [("up", "xray")]
    alerts.confirm(up[0])
    assert alerts.plan([ADMIN]) == []

    _report(sid, telemt="failed")      # упал и поднялся до того, как о нём успели сообщить: тишина
    _report(sid, telemt="running")
    assert alerts.plan([ADMIN]) == []


def test_flapping_service_is_muted_within_cooldown(db):
    sid = _server()
    _report(sid, xray="failed")
    alerts.confirm(alerts.plan([ADMIN])[0])
    _report(sid, xray="running")
    alerts.confirm(alerts.plan([ADMIN])[0])      # «снова работает»
    _report(sid, xray="failed")                  # упал снова спустя секунды
    assert alerts.plan([ADMIN]) == []            # дребезг: сообщения нет...
    _report(sid, xray="running")
    assert alerts.plan([ADMIN]) == []            # ...и о восстановлении тоже

    later = datetime.now(timezone.utc) + timedelta(seconds=alerts.COOLDOWN_SECONDS + 5)
    _report(sid, xray="failed")
    assert [d.alert.kind for d in alerts.plan([ADMIN], now=later)] == ["down"]  # через 10 минут — снова сообщаем


def test_deleted_server_and_legacy_events_without_service_are_ignored(db):
    sid = _server()
    repo.create_event(sid, "service_down", "warning", {"status": "stopped"})  # старое событие без имени сервиса
    assert alerts.plan([ADMIN]) == []
    _report(sid, xray="failed")
    repo.delete_server(sid)
    assert alerts.plan([ADMIN]) == []


def test_messages_name_the_server_and_the_service(db):
    sid = _server("web-1")
    _report(sid, xray="failed")
    down = alerts.plan([ADMIN])[0]
    text = build_attention_message(alerts.as_item(down.alert))
    assert "Сбой сервиса" in text and "web-1 · xray" in text and "Статус: failed" in text

    alerts.confirm(down)
    _report(sid, xray="running")
    up = alerts.plan([ADMIN])[0]
    text = build_attention_message(alerts.as_item(up.alert))
    assert "Сервис снова работает" in text and "web-1 · xray" in text and "Простой:" in text
    assert alerts.as_item(up.alert).url == f"/servers/{sid}"


@pytest.mark.parametrize("seconds,expected", [(5, "5 с"), (90, "1 мин"), (3600, "1 ч"), (3900, "1 ч 5 мин"), (90000, "1 д 1 ч")])
def test_duration_format(seconds, expected):
    assert alerts.format_duration(seconds) == expected


# ───────────────────────── лента внимания и HTTP ─────────────────────────

def test_attention_items_of_one_server_do_not_collide(db):
    sid = _server()
    _report(sid, xray="failed", telemt="stopped")
    items = attention_repo._servers_to_items("u")
    ids = {i.id for i in items}
    assert ids == {f"srv-{sid}-service_down-xray", f"srv-{sid}-service_down-telemt"}
    assert {i.title for i in items} == {"web-1: xray", "web-1: telemt"}


def test_metrics_endpoint_accepts_available_services(db):
    sid = _server()
    app = FastAPI()
    app.include_router(api.agent_router)
    app.dependency_overrides[api.require_agent] = lambda: {"id": sid}
    client = TestClient(app)
    body = {"cpu": {"usage": 1}, "available_services": CATALOG[:2]}
    assert client.post("/api/v1/agent/metrics", json=body).status_code == 200
    assert [c["name"] for c in service.get_server(sid).available_services] == ["xray", "telemt"]
    assert client.post("/api/v1/agent/metrics", json={"cpu": {"usage": 1}}).status_code == 200  # старый агент без поля
    too_long = {"cpu": {"usage": 1}, "available_services": [{"name": "x" * 101}]}
    assert client.post("/api/v1/agent/metrics", json=too_long).status_code == 422
