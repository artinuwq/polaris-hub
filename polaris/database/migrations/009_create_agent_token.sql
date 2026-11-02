-- Migration 009: статичный токен для подключения внешнего агента (Athena).
-- Хранится только хеш токена; сам токен показывается один раз при генерации.
-- Миграции запускаются при каждом старте, поэтому всё идемпотентно.

CREATE TABLE IF NOT EXISTS agent_token (
    id          INTEGER PRIMARY KEY CHECK (id = 1),   -- токен всегда один
    token_hash  TEXT NOT NULL,                        -- sha256(token), hex
    token_hint  TEXT NOT NULL,                        -- последние 4 символа, для отображения
    created_at  TEXT NOT NULL
);
