-- Несколько напоминаний на одну задачу.
-- Migration 008. Миграции запускаются при каждом старте, поэтому всё идемпотентно.

CREATE TABLE IF NOT EXISTS task_reminders (
    id          TEXT PRIMARY KEY,                                   -- UUID v4
    task_id     TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    remind_at   TEXT NOT NULL,                                      -- ISO datetime (со смещением часового пояса)
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_reminders_task ON task_reminders(task_id);

-- Переносим старые одиночные напоминания (tasks.remind_at), если у задачи ещё нет записей
INSERT INTO task_reminders (id, task_id, remind_at, created_at)
SELECT 'legacy-' || t.id, t.id, t.remind_at, t.updated_at
FROM tasks t
WHERE t.remind_at != ''
  AND NOT EXISTS (SELECT 1 FROM task_reminders r WHERE r.task_id = t.id);