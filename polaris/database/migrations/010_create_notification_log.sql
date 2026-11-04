-- Migration 010: журнал отправленных уведомлений, чтобы не слать одно и то же дважды
-- (в том числе после перезапуска бота). Миграции запускаются при каждом старте — всё идемпотентно.

CREATE TABLE IF NOT EXISTS notification_log (
    key      TEXT PRIMARY KEY,   -- например payment:<id>:<дата платежа>:<за сколько дней>:<admin>
    sent_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notification_log_sent_at ON notification_log(sent_at);
