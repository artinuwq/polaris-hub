"""SQL для таблицы memes (основная БД Polaris, polaris.db). Ничего не знает о Storage и Telegram."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from polaris.infra.database import get_db

MAX_QUERY_TOKENS = 8
_WORD = re.compile(r"\w+", re.UNICODE)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def new_id() -> str:
    return str(uuid.uuid4())


def norm(text: str) -> str:
    """Нормализация для поиска: регистр (casefold, в т.ч. кириллица) и «ё» -> «е»."""
    return (text or "").casefold().replace("ё", "е")


def query_tokens(query: str) -> list[str]:
    return _WORD.findall(norm(query))[:MAX_QUERY_TOKENS]


def _like(token: str) -> str:
    escaped = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    item["tags"] = json.loads(item.get("tags") or "[]")
    return item


class DuplicateMeme(Exception):
    """Изображение с таким SHA-256 уже есть в библиотеке."""

    def __init__(self, existing: dict[str, Any]) -> None:
        super().__init__(existing["id"])
        self.existing = existing


def insert(data: dict[str, Any]) -> dict[str, Any]:
    now = now_iso()
    tags = data["tags"]
    try:
        with get_db() as conn:
            conn.execute(
                """INSERT INTO memes
                   (id, title, description, tags, title_norm, desc_norm, tags_norm, storage_bucket,
                    storage_object_id, filename, mime_type, size_bytes, width, height, sha256, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (data["id"], data["title"], data["description"], json.dumps(tags, ensure_ascii=False),
                 norm(data["title"]), norm(data["description"]), norm(" | ".join(tags)),
                 data["storage_bucket"], data["storage_object_id"], data["filename"], data["mime_type"],
                 data["size_bytes"], data["width"], data["height"], data["sha256"], now, now),
            )
            return _row(conn.execute("SELECT * FROM memes WHERE id = ?", (data["id"],)).fetchone())  # type: ignore[return-value]
    except sqlite3.IntegrityError:
        existing = get_by_sha(data["sha256"])
        if existing is None:
            raise
        raise DuplicateMeme(existing) from None


def get(meme_id: str) -> dict[str, Any] | None:
    with get_db() as conn:
        return _row(conn.execute("SELECT * FROM memes WHERE id = ?", (meme_id,)).fetchone())


def get_by_sha(sha256: str) -> dict[str, Any] | None:
    with get_db() as conn:
        return _row(conn.execute("SELECT * FROM memes WHERE sha256 = ?", (sha256,)).fetchone())


def update_meta(meme_id: str, title: str, description: str, tags: list[str]) -> dict[str, Any] | None:
    with get_db() as conn:
        cur = conn.execute(
            """UPDATE memes SET title = ?, description = ?, tags = ?, title_norm = ?, desc_norm = ?, tags_norm = ?,
               updated_at = ? WHERE id = ?""",
            (title, description, json.dumps(tags, ensure_ascii=False), norm(title), norm(description),
             norm(" | ".join(tags)), now_iso(), meme_id),
        )
        if cur.rowcount == 0:
            return None
        return _row(conn.execute("SELECT * FROM memes WHERE id = ?", (meme_id,)).fetchone())


def delete(meme_id: str) -> bool:
    with get_db() as conn:
        return conn.execute("DELETE FROM memes WHERE id = ?", (meme_id,)).rowcount > 0


def set_telegram_cache(meme_id: str, file_id: str, kind: str, cache_key: str) -> None:
    # updated_at не трогаем: это служебный кэш, а не правка записи пользователем
    with get_db() as conn:
        conn.execute("UPDATE memes SET tg_file_id = ?, tg_file_kind = ?, tg_cache_key = ? WHERE id = ?",
                     (file_id, kind, cache_key, meme_id))


def clear_telegram_cache(meme_id: str) -> None:
    with get_db() as conn:
        conn.execute("UPDATE memes SET tg_file_id = NULL, tg_file_kind = NULL, tg_cache_key = NULL WHERE id = ?",
                     (meme_id,))


