-- Migration 012: Polaris Meme — личная библиотека изображений.
-- Сами файлы лежат в Polaris Storage (bucket polaris-meme); здесь только метаданные и ссылка на объект Storage.
-- Миграции запускаются при каждом старте — всё идемпотентно.

CREATE TABLE IF NOT EXISTS memes (
    id                TEXT PRIMARY KEY,
    title             TEXT NOT NULL,
    description       TEXT NOT NULL DEFAULT '',
    tags              TEXT NOT NULL DEFAULT '[]',        -- JSON-массив тегов как их ввёл пользователь
    title_norm        TEXT NOT NULL DEFAULT '',          -- нормализованные копии для поиска (casefold, ё -> е):
    desc_norm         TEXT NOT NULL DEFAULT '',          -- SQLite не умеет регистронезависимый поиск по кириллице
    tags_norm         TEXT NOT NULL DEFAULT '',
    storage_bucket    TEXT NOT NULL,
    storage_object_id TEXT NOT NULL,                     -- объект Storage (не чанк); наружу не отдаётся
    filename          TEXT NOT NULL DEFAULT '',
    mime_type         TEXT NOT NULL,
    size_bytes        INTEGER NOT NULL,
    width             INTEGER NOT NULL DEFAULT 0,
    height            INTEGER NOT NULL DEFAULT 0,
    sha256            TEXT NOT NULL,                     -- защита от дублей
    tg_file_id        TEXT,                              -- file_id, полученный ботом при первой отправке (кэш для inline)
    tg_file_kind      TEXT,                              -- photo | animation
    tg_cache_key      TEXT,                              -- к какому токену бота относится file_id
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_memes_sha256 ON memes(sha256);
CREATE INDEX IF NOT EXISTS idx_memes_updated_at ON memes(updated_at DESC);
