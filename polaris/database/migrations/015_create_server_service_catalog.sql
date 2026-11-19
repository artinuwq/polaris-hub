-- Migration 015: список установленных на сервере systemd-сервисов (его присылает агент).
-- Из него в интерфейсе выбирают, за какими сервисами следить. Целиком заменяется при каждой отправке.

CREATE TABLE IF NOT EXISTS server_service_catalog (
    server_id     TEXT PRIMARY KEY REFERENCES servers(id) ON DELETE CASCADE,
    services_json TEXT NOT NULL DEFAULT '[]',
    updated_at    TEXT NOT NULL
);
