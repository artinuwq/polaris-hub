"""Единая точка миграций основной БД: первый/повторный запуск, параллельный старт, MailDaemon без схемы."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from polaris.infra import database, migrations
from polaris.modules.mail import daemon, repository

SRC = Path(__file__).resolve().parents[1] / "src"


@pytest.fixture()
def db_file(tmp_path, monkeypatch):
    path = tmp_path / "data" / "polaris.db"  # каталога ещё нет: его создаёт init_database
    monkeypatch.setattr(database, "DB_PATH", path)
    monkeypatch.setattr(database, "_wal_ready", set())
    return path


def _tables() -> set[str]:
    with database.get_db() as conn:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _schema() -> list[tuple]:
    with database.get_db() as conn:
        rows = conn.execute("SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name")
        return [tuple(r) for r in rows]


def test_first_run_creates_every_table(db_file):
    names = migrations.migrate()
    assert names == [f.name for f in migrations.migration_files()]
    assert {"tasks", "task_reminders", "notification_log", "agent_token", "attention_actions",
            *repository.REQUIRED_TABLES} <= _tables()
    repository.require_schema()


def test_repeated_run_keeps_schema_and_data(db_file):
    migrations.migrate()
    schema = _schema()
    with database.get_db() as conn:
        conn.execute("INSERT INTO tasks (id, title, remind_at, created_at, updated_at) VALUES ('t1', 'x', '2026-01-01T10:00:00', 'n', 'n')")
    migrations.migrate()  # «перезапуск приложения»
    migrations.migrate()
    assert _schema() == schema
    with database.get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks WHERE id = 't1'").fetchone()[0] == 1
        # 008 переносит старое напоминание ровно один раз
        assert conn.execute("SELECT COUNT(*) FROM task_reminders WHERE task_id = 't1'").fetchone()[0] == 1
        # сид-данные 001 не дублируются
        assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 5


def test_existing_install_applied_file_by_file_is_compatible(db_file):
    """База, созданная старым способом (каждый файл отдельным executescript), с пользовательскими данными."""
    for sql_file in migrations.migration_files():
        database.run_migration(sql_file.read_text(encoding="utf-8"))
    with database.get_db() as conn:
        conn.execute("INSERT INTO mail_accounts (provider, email, password, created_at, updated_at) VALUES ('gmail', 'a@b.c', 'pw', 'n', 'n')")
    schema = _schema()
    migrations.migrate()
    assert _schema() == schema
    with database.get_db() as conn:
        assert conn.execute("SELECT email FROM mail_accounts").fetchone()[0] == "a@b.c"


def test_failed_migration_rolls_back_whole_set(db_file, tmp_path):
    bad = tmp_path / "migs"
    bad.mkdir()
    (bad / "001_ok.sql").write_text("CREATE TABLE ok_table (id INTEGER);", encoding="utf-8")
    (bad / "002_bad.sql").write_text("CREATE TABLE broken (id INTEGER);\nINSERT INTO no_such_table VALUES (1);", encoding="utf-8")
    with pytest.raises(Exception):
        migrations.migrate(bad)
    assert not ({"ok_table", "broken"} & _tables())
    (bad / "002_bad.sql").write_text("CREATE TABLE fixed (id INTEGER);", encoding="utf-8")
    migrations.migrate(bad)  # после исправления применяется без хвостов прошлой попытки
    assert {"ok_table", "fixed"} <= _tables()


def test_missing_migrations_dir_is_not_fatal(db_file, tmp_path):
    assert migrations.migrate(tmp_path / "nope") == []


def test_parallel_processes_on_fresh_db(db_file):
    """Web и Bot стартуют одновременно на пустой БД: никто не падает, схема полная."""
    code = (
        "import sys; from pathlib import Path\n"
        "from polaris.infra import database, migrations\n"
        "database.DB_PATH = Path(sys.argv[1])\n"
        "migrations.migrate()\n"
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(SRC), os.environ.get("PYTHONPATH", "")]))}
    procs = [subprocess.Popen([sys.executable, "-c", code, str(db_file)], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(6)]
    results = [(p.wait(timeout=60), p.stderr.read()) for p in procs]
    assert all(code_ == 0 for code_, _ in results), results
    assert set(repository.REQUIRED_TABLES) <= _tables()


def test_mail_daemon_refuses_to_start_without_schema_and_creates_nothing(db_file):
    d = daemon.MailDaemon("TOKEN")
    with pytest.raises(RuntimeError, match="mail_accounts"):
        d.start()
    assert d._threads == []
    assert not (set(repository.REQUIRED_TABLES) & _tables()), "MailDaemon не должен создавать таблицы сам"


def test_mail_daemon_starts_after_migrations(db_file, monkeypatch):
    monkeypatch.setattr(daemon, "RECONCILE_INTERVAL", 0.1)
    monkeypatch.setattr(daemon, "SENDER_INTERVAL", 0.1)
    migrations.migrate()
    d = daemon.MailDaemon("TOKEN")
    d.start()
    try:
        assert len(d._threads) == 2
    finally:
        d.stop()