def _where(query: str, tag: str) -> tuple[str, list[Any], str, list[Any]]:
    """WHERE (все слова запроса должны найтись в названии, тегах или описании) и выражение релевантности."""
    clauses: list[str] = []
    params: list[Any] = []
    score_parts: list[str] = []
    score_params: list[Any] = []
    for token in query_tokens(query):
        like = _like(token)
        clauses.append("(title_norm LIKE ? ESCAPE '\\' OR tags_norm LIKE ? ESCAPE '\\' OR desc_norm LIKE ? ESCAPE '\\')")
        params += [like, like, like]
        score_parts.append("(CASE WHEN title_norm LIKE ? ESCAPE '\\' THEN 3 ELSE 0 END)"
                           " + (CASE WHEN tags_norm LIKE ? ESCAPE '\\' THEN 2 ELSE 0 END)"
                           " + (CASE WHEN desc_norm LIKE ? ESCAPE '\\' THEN 1 ELSE 0 END)")
        score_params += [like, like, like]
    tag_norm = norm(tag).strip()
    if tag_norm:
        # тег совпадает целиком: теги хранятся как «a | b | c»
        clauses.append("(' | ' || tags_norm || ' | ') LIKE ? ESCAPE '\\'")
        params.append(f"% | {_like(tag_norm)[1:-1]} | %")
    where = " AND ".join(clauses) if clauses else "1 = 1"
    score = " + ".join(score_parts) if score_parts else "(0 + 0)"  # не голый «0»: SQLite счёл бы его номером столбца
    return where, params, score, score_params


def search(query: str = "", tag: str = "", limit: int = 20, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
    """Страница результатов и общее число совпадений. Без запроса — все записи, новые сверху."""
    where, params, score, score_params = _where(query, tag)
    with get_db() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM memes WHERE {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM memes WHERE {where} ORDER BY ({score}) DESC, updated_at DESC, id LIMIT ? OFFSET ?",
            [*params, *score_params, max(1, limit), max(0, offset)],
        ).fetchall()
    return [_row(r) for r in rows], int(total)  # type: ignore[misc]


def top_tags(limit: int = 30) -> list[dict[str, Any]]:
    """Самые частые теги (для быстрых фильтров). Библиотека личная, поэтому считаем в Python."""
    counts: dict[str, list] = {}
    with get_db() as conn:
        for row in conn.execute("SELECT tags FROM memes"):
            for tag in json.loads(row[0] or "[]"):
                entry = counts.setdefault(norm(tag), [tag, 0])
                entry[1] += 1
    ordered = sorted(counts.values(), key=lambda e: (-e[1], norm(e[0])))
    return [{"tag": tag, "count": count} for tag, count in ordered[:limit]]


def count() -> int:
    with get_db() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM memes").fetchone()[0])


# ─── настройки (таблица meme_settings) ───

def get_setting(key: str) -> str:
    """Значение настройки или ''. Ошибка БД (например, схема ещё не применена) не должна ронять вызывающего."""
    try:
        with get_db() as conn:
            row = conn.execute("SELECT value FROM meme_settings WHERE key = ?", (key,)).fetchone()
    except sqlite3.Error:
        return ""
    return row[0] if row else ""


def set_setting(key: str, value: str) -> None:
    """Пустое значение удаляет настройку (возврат к значению по умолчанию)."""
    with get_db() as conn:
        if value:
            conn.execute("INSERT INTO meme_settings (key, value, updated_at) VALUES (?, ?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                         (key, value, now_iso()))
        else:
            conn.execute("DELETE FROM meme_settings WHERE key = ?", (key,))


def uncached(cache_key: str, limit: int, skip: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """Записи без действующего file_id (нет совсем или получен другим ботом), старые первыми; skip — уже пробованные."""
    marks = ",".join("?" for _ in skip)
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM memes WHERE (tg_file_id IS NULL OR tg_cache_key IS NOT ?)"
            + (f" AND id NOT IN ({marks})" if skip else "") + " ORDER BY created_at, id LIMIT ?",
            (cache_key, *skip, max(1, limit))).fetchall()
    return [_row(r) for r in rows]  # type: ignore[misc]


def count_uncached(cache_key: str, skip: tuple[str, ...] = ()) -> int:
    marks = ",".join("?" for _ in skip)
    with get_db() as conn:
        return int(conn.execute(
            "SELECT COUNT(*) FROM memes WHERE (tg_file_id IS NULL OR tg_cache_key IS NOT ?)"
            + (f" AND id NOT IN ({marks})" if skip else ""), (cache_key, *skip)).fetchone()[0])
