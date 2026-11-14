-- Phase 2 (S3 API): составное содержимое и состояние multipart-загрузок.

-- 'content'  — checksum_sha256 = SHA-256 всего содержимого (как в этапе 1; участвует в дедупликации);
-- 'composite'— blob собран из чанков частей multipart-загрузки без повторной отправки в backend;
--              checksum_sha256 = SHA-256 от перечня SHA-256 чанков (целостность проверяется по чанкам).
ALTER TABLE storage_blobs ADD COLUMN checksum_kind TEXT NOT NULL DEFAULT 'content'
    CHECK (checksum_kind IN ('content', 'composite'));

-- Незавершённые multipart-загрузки. Сами части — обычные объекты служебного bucket
-- polaris-internal.multipart с ключом '<upload_id>/<номер части>', поэтому на них распространяются
-- все гарантии этапа 1 (восстановление, физическое удаление, ограничения Telegram).
CREATE TABLE storage_multipart_uploads (
    id         TEXT PRIMARY KEY,
    bucket_id  TEXT NOT NULL REFERENCES storage_buckets (id) ON DELETE RESTRICT,
    object_key TEXT NOT NULL,
    filename   TEXT NOT NULL DEFAULT '',
    mime_type  TEXT NOT NULL,
    metadata   TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata)),
    -- open: принимает части; aborting: отмена начата и будет доведена до конца
    status     TEXT NOT NULL CHECK (status IN ('open', 'aborting')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_mpu_bucket_key ON storage_multipart_uploads (bucket_id, object_key, id);
