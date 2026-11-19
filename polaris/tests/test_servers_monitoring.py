"""Тесты пакетной записи и обслуживания истории мониторинга серверов."""
from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from polaris.modules.servers import repository as repo

MIGRATION = Path(__file__).resolve().parents[1] / "database" / "migrations" / "004_create_servers.sql"
# метрики теперь сверяются с настройками агента и сохраняют каталог сервисов
EXTRA_MIGRATIONS = [
    Path(__file__).resolve().parents[1] / "database" / "migrations" / name
    for name in ("014_create_server_agent_settings.sql", "015_create_server_service_catalog.sql")
]
OPTIMIZATION_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "database"
    / "migrations"
    / "011_optimize_server_monitoring.sql"
)


@contextlib.contextmanager
def server_db(tmp_path, monkeypatch):
    from polaris.infra import database

    db_file = tmp_path / "servers.db"
    monkeypatch.setattr(database, "DB_PATH", db_file)
    conn = sqlite3.connect(db_file)
    conn.executescript(MIGRATION.read_text(encoding="utf-8"))
    conn.executescript(OPTIMIZATION_MIGRATION.read_text(encoding="utf-8"))
    for extra in EXTRA_MIGRATIONS:
        conn.executescript(extra.read_text(encoding="utf-8"))
    conn.close()
    yield db_file


def _counted_get_db(monkeypatch):
    original = repo.get_db
    calls = {"count": 0}

    @contextlib.contextmanager
    def counted():
        calls["count"] += 1
        with original() as conn:
            yield conn

    monkeypatch.setattr(repo, "get_db", counted)
    return calls


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _metric(number: int = 1) -> dict:
    return {
        "cpu_usage": float(number),
        "cpu_load1": 0.1,
        "cpu_load5": 0.2,
        "cpu_load15": 0.3,
        "mem_total": 1024,
        "mem_used": 512,
        "mem_available": 512,
        "mem_percent": 50.0,
        "disk": [],
        "net_rx_bytes": number,
        "net_tx_bytes": number,
        "uptime_seconds": 1000 + number,
    }


def test_heartbeat_uses_one_connection_and_records_gap_once(tmp_path, monkeypatch):
    with server_db(tmp_path, monkeypatch):
        server = repo.create_server("test")
        old_seen = _iso(datetime.now(timezone.utc) - timedelta(minutes=5))
        repo.update_server(server["id"], {"status": "online", "last_seen": old_seen})

        calls = _counted_get_db(monkeypatch)
        repo.record_agent_heartbeat(
            server["id"],
            {"status": "online", "status_reason": None, "last_seen": repo.now_iso(), "agent_version": "1.2"},
            offline_threshold_seconds=45,
        )
        assert calls["count"] == 1

        events = repo.get_events_for_server(server["id"], 10)
        assert len(events) == 1
        assert events[0]["type"] == "agent_offline"
        assert events[0]["created_at"] == old_seen
        assert events[0]["resolved_at"] is not None
        assert json.loads(events[0]["payload"])["retro"] is True

        # Последующий heartbeat не должен создавать повторный ретро-инцидент.
        repo.record_agent_heartbeat(
            server["id"],
            {"status": "online", "status_reason": None, "last_seen": repo.now_iso()},
            offline_threshold_seconds=45,
        )
        assert len(repo.get_events_for_server(server["id"], 10)) == 1


def test_metrics_sample_is_batched_and_unchanged_service_status_is_not_rewritten(
    tmp_path, monkeypatch
):
    with server_db(tmp_path, monkeypatch):
        server = repo.create_server("test")
        repo.update_server(server["id"], {"status": "online", "last_seen": repo.now_iso()})

        # Разносим timestamps так, чтобы повторный вызов точно имел другое время.
        values = iter([
            _iso(datetime.now(timezone.utc) - timedelta(seconds=2)),
            _iso(datetime.now(timezone.utc) - timedelta(seconds=1)),
            _iso(datetime.now(timezone.utc)),
        ])
        monkeypatch.setattr(repo, "now_iso", lambda: next(values))
        calls = _counted_get_db(monkeypatch)

        args = (
            server["id"],
            _metric(),
            {"status": "online", "status_reason": None, "last_seen": _iso(datetime.now(timezone.utc))},
            [("/", 96.0)],
            [("nginx", "failed")],
        )
        options = {
            "offline_threshold_seconds": 45,
            "disk_warning_threshold": 90.0,
            "disk_critical_threshold": 95.0,
            "disk_event_dedup_seconds": 1800,
            "service_event_dedup_seconds": 600,
        }

        before = calls["count"]
        repo.ingest_metrics_sample(*args, **options)
        assert calls["count"] - before == 1
        first_status = repo.get_services_for_server(server["id"])[0]
        first_updated_at = first_status["updated_at"]
        assert first_status["status"] == "failed"

        before = calls["count"]
        repo.ingest_metrics_sample(*args, **options)
        assert calls["count"] - before == 1
        second_status = repo.get_services_for_server(server["id"])[0]
        assert second_status["updated_at"] == first_updated_at

        # Дедупликация не должна плодить одинаковые события в пределах окна.
        events = repo.get_events_for_server(server["id"], 20)
        assert len(events) == 2
        assert {event["type"] for event in events} == {"disk_threshold", "service_down"}

        # Восстановление диска и сервиса закрывает соответствующие события.
        before = calls["count"]
        repo.ingest_metrics_sample(
            server["id"],
            _metric(2),
            {"status": "online", "status_reason": None, "last_seen": _iso(datetime.now(timezone.utc))},
            [("/", 70.0)],
            [("nginx", "running")],
            **options,
        )
        assert calls["count"] - before == 1
        events = repo.get_events_for_server(server["id"], 20)
        assert all(event["resolved_at"] is not None for event in events)

        from polaris.infra import database
        with sqlite3.connect(database.DB_PATH) as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM server_metrics WHERE server_id = ?", (server["id"],)
            ).fetchone()[0] == 3


