from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parents[3] / "database" / "data" / "polaris.db"

# Web и Bot — разные процессы с одной БД, а SQLite допускает только одного писателя.
# Остальные ждут блокировку не дольше BUSY_TIMEOUT_MS, после чего получают
# sqlite3.OperationalError("database is locked"): повторов «под капотом» нет,
# ошибка доходит до вызывающего кода. Значение общее для sqlite3 и SQLAlchemy.
BUSY_TIMEOUT_MS = 10_000

# journal_mode=WAL хранится в самом файле БД, поэтому включаем его один раз на
# процесс и файл, а не при каждом открытии соединения.
_wal_ready: set[str] = set()
_wal_lock = threading.Lock()


def get_database_path() -> Path:
    return DB_PATH


def _busy_timeout_seconds() -> float:
    return BUSY_TIMEOUT_MS / 1000


def _configure_connection(conn: sqlite3.Connection) -> None:
    """Настройки, которые SQLite держит на уровне соединения (в файле БД они не сохраняются)."""
    conn.execute(f"PRAGMA busy_timeout={int(BUSY_TIMEOUT_MS)}")
    conn.execute("PRAGMA foreign_keys=ON")


def _enable_wal(path: Path) -> None:
    conn = sqlite3.connect(str(path), timeout=_busy_timeout_seconds())
    try:
        # Если WAL уже включён (обычный случай после первого запуска), ничего не меняем:
        # переключение режима требует блокировки, а читать текущий режим можно без неё.
        if str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal":
            return
        mode = str(conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower()
        if mode != "wal":
            logger.warning("SQLite %s: не удалось включить WAL, режим журнала: %s", path, mode)
    finally:
        conn.close()


def _ensure_wal(path: Path) -> None:
    key = str(path)
    if key in _wal_ready:
        return
    with _wal_lock:
        if key in _wal_ready:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        _enable_wal(path)
        # Помечаем только после успеха: при ошибке (например, database is locked)
        # следующее открытие попробует снова, но не в цикле.
        _wal_ready.add(key)


def init_database() -> None:
    """Однократная подготовка основной БД: каталог и режим WAL. Идемпотентна."""
    _ensure_wal(DB_PATH)


def get_connection() -> sqlite3.Connection:
    """Новое соединение SQLite: busy_timeout и foreign_keys включены, WAL настроен в init_database()."""
    init_database()
    conn = sqlite3.connect(str(DB_PATH), timeout=_busy_timeout_seconds())
    try:
        _configure_connection(conn)
        conn.row_factory = sqlite3.Row
    except BaseException:
        conn.close()
        raise
    return conn


def _rollback_quietly(conn: sqlite3.Connection) -> None:
    # Ошибка отката не должна подменять исходное исключение.
    try:
        conn.rollback()
    except sqlite3.Error:
        logger.warning("SQLite: rollback не удался", exc_info=True)


@contextmanager
def get_db():
    """Context manager for database connections.

    Успех -> commit, любое исключение (включая отмену задачи) -> rollback и повторный
    подъём исключения, в любом случае -> закрытие соединения.
    """
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except BaseException:
        _rollback_quietly(conn)
        raise
    finally:
        conn.close()


def run_migration(sql: str) -> None:
    """Execute a migration SQL file."""
    with get_db() as conn:
        conn.executescript(sql)


# ─── SQLAlchemy (используется модулем athena) ───
# Тот же файл SQLite, что и у sqlite3-хелперов выше.
def _make_engine(path: Path) -> Engine:
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": _busy_timeout_seconds()},
    )

    @event.listens_for(engine, "do_connect")
    def _prepare_database(_dialect, _record, _cargs, _cparams) -> None:
        # До открытия соединения: каталог и WAL (возвращаем None — подключение идёт штатно).
        _ensure_wal(path)

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record) -> None:
        _configure_connection(dbapi_conn)

    return engine


_engine = _make_engine(DB_PATH)

_SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """Сессия SQLAlchemy: `Depends(get_session)` в FastAPI или `next(get_session())`.

    commit остаётся за вызывающим кодом; close() откатывает незавершённую транзакцию
    и возвращает соединение в пул.
    """
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()
