from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

DB_PATH = Path(__file__).resolve().parents[3] / "database" / "data" / "polaris.db"


def get_database_path() -> Path:
    return DB_PATH


def get_connection() -> sqlite3.Connection:
    """Get a new SQLite connection with WAL mode and foreign keys enabled."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def get_db():
    """Context manager for database connections."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def run_migration(sql: str) -> None:
    """Execute a migration SQL file."""
    with get_db() as conn:
        conn.executescript(sql)


# ─── SQLAlchemy (используется модулем athena) ───
# Тот же файл SQLite, что и у sqlite3-хелперов выше.
_engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)


@event.listens_for(_engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record) -> None:
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


_SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """Сессия SQLAlchemy: `Depends(get_session)` в FastAPI или `next(get_session())`."""
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()
