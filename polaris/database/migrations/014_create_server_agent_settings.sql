-- Migration 014: удалённые настройки и команда обновления для Polaris Agent.
-- Отдельная таблица, а не ALTER TABLE: миграции прогоняются при каждом старте и должны быть идемпотентны.
--
-- config_json         — настройки, которые Hub раздаёт агенту в ответе на heartbeat
-- config_version      — растёт при каждом сохранении; 0 = Hub настройками не управляет
-- update_requested_at — Hub просит агента обновиться (сбрасывается, когда агент сообщил результат)
-- update_result       — updated | current | failed | rollback (последний результат от агента)

CREATE TABLE IF NOT EXISTS server_agent_settings (
    server_id           TEXT PRIMARY KEY REFERENCES servers(id) ON DELETE CASCADE,
    config_json         TEXT NOT NULL DEFAULT '{}',
    config_version      INTEGER NOT NULL DEFAULT 0,
    update_requested_at TEXT,
    update_result       TEXT NOT NULL DEFAULT '',
    update_detail       TEXT NOT NULL DEFAULT '',
    update_result_at    TEXT,
    updated_at          TEXT NOT NULL
);
