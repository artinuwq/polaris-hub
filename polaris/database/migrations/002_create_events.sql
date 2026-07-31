-- Events table for Polaris Hub
-- Migration 002: Create events table (произвольные события календаря,
-- добавляемые вручную — встречи, дни рождения и т.п.)

CREATE TABLE IF NOT EXISTS events (
    id              TEXT PRIMARY KEY,           -- UUID v4
    title           TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    date            TEXT NOT NULL DEFAULT '',   -- YYYY-MM-DD
    time            TEXT NOT NULL DEFAULT '',   -- HH:MM
    end_time        TEXT NOT NULL DEFAULT '',   -- HH:MM
    project         TEXT NOT NULL DEFAULT '',
    project_color   TEXT NOT NULL DEFAULT '',
    tags            TEXT NOT NULL DEFAULT '[]', -- JSON array of strings
    created_at      TEXT NOT NULL,               -- ISO datetime
    updated_at      TEXT NOT NULL                -- ISO datetime
);

CREATE INDEX IF NOT EXISTS idx_events_date ON events(date);
