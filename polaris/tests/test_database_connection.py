"""Настройка соединений основной БД: WAL один раз, busy_timeout, транзакции, закрытие."""
from __future__ import annotations

import sqlite3
import time

import pytest

from polaris.infra import database


@pytest.fixture()
def db_file(tmp_path, monkeypatch):
    path = tmp_path / "sub" / "polaris.db"  # каталога ещё нет: его создаёт init_database
    monkeypatch.setattr(database, "DB_PATH", path)
    monkeypatch.setattr(database, "_wal_ready", set())
    return path


def _make_table() -> None:
    with database.get_db() as conn:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")


def test_connection_pragmas(db_file):
    conn = database.get_connection()
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == database.BUSY_TIMEOUT_MS
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.row_factory is sqlite3.Row
    finally:
        conn.close()


def test_wal_is_enabled_once_per_process(db_file, monkeypatch):
    calls = []
    real = database._enable_wal
    monkeypatch.setattr(database, "_enable_wal", lambda path: (calls.append(path), real(path))[1])

    for _ in range(3):
        database.get_connection().close()
    database.init_database()

    assert calls == [db_file]


def test_wal_failure_is_not_cached(db_file, monkeypatch):
    def boom(path):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(database, "_enable_wal", boom)
    with pytest.raises(sqlite3.OperationalError):
        database.get_connection()
    assert str(db_file) not in database._wal_ready


def test_commit_on_success(db_file):
    _make_table()
    with database.get_db() as conn:
        conn.execute("INSERT INTO t (v) VALUES ('a')")
    with database.get_db() as conn:
        assert conn.execute("SELECT v FROM t").fetchall()[0]["v"] == "a"


def test_rollback_on_error_and_original_exception_propagates(db_file):
    _make_table()
    with pytest.raises(ValueError):
        with database.get_db() as conn:
            conn.execute("INSERT INTO t (v) VALUES ('lost')")
            raise ValueError("boom")
    with database.get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0


def test_rollback_on_base_exception(db_file):
    _make_table()
    with pytest.raises(KeyboardInterrupt):
        with database.get_db() as conn:
            conn.execute("INSERT INTO t (v) VALUES ('lost')")
            raise KeyboardInterrupt
    with database.get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0


def test_connection_closed_after_success_and_error(db_file):
    _make_table()
    with database.get_db() as ok_conn:
        pass
    with pytest.raises(RuntimeError):
        with database.get_db() as err_conn:
            raise RuntimeError
    for conn in (ok_conn, err_conn):
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")


def test_failed_pragma_setup_closes_connection(db_file, monkeypatch):
    opened = []
    real_connect = sqlite3.connect

    def tracking_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    def bad_configure(conn):
        raise sqlite3.OperationalError("pragma failed")

    database.init_database()
    monkeypatch.setattr(database.sqlite3, "connect", tracking_connect)
    monkeypatch.setattr(database, "_configure_connection", bad_configure)
    with pytest.raises(sqlite3.OperationalError):
        database.get_connection()
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")


def test_busy_timeout_waits_then_raises_locked(db_file, monkeypatch):
    _make_table()
    monkeypatch.setattr(database, "BUSY_TIMEOUT_MS", 300)

    blocker = database.get_connection()
    try:
        blocker.execute("BEGIN IMMEDIATE")  # второй процесс (Web/Bot) держит блокировку записи
        started = time.monotonic()
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            with database.get_db() as conn:
                conn.execute("INSERT INTO t (v) VALUES ('x')")
        elapsed = time.monotonic() - started
    finally:
        blocker.rollback()
        blocker.close()

    assert 0.2 <= elapsed < 3  # ждали busy_timeout, но не бесконечно


def test_sqlalchemy_engine_pragmas(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "_wal_ready", set())
    path = tmp_path / "sa" / "polaris.db"
    engine = database._make_engine(path)
    try:
        with engine.connect() as conn:
            assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
            assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == database.BUSY_TIMEOUT_MS
            assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    finally:
        engine.dispose()
