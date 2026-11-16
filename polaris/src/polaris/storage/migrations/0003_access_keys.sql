-- Phase 3: управляемые S3-ключи (Access Management).
-- Secret Access Key в БД НЕ хранится. SigV4 требует знать секрет при проверке подписи, поэтому обычное
-- «солёное хеширование» (как у паролей) тут невозможно. Вместо этого в БД лежит случайное семя, а секрет
-- выводится как HMAC-SHA256(мастер-ключ, access_key_id, семя, версия). Мастер-ключ хранится вне БД
-- (файл с правами 0600 или переменная окружения), поэтому утечка одной только базы секретов не раскрывает.
CREATE TABLE storage_access_keys (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    description    TEXT NOT NULL DEFAULT '',
    access_key_id  TEXT NOT NULL UNIQUE,
    secret_seed    TEXT NOT NULL,
    secret_version INTEGER NOT NULL DEFAULT 1,
    master_fp      TEXT NOT NULL,
    permissions    TEXT NOT NULL CHECK (json_valid(permissions)),
    all_buckets    INTEGER NOT NULL DEFAULT 0 CHECK (all_buckets IN (0, 1)),
    status         TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
    system         INTEGER NOT NULL DEFAULT 0 CHECK (system IN (0, 1)),
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    revoked_at     TEXT,
    rotated_at     TEXT,
    last_used_at   TEXT
);
CREATE INDEX idx_access_keys_status ON storage_access_keys (status, system);

CREATE TABLE storage_access_key_buckets (
    key_id      TEXT NOT NULL REFERENCES storage_access_keys (id) ON DELETE CASCADE,
    bucket_name TEXT NOT NULL,
    PRIMARY KEY (key_id, bucket_name)
);
