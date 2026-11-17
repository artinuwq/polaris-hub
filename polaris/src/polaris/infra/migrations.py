"""Единственное место, где применяются SQL-миграции основной БД (polaris.db).

Кто вызывает `migrate()`: Web (lifespan FastAPI) и Bot (старт `run_bot`). Оба процесса могут
стартовать одновременно (scripts/run_all.py) или по отдельности (run_web.py / run_bot.py,
polaris-agent.service), поэтому каждый из них сам гарантирует схему перед началом работы, а
остальные модули (например MailDaemon) схему НЕ меняют, а только проверяют.

Как это безопасно для двух процессов:
- все миграции выполняются одной транзакцией, которая начинается с `BEGIN IMMEDIATE`: писательскую
  блокировку SQLite берёт сразу и ждёт её до BUSY_TIMEOUT_MS, а не падает на середине скрипта из-за
  «database is locked» при апгрейде read -> write. Пока один процесс мигрирует, второй ждёт;
- файлы миграций идемпотентны (CREATE ... IF NOT EXISTS, INSERT OR IGNORE, INSERT ... WHERE NOT EXISTS)
  и по-прежнему прогоняются при каждом старте, как и раньше — отдельной таблицы версий нет,
  существующие установки не требуют никаких преобразований;
- при ошибке откатывается весь набор: база остаётся в прежнем состоянии, а не «наполовину».

Файлы миграций не должны сами управлять транзакциями (BEGIN/COMMIT/ROLLBACK).
"""

from __future__ import annotations

import logging
from pathlib import Path

from polaris.infra import database

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "database" / "migrations"


def migration_files(directory: Path | None = None) -> list[Path]:
    """SQL-файлы миграций в порядке применения (по имени: 001_..., 002_...)."""
    directory = MIGRATIONS_DIR if directory is None else directory
    return sorted(directory.glob("*.sql")) if directory.exists() else []


def migrate(directory: Path | None = None) -> list[str]:
    """Привести схему polaris.db к актуальной. Идемпотентна и безопасна при параллельном вызове.

    Возвращает имена выполненных файлов. Исключения (в том числе «database is locked», если
    блокировку не удалось получить за BUSY_TIMEOUT_MS) не глотаются: запуск без схемы небезопасен.
    """
    files = migration_files(directory)
    if not files:
        logger.warning("Миграции не найдены в %s — схема БД не изменялась", directory or MIGRATIONS_DIR)
        return []
    # `;` между файлами страхует от миграции без завершающей точки с запятой.
    body = "\n;\n".join(f.read_text(encoding="utf-8") for f in files)
    script = f"BEGIN IMMEDIATE;\n{body}\n;\nCOMMIT;\n"
    # executescript сначала делает COMMIT открытой транзакции, поэтому BEGIN IMMEDIATE идёт внутри
    # скрипта. При ошибке get_db() откатывает незавершённую транзакцию и пробрасывает исключение.
    with database.get_db() as conn:
        conn.executescript(script)
    names = [f.name for f in files]
    logger.info("Миграции БД применены: %d файл(ов), последний — %s", len(names), names[-1])
    return names