def test_metrics_history_prunes_in_batches_not_on_every_insert(tmp_path, monkeypatch):
    with server_db(tmp_path, monkeypatch):
        from polaris.infra import database
        server = repo.create_server("test")
        monkeypatch.setattr(repo, "METRICS_HISTORY_LIMIT", 3)
        monkeypatch.setattr(repo, "METRICS_PRUNE_BATCH", 2)

        for index in range(5):
            repo.insert_metric(server["id"], _metric(index))

        from polaris.infra import database
        with sqlite3.connect(database.DB_PATH) as conn:
            count_before_threshold = conn.execute(
                "SELECT COUNT(*) FROM server_metrics WHERE server_id = ?", (server["id"],)
            ).fetchone()[0]
        assert count_before_threshold == 5

        repo.insert_metric(server["id"], _metric(6))
        from polaris.infra import database
        with sqlite3.connect(database.DB_PATH) as conn:
            count_after_prune = conn.execute(
                "SELECT COUNT(*) FROM server_metrics WHERE server_id = ?", (server["id"],)
            ).fetchone()[0]
        assert count_after_prune == 3


def test_event_retention_deletes_only_old_resolved_events(tmp_path, monkeypatch):
    with server_db(tmp_path, monkeypatch) as db_file:
        server = repo.create_server("test")
        monkeypatch.setenv("POLARIS_SERVER_EVENT_RETENTION_DAYS", "30")
        now = datetime.now(timezone.utc)
        old = _iso(now - timedelta(days=90))
        recent = _iso(now - timedelta(days=10))
        very_old = _iso(now - timedelta(days=200))

        with sqlite3.connect(db_file) as conn:
            conn.execute(
                """INSERT INTO server_events
                   (id, server_id, type, severity, payload, created_at, resolved_at)
                   VALUES ('old-closed', ?, 'test', 'info', '{}', ?, ?)""",
                (server["id"], old, old),
            )
            conn.execute(
                """INSERT INTO server_events
                   (id, server_id, type, severity, payload, created_at, resolved_at)
                   VALUES ('old-open', ?, 'test', 'critical', '{}', ?, NULL)""",
                (server["id"], very_old),
            )
            conn.execute(
                """INSERT INTO server_events
                   (id, server_id, type, severity, payload, created_at, resolved_at)
                   VALUES ('recent-closed', ?, 'test', 'info', '{}', ?, ?)""",
                (server["id"], recent, recent),
            )

        repo.create_event(server["id"], "new", "info", {})
        with sqlite3.connect(db_file) as conn:
            remaining = {
                row[0] for row in conn.execute("SELECT id FROM server_events")
            }
        assert "old-closed" not in remaining
        assert "old-open" in remaining
        assert "recent-closed" in remaining


def test_monitoring_indexes_are_created(tmp_path, monkeypatch):
    with server_db(tmp_path, monkeypatch) as db_file:
        with sqlite3.connect(db_file) as conn:
            names = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
                )
            }
        assert {
            "idx_server_events_open_lookup",
            "idx_server_events_recent",
            "idx_server_events_resolved_retention",
            "idx_server_events_type_status",
        } <= names


def test_service_metrics_endpoint_path_uses_batched_repository_method(tmp_path, monkeypatch):
    with server_db(tmp_path, monkeypatch):
        from polaris.modules.servers import service
        from polaris.modules.servers.models import AgentMetricsRequest

        server = repo.create_server("test")
        calls = _counted_get_db(monkeypatch)
        payload = AgentMetricsRequest.model_validate({
            "cpu": {"usage": 12.5},
            "memory": {"total": 2048, "used": 1024, "available": 1024, "percent": 50},
            "disk": [{"mount": "/", "total": 100, "used": 96, "available": 4, "percent": 96}],
            "network": {"rx_bytes": 10, "tx_bytes": 20},
            "system": {"hostname": "node-1", "os": "Linux", "uptime_seconds": 120},
            "services": [{"name": "nginx", "status": "failed"}],
        })

        service.ingest_metrics(server["id"], payload)
        assert calls["count"] == 1

        metrics = repo.get_metrics_history(server["id"], 10)
        assert len(metrics) == 1
        assert metrics[0]["cpu_usage"] == 12.5
        events = repo.get_events_for_server(server["id"], 10)
        assert {event["type"] for event in events} == {"disk_threshold", "service_down"}
