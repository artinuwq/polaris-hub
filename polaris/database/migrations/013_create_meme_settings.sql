-- Migration 013: настройки Polaris Meme (меню с шестерёнкой на странице Meme).
-- Хранятся в БД, а не в .env: веб и бот видят изменение сразу, без перезапуска. Идемпотентна.

CREATE TABLE IF NOT EXISTS meme_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
