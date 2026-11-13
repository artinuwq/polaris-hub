-- Polaris Storage: начальная схема метаданных. Содержимое файлов в SQLite не хранится.

-- Логическое пространство хранения. dedup_scope — граница дедупликации:
-- содержимое делится только между bucket-ами с одинаковым scope.
CREATE TABLE storage_buckets (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    dedup_scope TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    settings    TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(settings))
);

-- Физически сохранённое содержимое (общее для объектов с одинаковым SHA-256 внутри scope).
CREATE TABLE storage_blobs (
    id              TEXT PRIMARY KEY,
    dedup_scope     TEXT NOT NULL,
    backend         TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('uploading', 'ready', 'failed', 'deleting')),
    size_bytes      INTEGER NOT NULL DEFAULT 0 CHECK (size_bytes >= 0),
    chunk_count     INTEGER CHECK (chunk_count IS NULL OR chunk_count >= 0),
    checksum_sha256 TEXT CHECK (checksum_sha256 IS NULL OR length(checksum_sha256) = 64),
    md5_hex         TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    completed_at    TEXT,
    CHECK (status <> 'ready' OR (checksum_sha256 IS NOT NULL AND chunk_count IS NOT NULL))
);
-- Одно готовое содержимое на (scope, sha256): гарантия дедупликации даже при гонках.
CREATE UNIQUE INDEX uq_blobs_ready_sha ON storage_blobs (dedup_scope, checksum_sha256) WHERE status = 'ready';
CREATE INDEX idx_blobs_status ON storage_blobs (status, updated_at);

-- Логический файл.
CREATE TABLE storage_objects (
    id              TEXT PRIMARY KEY,
    bucket_id       TEXT NOT NULL REFERENCES storage_buckets (id) ON DELETE RESTRICT,
    object_key      TEXT NOT NULL,
    filename        TEXT NOT NULL DEFAULT '',
    size_bytes      INTEGER NOT NULL DEFAULT 0 CHECK (size_bytes >= 0),
    mime_type       TEXT NOT NULL DEFAULT 'application/octet-stream',
    checksum_sha256 TEXT CHECK (checksum_sha256 IS NULL OR length(checksum_sha256) = 64),
    etag            TEXT,
    backend         TEXT,
    status          TEXT NOT NULL CHECK (status IN ('pending', 'uploading', 'ready', 'failed', 'deleting')),
    blob_id         TEXT REFERENCES storage_blobs (id) ON DELETE RESTRICT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    completed_at    TEXT,
    metadata        TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata)),
    CHECK (status <> 'ready' OR (blob_id IS NOT NULL AND checksum_sha256 IS NOT NULL))
);
-- На ключ видим только один готовый объект; загрузки нового значения могут идти параллельно.
CREATE UNIQUE INDEX uq_objects_ready_key ON storage_objects (bucket_id, object_key) WHERE status = 'ready';
CREATE INDEX idx_objects_key ON storage_objects (bucket_id, object_key);
CREATE INDEX idx_objects_blob ON storage_objects (blob_id);
CREATE INDEX idx_objects_status ON storage_objects (status, updated_at);
CREATE INDEX idx_objects_sha ON storage_objects (checksum_sha256);

-- Физическая часть blob. backend_reference — непрозрачная строка драйвера.
CREATE TABLE storage_chunks (
    id                TEXT PRIMARY KEY,
    blob_id           TEXT NOT NULL REFERENCES storage_blobs (id) ON DELETE CASCADE,
    chunk_index       INTEGER NOT NULL CHECK (chunk_index >= 0),
    size_bytes        INTEGER NOT NULL CHECK (size_bytes > 0),
    checksum_sha256   TEXT NOT NULL CHECK (length(checksum_sha256) = 64),
    backend_reference TEXT,
    -- pending: отправка начата, результат неизвестен; stored: ссылка получена;
    -- delete_failed: удаление не удалось, будет повторено; delete_rejected: backend отказал, нужно вмешательство
    status            TEXT NOT NULL CHECK (status IN ('pending', 'stored', 'delete_failed', 'delete_rejected')),
    delete_attempts   INTEGER NOT NULL DEFAULT 0,
    last_error        TEXT,
    last_attempt_at   TEXT,
    created_at        TEXT NOT NULL,
    UNIQUE (blob_id, chunk_index),
    CHECK (status = 'pending' OR backend_reference IS NOT NULL)
);
CREATE INDEX idx_chunks_status ON storage_chunks (status);

-- Физические данные, которые могли остаться в backend после сбоев и не принадлежат ни одному чанку.
-- Без backend_reference (suspected) автоматически НЕ удаляются: принадлежность не подтверждена.
CREATE TABLE storage_orphans (
    id                TEXT PRIMARY KEY,
    backend           TEXT NOT NULL,
    kind              TEXT NOT NULL CHECK (kind IN ('uncertain_upload', 'interrupted_upload', 'db_write_lost')),
    blob_id           TEXT,
    chunk_index       INTEGER,
    marker            TEXT,
    backend_reference TEXT,
    delete_attempts   INTEGER NOT NULL DEFAULT 0,
    last_error        TEXT,
    note              TEXT,
    created_at        TEXT NOT NULL
);
