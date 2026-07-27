-- Tasks table for Polaris Hub
-- Migration 001: Create tasks table

CREATE TABLE IF NOT EXISTS tasks (
    id              TEXT PRIMARY KEY,           -- UUID v4
    title           TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    date            TEXT NOT NULL DEFAULT '',   -- YYYY-MM-DD
    time            TEXT NOT NULL DEFAULT '',   -- HH:MM
    repeat          TEXT NOT NULL DEFAULT 'never',  -- never|daily|weekly|monthly|yearly|custom
    priority        TEXT NOT NULL DEFAULT 'normal', -- fire|important|normal|someday
    status          TEXT NOT NULL DEFAULT 'todo',   -- todo|in-progress|waiting|done
    energy          TEXT NOT NULL DEFAULT 'medium', -- quick|medium|large
    tags            TEXT NOT NULL DEFAULT '[]',     -- JSON array of strings
    project         TEXT NOT NULL DEFAULT '',
    project_color   TEXT NOT NULL DEFAULT '',       -- hex color for project
    checklist       TEXT NOT NULL DEFAULT '[]',     -- JSON array of { text, done }
    remind_at       TEXT NOT NULL DEFAULT '',       -- ISO datetime for reminder
    created_at      TEXT NOT NULL,                  -- ISO datetime
    updated_at      TEXT NOT NULL,                  -- ISO datetime
    done_at         TEXT NOT NULL DEFAULT ''         -- ISO datetime
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_date ON tasks(date);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project);

-- Projects table
CREATE TABLE IF NOT EXISTS projects (
    id              TEXT PRIMARY KEY,           -- UUID v4
    name            TEXT NOT NULL UNIQUE,
    color           TEXT NOT NULL DEFAULT '#5ab8ff',  -- hex color
    created_at      TEXT NOT NULL
);

-- Tags table
CREATE TABLE IF NOT EXISTS tags (
    id              TEXT PRIMARY KEY,           -- UUID v4
    name            TEXT NOT NULL UNIQUE,
    created_at      TEXT NOT NULL
);

-- Seed default projects
INSERT OR IGNORE INTO projects (id, name, color, created_at) VALUES
    ('00000000-0000-0000-0000-000000000001', 'Polaris', '#5ab8ff', datetime('now')),
    ('00000000-0000-0000-0000-000000000002', 'Lumica', '#52d273', datetime('now')),
    ('00000000-0000-0000-0000-000000000003', 'Личное', '#f5b942', datetime('now')),
    ('00000000-0000-0000-0000-000000000004', 'Дом', '#ff5d73', datetime('now')),
    ('00000000-0000-0000-0000-000000000005', 'Учеба', '#a78bfa', datetime('now'));

-- Seed default tags
INSERT OR IGNORE INTO tags (id, name, created_at) VALUES
    ('00000000-0000-0000-0000-000000000010', 'linux', datetime('now')),
    ('00000000-0000-0000-0000-000000000011', 'python', datetime('now')),
    ('00000000-0000-0000-0000-000000000012', 'telegram', datetime('now')),
    ('00000000-0000-0000-0000-000000000013', 'idea', datetime('now'));