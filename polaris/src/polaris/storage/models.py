"""Внутренние модели Storage. Backend-специфичных полей здесь нет."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ObjectStatus:
    PENDING = "pending"
    UPLOADING = "uploading"
    READY = "ready"
    FAILED = "failed"
    DELETING = "deleting"


class BlobStatus:
    UPLOADING = "uploading"
    READY = "ready"
    FAILED = "failed"
    DELETING = "deleting"


class ChunkStatus:
    PENDING = "pending"
    STORED = "stored"
    DELETE_FAILED = "delete_failed"
    DELETE_REJECTED = "delete_rejected"


@dataclass(frozen=True)
class StorageBucket:
    id: str
    name: str
    dedup_scope: str
    created_at: str
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StorageBlob:
    id: str
    dedup_scope: str
    backend: str
    status: str
    size_bytes: int
    chunk_count: int | None
    checksum_sha256: str | None
    md5_hex: str | None
    created_at: str
    updated_at: str
    completed_at: str | None


@dataclass(frozen=True)
class StorageObject:
    id: str
    bucket: str
    bucket_id: str
    object_key: str
    filename: str
    size_bytes: int
    mime_type: str
    checksum_sha256: str | None
    etag: str | None
    backend: str | None
    status: str
    blob_id: str | None
    created_at: str
    updated_at: str
    completed_at: str | None
    metadata: dict[str, Any] = field(default_factory=dict)
    dedup_scope: str = ""


@dataclass(frozen=True)
class StorageChunk:
    id: str
    blob_id: str
    chunk_index: int
    size_bytes: int
    checksum_sha256: str
    backend_reference: str | None
    status: str
    delete_attempts: int
    last_error: str | None
    last_attempt_at: str | None
    created_at: str


@dataclass(frozen=True)
class StorageOrphan:
    id: str
    backend: str
    kind: str
    blob_id: str | None
    chunk_index: int | None
    marker: str | None
    backend_reference: str | None
    delete_attempts: int
    last_error: str | None
    note: str | None
    created_at: str


@dataclass(frozen=True)
class IntegrityReport:
    ok: bool
    problems: list[str]
    checked_chunks: int = 0
