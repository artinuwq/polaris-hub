-- Attention Engine: user actions (dismiss, snooze, pin) per item.
-- Migration 005.

CREATE TABLE IF NOT EXISTS attention_actions (
    id            TEXT PRIMARY KEY,             -- UUID v4
    user_id       TEXT NOT NULL,                -- Telegram user id (string)
    item_id       TEXT NOT NULL,                -- attention item id (e.g. "cal-123", "fin-456")
    action        TEXT NOT NULL,                -- dismiss|snooze|pin
    snoozed_until TEXT,                         -- ISO datetime for snooze, nullable
    created_at    TEXT NOT NULL,                -- ISO datetime
    UNIQUE(user_id, item_id, action)
);

CREATE INDEX IF NOT EXISTS idx_attention_actions_user_item ON attention_actions(user_id, item_id);