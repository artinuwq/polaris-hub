"""SQLite-слой Storage: соединения по потокам, транзакции, версионные миграции."""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from polaris.storage.errors import MigrationError, StorageBusy, StorageUnavailable

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def load_migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    result = []
    for path in sorted(directory.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        version = int(path.name.split("_", 1)[0])
        # нормализуем переводы строк, чтобы checksum не зависел от git autocrlf
        sql = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        result.append(Migration(version, path.stem, sql))
    versions = [m.version for m in result]
    if versions != sorted(set(versions)):
        raise MigrationError(f"повторяющиеся версии миграций: {versions}")
    return result


def split_statements(sql: str) -> Iterator[str]:
    buf = ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            stmt = buf.strip()
            buf = ""
            if stmt:
                yield stmt
    tail = "\n".join(l for l in buf.splitlines() if l.strip() and not l.strip().startswith("--")).strip()
    if tail:
        yield buf.strip()


class StorageDatabase:
    """Одно соединение на поток (sqlite3-соединение нельзя безопасно делить между потоками).

    Все записи идут через `transaction()` -> BEGIN IMMEDIATE: блокировка записи берётся
    сразу, поэтому нет взаимных блокировок при апгрейде read -> write, а ожидание
    ограничено busy_timeout.
    """

    def __init__(self, path: Path, *, busy_timeout_ms: int = 10_000) -> None:
        self.path = Path(path)
        self.busy_timeout_ms = busy_timeout_ms
        self._local = threading.local()
        self._all: list[sqlite3.Connection] = []
        self._lock = threading.Lock()
        self._closed = False
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.path),
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,  # транзакциями управляем явно
            check_same_thread=False,  # используется только своим потоком; нужно для close()
        )
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={int(self.busy_timeout_ms)}")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def connection(self) -> sqlite3.Connection:
        if self._closed:
            raise StorageUnavailable("Storage database закрыта")
        conn = getattr(self._local, "conn", None)
        if conn is None:
            try:
                conn = self._open()
            except sqlite3.OperationalError as exc:
                raise _translate(exc) from exc
            self._local.conn = conn
            self._local.depth = 0
            with self._lock:
                self._all.append(conn)
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Атомарный блок записи. Вложенный вызов присоединяется к внешней транзакции."""
        conn = self.connection()
        if self._local.depth > 0:
            self._local.depth += 1
            try:
                yield conn
            finally:
                self._local.depth -= 1
            return
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            raise _translate(exc) from exc
        self._local.depth = 1
        try:
            yield conn
        except BaseException:
            self._local.depth = 0
            _rollback(conn)
            raise
        try:
            self._local.depth = 0
            conn.execute("COMMIT")
        except sqlite3.Error:
            _rollback(conn)
            raise

    def close(self) -> None:
        self._closed = True
        with self._lock:
            conns, self._all = self._all, []
        for conn in conns:
            try:
                conn.close()
            except sqlite3.Error:
                pass

    # ─── миграции ───

    def schema_version(self) -> int:
        row = self.connection().execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        return int(row[0] or 0)

    def migrate(self, migrations: list[Migration] | None = None) -> int:
        """Применить недостающие миграции. Безопасно при одновременном старте нескольких процессов."""
        migrations = load_migrations() if migrations is None else migrations
        conn = self.connection()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        known = {m.version for m in migrations}
        for m in migrations:
            with self.transaction() as c:  # внутри блокировки перепроверяем: другой процесс мог успеть
                row = c.execute("SELECT checksum FROM schema_migrations WHERE version=?", (m.version,)).fetchone()
                if row is not None:
                    if row["checksum"] != m.checksum:
                        raise MigrationError(f"миграция {m.name} изменена после применения")
                    continue
                for stmt in split_statements(m.sql):
                    c.execute(stmt)
                c.execute(
                    "INSERT INTO schema_migrations (version, name, checksum, applied_at) VALUES (?,?,?,?)",
                    (m.version, m.name, m.checksum, utcnow_iso()),
                )
                logger.info("Storage: применена миграция %s", m.name)
        newer = [r[0] for r in conn.execute("SELECT version FROM schema_migrations") if r[0] not in known]
        if newer:
            raise MigrationError(
                f"база создана более новой версией Polaris (схема {max(newer)}); обновите код, откат кода невозможен"
            )
        return self.schema_version()


def _rollback(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("ROLLBACK")
    except sqlite3.Error:
        pass


def _translate(exc: sqlite3.OperationalError) -> Exception:
    text = str(exc).lower()
    if "locked" in text or "busy" in text:
        return StorageBusy("SQLite занята: превышен busy_timeout")
    return exc
