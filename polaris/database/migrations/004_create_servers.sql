-- Servers module: мониторинг инфраструктуры через Polaris Agent.
-- Migration 004.
--
-- Agent НЕ подключается к Hub по SSH. Он сам устанавливает исходящее
-- HTTPS-соединение и шлёт heartbeat/metrics/events. Hub только хранит
-- и раздаёт факты; кто из них достоин внимания — решает Attention Engine.

CREATE TABLE IF NOT EXISTS servers (
    id                TEXT PRIMARY KEY,             -- UUID v4
    name              TEXT NOT NULL,
    hostname          TEXT NOT NULL DEFAULT '',
    address           TEXT NOT NULL DEFAULT '',      -- IP/адрес — просто метаданные
    status            TEXT NOT NULL DEFAULT 'pending', -- pending|online|offline|error
    status_reason     TEXT,                          -- напр. 'token_expired'
    agent_id          TEXT UNIQUE,                   -- NULL пока агент не зарегистрирован
    agent_token_hash  TEXT,                          -- sha256(agent_token), сам токен не хранится
    agent_version     TEXT NOT NULL DEFAULT '',
    os                TEXT NOT NULL DEFAULT '',
    kernel            TEXT NOT NULL DEFAULT '',
    architecture      TEXT NOT NULL DEFAULT '',
    uptime_seconds    INTEGER,
    last_seen         TEXT,                          -- ISO datetime последнего heartbeat/metrics
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS registration_tokens (
    id           TEXT PRIMARY KEY,
    server_id    TEXT NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    token_hash   TEXT NOT NULL,                      -- sha256(token) — сырой токен не хранится
    expires_at   TEXT NOT NULL,
    used_at      TEXT,
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_registration_tokens_server ON registration_tokens(server_id);
CREATE INDEX IF NOT EXISTS idx_registration_tokens_hash ON registration_tokens(token_hash);

CREATE TABLE IF NOT EXISTS server_metrics (
    id             TEXT PRIMARY KEY,
    server_id      TEXT NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    cpu_usage      REAL,
    cpu_load1      REAL,
    cpu_load5      REAL,
    cpu_load15     REAL,
    mem_total      INTEGER,
    mem_used       INTEGER,
    mem_available  INTEGER,
    mem_percent    REAL,
    disk_json      TEXT NOT NULL DEFAULT '[]',        -- [{mount,total,used,available,percent}, ...]
    net_rx_bytes   INTEGER,
    net_tx_bytes   INTEGER,
    uptime_seconds INTEGER,
    recorded_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_server_metrics_server_time ON server_metrics(server_id, recorded_at);

CREATE TABLE IF NOT EXISTS server_events (
    id           TEXT PRIMARY KEY,
    server_id    TEXT NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    type         TEXT NOT NULL,                      -- service_down|disk_threshold|agent_offline|...
    severity     TEXT NOT NULL DEFAULT 'info',        -- info|warning|critical
    payload      TEXT NOT NULL DEFAULT '{}',          -- JSON — контекст события
    created_at   TEXT NOT NULL,
    resolved_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_server_events_server ON server_events(server_id, created_at);

CREATE TABLE IF NOT EXISTS server_services_status (
    server_id     TEXT NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    service_name  TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'unknown',    -- running|stopped|failed|unknown
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (server_id, service_name)
);
