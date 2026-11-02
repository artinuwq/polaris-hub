"""Статичный токен агента.

Токен генерируется из настроек (экран «Агент»), в базе лежит только его sha256.
Старый вариант — переменная окружения API_TOKEN — работает как запасной, пока
токен не сгенерирован в интерфейсе. Как только токен создан (или отозван) в интерфейсе, переменная
окружения перестаёт приниматься — иначе отозвать доступ было бы невозможно.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from typing import Any

from datetime import datetime, timezone

from polaris.infra.database import get_db

TOKEN_PREFIX = "plr_"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _stored() -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute("SELECT token_hash, token_hint, created_at FROM agent_token WHERE id = 1").fetchone()
        return dict(row) if row else None


def _env_token() -> str:
    return os.getenv("API_TOKEN", "").strip()


def status() -> dict[str, Any]:
    """Что показывать в настройках. Сам токен не возвращается никогда."""
    row = _stored()
    if row:
        # строка есть, но хеш пустой — токен отозван, переменная окружения тоже не действует
        if not row["token_hash"]:
            return {"configured": False, "source": "", "hint": "", "created_at": ""}
        return {"configured": True, "source": "db", "hint": row["token_hint"], "created_at": row["created_at"]}
    if _env_token():
        return {"configured": True, "source": "env", "hint": _env_token()[-4:], "created_at": ""}
    return {"configured": False, "source": "", "hint": "", "created_at": ""}


def generate() -> dict[str, str]:
    """Создаёт новый токен (старый перестаёт работать). Возвращает токен ОДИН раз."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    created = now_iso()
    with get_db() as conn:
        conn.execute(
            """INSERT INTO agent_token (id, token_hash, token_hint, created_at) VALUES (1, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET token_hash = excluded.token_hash,
                   token_hint = excluded.token_hint, created_at = excluded.created_at""",
            (_hash(token), token[-4:], created),
        )
    return {"token": token, "hint": token[-4:], "created_at": created}


def revoke() -> None:
    """Отзывает токен. Ставим пустой хеш (а не удаляем строку), чтобы после отзыва не
    «ожил» старый токен из переменной окружения API_TOKEN: токена нет, пока не создан новый."""
    with get_db() as conn:
        conn.execute(
            """INSERT INTO agent_token (id, token_hash, token_hint, created_at) VALUES (1, '', '', ?)
               ON CONFLICT(id) DO UPDATE SET token_hash = '', token_hint = '', created_at = excluded.created_at""",
            (now_iso(),),
        )


def verify(token: str) -> bool:
    if not token:
        return False
    row = _stored()
    if row:
        return bool(row["token_hash"]) and hmac.compare_digest(_hash(token), row["token_hash"])
    env = _env_token()
    return bool(env) and hmac.compare_digest(token.encode("utf-8"), env.encode("utf-8"))
