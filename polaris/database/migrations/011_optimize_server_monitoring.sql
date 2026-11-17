-- Оптимизация частых запросов мониторинга серверов.
-- Индекс ускоряет поиск и закрытие открытых событий конкретного типа,
-- а также проверку дедупликации по server_id/type/resolved_at/created_at.
CREATE INDEX IF NOT EXISTS idx_server_events_open_lookup
    ON server_events(server_id, type, resolved_at, created_at DESC);

-- Используется для глобальной ленты последних событий Attention Engine.
CREATE INDEX IF NOT EXISTS idx_server_events_recent
    ON server_events(created_at DESC);

-- Ускоряет периодическую очистку старых закрытых событий.
CREATE INDEX IF NOT EXISTS idx_server_events_resolved_retention
    ON server_events(resolved_at)
    WHERE resolved_at IS NOT NULL;

-- Нужен для расчёта uptime и выборки всех открытых service_down.
CREATE INDEX IF NOT EXISTS idx_server_events_type_status
    ON server_events(type, resolved_at, created_at DESC);
