"""Все SQL-запросы Storage. Функции принимают соединение: транзакциями управляет ядро."""

from __future__ import annotations

import json
import sqlite3
import uuid

from polaris.storage.database import utcnow_iso
from polaris.storage.models import (
    MultipartUpload,
    StorageBlob,
    StorageBucket,
    StorageChunk,
    StorageObject,
    StorageOrphan,
)

_OBJECT_SELECT = (
    "SELECT o.*, b.name AS bucket_name, b.dedup_scope AS dedup_scope "
    "FROM storage_objects o JOIN storage_buckets b ON b.id = o.bucket_id"
)


def new_id() -> str:
    return str(uuid.uuid4())


def _bucket(r: sqlite3.Row) -> StorageBucket:
    return StorageBucket(r["id"], r["name"], r["dedup_scope"], r["created_at"], json.loads(r["settings"]))


def _blob(r: sqlite3.Row) -> StorageBlob:
    return StorageBlob(
        r["id"], r["dedup_scope"], r["backend"], r["status"], r["size_bytes"], r["chunk_count"],
        r["checksum_sha256"], r["md5_hex"], r["created_at"], r["updated_at"], r["completed_at"],
        r["checksum_kind"],
    )


def _object(r: sqlite3.Row) -> StorageObject:
    return StorageObject(
        id=r["id"], bucket=r["bucket_name"], bucket_id=r["bucket_id"], object_key=r["object_key"],
        filename=r["filename"], size_bytes=r["size_bytes"], mime_type=r["mime_type"],
        checksum_sha256=r["checksum_sha256"], etag=r["etag"], backend=r["backend"], status=r["status"],
        blob_id=r["blob_id"], created_at=r["created_at"], updated_at=r["updated_at"],
        completed_at=r["completed_at"], metadata=json.loads(r["metadata"]), dedup_scope=r["dedup_scope"],
    )


def _chunk(r: sqlite3.Row) -> StorageChunk:
    return StorageChunk(
        r["id"], r["blob_id"], r["chunk_index"], r["size_bytes"], r["checksum_sha256"],
        r["backend_reference"], r["status"], r["delete_attempts"], r["last_error"],
        r["last_attempt_at"], r["created_at"],
    )


def _orphan(r: sqlite3.Row) -> StorageOrphan:
    return StorageOrphan(
        r["id"], r["backend"], r["kind"], r["blob_id"], r["chunk_index"], r["marker"],
        r["backend_reference"], r["delete_attempts"], r["last_error"], r["note"], r["created_at"],
    )


# ─── buckets ───

def get_bucket(conn, name: str) -> StorageBucket | None:
    r = conn.execute("SELECT * FROM storage_buckets WHERE name=?", (name,)).fetchone()
    return _bucket(r) if r else None


def insert_bucket(conn, name: str, dedup_scope: str, settings: dict) -> StorageBucket:
    bid = new_id()
    conn.execute(
        "INSERT INTO storage_buckets (id, name, dedup_scope, created_at, settings) VALUES (?,?,?,?,?)",
        (bid, name, dedup_scope, utcnow_iso(), json.dumps(settings)),
    )
    return get_bucket(conn, name)  # type: ignore[return-value]


# ─── objects ───

def insert_object(conn, bucket_id, key, filename, mime_type, metadata) -> str:
    oid, now = new_id(), utcnow_iso()
    conn.execute(
        "INSERT INTO storage_objects (id, bucket_id, object_key, filename, mime_type, status, created_at, "
        "updated_at, metadata) VALUES (?,?,?,?,?, 'pending', ?,?,?)",
        (oid, bucket_id, key, filename, mime_type, now, now, json.dumps(metadata)),
    )
    return oid


def get_object(conn, object_id: str) -> StorageObject | None:
    r = conn.execute(_OBJECT_SELECT + " WHERE o.id=?", (object_id,)).fetchone()
    return _object(r) if r else None


def get_ready_object_by_key(conn, bucket_id: str, key: str) -> StorageObject | None:
    r = conn.execute(
        _OBJECT_SELECT + " WHERE o.bucket_id=? AND o.object_key=? AND o.status='ready'", (bucket_id, key)
    ).fetchone()
    return _object(r) if r else None


def list_objects_by_sha(conn, dedup_scope: str, sha256: str) -> list[StorageObject]:
    rows = conn.execute(
        _OBJECT_SELECT + " WHERE b.dedup_scope=? AND o.checksum_sha256=? AND o.status='ready' ORDER BY o.created_at",
        (dedup_scope, sha256),
    ).fetchall()
    return [_object(r) for r in rows]


def set_object_status(conn, object_id: str, status: str) -> None:
    conn.execute("UPDATE storage_objects SET status=?, updated_at=? WHERE id=?", (status, utcnow_iso(), object_id))


def begin_object_upload(conn, object_id: str, blob_id: str) -> None:
    conn.execute(
        "UPDATE storage_objects SET status='uploading', blob_id=?, updated_at=? WHERE id=?",
        (blob_id, utcnow_iso(), object_id),
    )


def fail_object(conn, object_id: str) -> None:
    conn.execute(
        "UPDATE storage_objects SET status='failed', blob_id=NULL, updated_at=? WHERE id=?",
        (utcnow_iso(), object_id),
    )


def mark_object_ready(conn, object_id: str, blob: StorageBlob, etag: str | None = None) -> None:
    now = utcnow_iso()
    conn.execute(
        "UPDATE storage_objects SET status='ready', blob_id=?, size_bytes=?, checksum_sha256=?, etag=?, "
        "backend=?, completed_at=?, updated_at=? WHERE id=?",
        (blob.id, blob.size_bytes, blob.checksum_sha256, etag or blob.md5_hex, blob.backend, now, now, object_id),
    )


def touch_object(conn, object_id: str) -> None:
    conn.execute("UPDATE storage_objects SET updated_at=? WHERE id=?", (utcnow_iso(), object_id))


def delete_object(conn, object_id: str) -> None:
    conn.execute("DELETE FROM storage_objects WHERE id=?", (object_id,))


def count_blob_refs(conn, blob_id: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM storage_objects WHERE blob_id=?", (blob_id,)).fetchone()[0]


def list_objects_with_status(conn, status: str, older_than: str | None = None, limit: int = 500) -> list[StorageObject]:
    sql = _OBJECT_SELECT + " WHERE o.status=?"
    args: list = [status]
    if older_than:
        sql += " AND o.updated_at < ?"
        args.append(older_than)
    rows = conn.execute(sql + " ORDER BY o.updated_at LIMIT ?", (*args, limit)).fetchall()
    return [_object(r) for r in rows]


def list_objects_of_blob(conn, blob_id: str) -> list[StorageObject]:
    rows = conn.execute(_OBJECT_SELECT + " WHERE o.blob_id=?", (blob_id,)).fetchall()
    return [_object(r) for r in rows]


# ─── blobs ───

def insert_composite_blob(conn, dedup_scope: str, backend: str, size: int, chunk_count: int, sha256: str) -> str:
    bid, now = new_id(), utcnow_iso()
    conn.execute(
        "INSERT INTO storage_blobs (id, dedup_scope, backend, status, size_bytes, chunk_count, checksum_sha256, "
        "checksum_kind, created_at, updated_at, completed_at) VALUES (?,?,?, 'ready', ?,?,?, 'composite', ?,?,?)",
        (bid, dedup_scope, backend, size, chunk_count, sha256, now, now, now),
    )
    return bid


def insert_blob(conn, dedup_scope: str, backend: str) -> str:
    bid, now = new_id(), utcnow_iso()
    conn.execute(
        "INSERT INTO storage_blobs (id, dedup_scope, backend, status, created_at, updated_at) "
        "VALUES (?,?,?, 'uploading', ?,?)",
        (bid, dedup_scope, backend, now, now),
    )
    return bid


def get_blob(conn, blob_id: str) -> StorageBlob | None:
    r = conn.execute("SELECT * FROM storage_blobs WHERE id=?", (blob_id,)).fetchone()
    return _blob(r) if r else None


def find_ready_blob(conn, dedup_scope: str, sha256: str) -> StorageBlob | None:
    r = conn.execute(
        "SELECT * FROM storage_blobs WHERE dedup_scope=? AND checksum_sha256=? AND status='ready'",
        (dedup_scope, sha256),
    ).fetchone()
    return _blob(r) if r else None


def set_blob_status(conn, blob_id: str, status: str) -> None:
    conn.execute("UPDATE storage_blobs SET status=?, updated_at=? WHERE id=?", (status, utcnow_iso(), blob_id))


def mark_blob_ready(conn, blob_id: str, size: int, chunk_count: int, sha256: str, md5_hex: str) -> None:
    now = utcnow_iso()
    conn.execute(
        "UPDATE storage_blobs SET status='ready', size_bytes=?, chunk_count=?, checksum_sha256=?, md5_hex=?, "
        "completed_at=?, updated_at=? WHERE id=?",
        (size, chunk_count, sha256, md5_hex, now, now, blob_id),
    )


def touch_blob(conn, blob_id: str) -> None:
    conn.execute("UPDATE storage_blobs SET updated_at=? WHERE id=?", (utcnow_iso(), blob_id))


def delete_blob(conn, blob_id: str) -> None:
    conn.execute("DELETE FROM storage_blobs WHERE id=?", (blob_id,))


def list_blobs_with_status(conn, status: str, older_than: str | None = None, limit: int = 500) -> list[StorageBlob]:
    sql = "SELECT * FROM storage_blobs WHERE status=?"
    args: list = [status]
    if older_than:
        sql += " AND updated_at < ?"
        args.append(older_than)
    return [_blob(r) for r in conn.execute(sql + " ORDER BY updated_at LIMIT ?", (*args, limit)).fetchall()]


def list_unreferenced_ready_blobs(conn, limit: int = 100) -> list[StorageBlob]:
    rows = conn.execute(
        "SELECT * FROM storage_blobs b WHERE status='ready' AND NOT EXISTS "
        "(SELECT 1 FROM storage_objects o WHERE o.blob_id=b.id) LIMIT ?", (limit,)
    ).fetchall()
    return [_blob(r) for r in rows]


# ─── chunks ───

def insert_pending_chunk(conn, blob_id: str, index: int, size: int, sha256: str) -> str:
    cid = new_id()
    conn.execute(
        "INSERT INTO storage_chunks (id, blob_id, chunk_index, size_bytes, checksum_sha256, status, created_at) "
        "VALUES (?,?,?,?,?, 'pending', ?)",
        (cid, blob_id, index, size, sha256, utcnow_iso()),
    )
    return cid


def mark_chunk_stored(conn, chunk_id: str, reference: str) -> None:
    conn.execute(
        "UPDATE storage_chunks SET status='stored', backend_reference=? WHERE id=?", (reference, chunk_id)
    )


def list_chunks(conn, blob_id: str) -> list[StorageChunk]:
    rows = conn.execute("SELECT * FROM storage_chunks WHERE blob_id=? ORDER BY chunk_index", (blob_id,)).fetchall()
    return [_chunk(r) for r in rows]


def delete_chunk(conn, chunk_id: str) -> None:
    conn.execute("DELETE FROM storage_chunks WHERE id=?", (chunk_id,))


def count_chunks(conn, blob_id: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM storage_chunks WHERE blob_id=?", (blob_id,)).fetchone()[0]


def record_chunk_delete_failure(conn, chunk_id: str, error: str, *, rejected: bool) -> None:
    conn.execute(
        "UPDATE storage_chunks SET delete_attempts = delete_attempts + 1, last_error=?, last_attempt_at=?, "
        "status=CASE WHEN ? THEN 'delete_rejected' ELSE 'delete_failed' END WHERE id=?",
        (error[:500], utcnow_iso(), 1 if rejected else 0, chunk_id),
    )


def count_chunks_by_status(conn, status: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM storage_chunks WHERE status=?", (status,)).fetchone()[0]


# ─── orphans ───

def insert_orphan(conn, backend: str, kind: str, *, blob_id=None, chunk_index=None, marker=None,
                  reference=None, note=None) -> str:
    oid = new_id()
    conn.execute(
        "INSERT INTO storage_orphans (id, backend, kind, blob_id, chunk_index, marker, backend_reference, note, "
        "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (oid, backend, kind, blob_id, chunk_index, marker, reference, note, utcnow_iso()),
    )
    return oid


def list_orphans(conn, *, with_reference: bool | None = None, limit: int = 500) -> list[StorageOrphan]:
    sql = "SELECT * FROM storage_orphans"
    if with_reference is True:
        sql += " WHERE backend_reference IS NOT NULL"
    elif with_reference is False:
        sql += " WHERE backend_reference IS NULL"
    return [_orphan(r) for r in conn.execute(sql + " ORDER BY created_at LIMIT ?", (limit,)).fetchall()]


def delete_orphan(conn, orphan_id: str) -> None:
    conn.execute("DELETE FROM storage_orphans WHERE id=?", (orphan_id,))


def record_orphan_delete_failure(conn, orphan_id: str, error: str) -> None:
    conn.execute(
        "UPDATE storage_orphans SET delete_attempts = delete_attempts + 1, last_error=? WHERE id=?",
        (error[:500], orphan_id),
    )


# ─── диагностика (reconcile) ───

def list_inconsistent_ready_blobs(conn, limit: int = 100) -> list[StorageBlob]:
    rows = conn.execute(
        "SELECT * FROM storage_blobs b WHERE status='ready' AND ("
        " chunk_count <> (SELECT COUNT(*) FROM storage_chunks c WHERE c.blob_id=b.id)"
        " OR size_bytes <> COALESCE((SELECT SUM(size_bytes) FROM storage_chunks c WHERE c.blob_id=b.id), 0)"
        " OR EXISTS (SELECT 1 FROM storage_chunks c WHERE c.blob_id=b.id AND c.status <> 'stored')) LIMIT ?",
        (limit,),
    ).fetchall()
    return [_blob(r) for r in rows]


def list_ready_objects_with_unready_blob(conn, limit: int = 100) -> list[StorageObject]:
    rows = conn.execute(
        _OBJECT_SELECT + " WHERE o.status='ready' AND NOT EXISTS "
        "(SELECT 1 FROM storage_blobs x WHERE x.id=o.blob_id AND x.status='ready') LIMIT ?", (limit,)
    ).fetchall()
    return [_object(r) for r in rows]


def count_exhausted_failed_chunks(conn, max_attempts: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM storage_chunks WHERE status='delete_failed' AND delete_attempts >= ?", (max_attempts,)
    ).fetchone()[0]


def count_orphans(conn, *, with_reference: bool) -> int:
    cond = "IS NOT NULL" if with_reference else "IS NULL"
    return conn.execute(f"SELECT COUNT(*) FROM storage_orphans WHERE backend_reference {cond}").fetchone()[0]


# ─── Phase 2: списки, buckets, multipart ───

def key_successor(prefix: str) -> str | None:
    """Наименьшая строка, большая всех строк с данным префиксом (None — верхней границы нет)."""
    chars = list(prefix)
    while chars and ord(chars[-1]) >= 0x10FFFF:
        chars.pop()
    if not chars:
        return None
    code = ord(chars[-1]) + 1
    if code == 0xD800:  # суррогаты в UTF-8 недопустимы
        code = 0xE000
    chars[-1] = chr(code)
    return "".join(chars)


def list_ready_objects(conn, bucket_id: str, prefix: str, from_key: str, limit: int) -> list[StorageObject]:
    sql = _OBJECT_SELECT + " WHERE o.bucket_id=? AND o.status='ready' AND o.object_key >= ?"
    args: list = [bucket_id, max(from_key, prefix)]
    upper = key_successor(prefix) if prefix else None
    if upper is not None:
        sql += " AND o.object_key < ?"
        args.append(upper)
    rows = conn.execute(sql + " ORDER BY o.object_key LIMIT ?", (*args, limit)).fetchall()
    return [_object(r) for r in rows]


def list_buckets(conn) -> list[StorageBucket]:
    return [_bucket(r) for r in conn.execute("SELECT * FROM storage_buckets ORDER BY name").fetchall()]


def count_bucket_objects(conn, bucket_id: str, statuses: tuple[str, ...]) -> int:
    marks = ",".join("?" * len(statuses))
    return conn.execute(
        f"SELECT COUNT(*) FROM storage_objects WHERE bucket_id=? AND status IN ({marks})", (bucket_id, *statuses)
    ).fetchone()[0]


def delete_bucket_dead_objects(conn, bucket_id: str) -> None:
    conn.execute("DELETE FROM storage_objects WHERE bucket_id=? AND status IN ('pending','failed')", (bucket_id,))


def delete_bucket(conn, bucket_id: str) -> None:
    conn.execute("DELETE FROM storage_buckets WHERE id=?", (bucket_id,))


def move_chunks(conn, from_blob_id: str, to_blob_id: str, index_offset: int) -> None:
    conn.execute(
        "UPDATE storage_chunks SET blob_id=?, chunk_index = chunk_index + ? WHERE blob_id=?",
        (to_blob_id, index_offset, from_blob_id),
    )


_UPLOAD_SELECT = (
    "SELECT u.*, b.name AS bucket_name FROM storage_multipart_uploads u "
    "JOIN storage_buckets b ON b.id = u.bucket_id"
)


def _upload(r: sqlite3.Row) -> MultipartUpload:
    return MultipartUpload(r["id"], r["bucket_name"], r["bucket_id"], r["object_key"], r["filename"],
                           r["mime_type"], json.loads(r["metadata"]), r["status"], r["created_at"])


def insert_upload(conn, upload_id: str, bucket_id: str, key: str, filename: str, mime_type: str, metadata: dict) -> None:
    now = utcnow_iso()
    conn.execute(
        "INSERT INTO storage_multipart_uploads (id, bucket_id, object_key, filename, mime_type, metadata, status, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?, 'open', ?,?)",
        (upload_id, bucket_id, key, filename, mime_type, json.dumps(metadata), now, now),
    )


def get_upload(conn, upload_id: str) -> MultipartUpload | None:
    r = conn.execute(_UPLOAD_SELECT + " WHERE u.id=?", (upload_id,)).fetchone()
    return _upload(r) if r else None


def set_upload_status(conn, upload_id: str, status: str) -> None:
    conn.execute("UPDATE storage_multipart_uploads SET status=?, updated_at=? WHERE id=?",
                 (status, utcnow_iso(), upload_id))


def delete_upload(conn, upload_id: str) -> None:
    conn.execute("DELETE FROM storage_multipart_uploads WHERE id=?", (upload_id,))


def count_bucket_uploads(conn, bucket_id: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM storage_multipart_uploads WHERE bucket_id=?", (bucket_id,)).fetchone()[0]


def list_uploads(conn, bucket_id: str, prefix: str, after: tuple[str, str] | None, limit: int) -> list[MultipartUpload]:
    sql = _UPLOAD_SELECT + " WHERE u.bucket_id=? AND u.status='open' AND u.object_key >= ?"
    args: list = [bucket_id, prefix]
    upper = key_successor(prefix) if prefix else None
    if upper is not None:
        sql += " AND u.object_key < ?"
        args.append(upper)
    if after is not None:
        sql += " AND (u.object_key, u.id) > (?, ?)"
        args.extend(after)
    rows = conn.execute(sql + " ORDER BY u.object_key, u.id LIMIT ?", (*args, limit)).fetchall()
    return [_upload(r) for r in rows]


def list_uploads_with_status(conn, status: str, limit: int = 500) -> list[MultipartUpload]:
    rows = conn.execute(_UPLOAD_SELECT + " WHERE u.status=? LIMIT ?", (status, limit)).fetchall()
    return [_upload(r) for r in rows]


def list_uploads_older_than(conn, cutoff: str, limit: int = 500) -> list[MultipartUpload]:
    rows = conn.execute(_UPLOAD_SELECT + " WHERE u.status='open' AND u.created_at < ? LIMIT ?", (cutoff, limit)).fetchall()
    return [_upload(r) for r in rows]


def all_upload_ids(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT id FROM storage_multipart_uploads")}


# ─── Phase 3: статистика, обзор восстановления, список multipart ───

_HIDDEN_BUCKET_SQL = "name NOT LIKE 'polaris-internal.%' AND name NOT LIKE 'polaris-selftest-%'"


def stats(conn) -> dict:
    one = lambda sql, *a: conn.execute(sql, a).fetchone()  # noqa: E731
    objects = one(
        "SELECT COUNT(*), COALESCE(SUM(o.size_bytes), 0) FROM storage_objects o JOIN storage_buckets b ON b.id=o.bucket_id "
        "WHERE o.status='ready' AND " + _HIDDEN_BUCKET_SQL.replace("name ", "b.name "))
    blobs = one("SELECT COUNT(*), COALESCE(SUM(size_bytes), 0) FROM storage_blobs WHERE status='ready'")
    parts = one(
        "SELECT COUNT(*), COALESCE(SUM(o.size_bytes), 0) FROM storage_objects o JOIN storage_buckets b ON b.id=o.bucket_id "
        "WHERE o.status='ready' AND b.name='polaris-internal.multipart'")
    return {
        "buckets": one("SELECT COUNT(*) FROM storage_buckets WHERE " + _HIDDEN_BUCKET_SQL)[0],
        "objects": objects[0], "logical_bytes": objects[1],
        "blobs": blobs[0], "physical_bytes": blobs[1],
        "blobs_in_progress": one("SELECT COUNT(*) FROM storage_blobs WHERE status IN ('uploading','failed','deleting')")[0],
        "multipart_uploads": one("SELECT COUNT(*) FROM storage_multipart_uploads WHERE status='open'")[0],
        "multipart_parts": parts[0], "multipart_bytes": parts[1],
        "chunks": one("SELECT COUNT(*) FROM storage_chunks WHERE backend_reference IS NOT NULL")[0],
    }


def bucket_summaries(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT b.id, b.name, b.created_at, COUNT(o.id) AS objects, COALESCE(SUM(o.size_bytes), 0) AS bytes "
        "FROM storage_buckets b LEFT JOIN storage_objects o ON o.bucket_id=b.id AND o.status='ready' "
        "WHERE " + _HIDDEN_BUCKET_SQL.replace("name ", "b.name ") + " GROUP BY b.id ORDER BY b.name"
    ).fetchall()
    return [dict(r) for r in rows]


def part_stats(conn, bucket_id: str) -> dict[str, tuple[int, int]]:
    rows = conn.execute(
        "SELECT substr(object_key, 1, 32) AS upload_id, COUNT(*) AS n, COALESCE(SUM(size_bytes), 0) AS bytes "
        "FROM storage_objects WHERE bucket_id=? AND status='ready' GROUP BY 1", (bucket_id,)).fetchall()
    return {r["upload_id"]: (r["n"], r["bytes"]) for r in rows}


def list_all_uploads(conn, limit: int) -> list[MultipartUpload]:
    return [_upload(r) for r in conn.execute(_UPLOAD_SELECT + " ORDER BY u.created_at LIMIT ?", (limit,)).fetchall()]


def recovery_overview(conn, limit: int = 50) -> dict:
    one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "orphans_suspected": count_orphans(conn, with_reference=False),
        "orphans_pending_delete": count_orphans(conn, with_reference=True),
        "chunks_delete_failed": count_chunks_by_status(conn, "delete_failed"),
        "chunks_delete_rejected": count_chunks_by_status(conn, "delete_rejected"),
        "chunks_pending": count_chunks_by_status(conn, "pending"),
        "blobs_failed": one("SELECT COUNT(*) FROM storage_blobs WHERE status='failed'"),
        "blobs_deleting": one("SELECT COUNT(*) FROM storage_blobs WHERE status='deleting'"),
        "objects_failed": one("SELECT COUNT(*) FROM storage_objects WHERE status='failed'"),
        "objects_pending": one("SELECT COUNT(*) FROM storage_objects WHERE status='pending'"),
        "orphans": [
            {"id": o.id, "kind": o.kind, "blob_id": o.blob_id, "chunk_index": o.chunk_index, "marker": o.marker,
             "has_reference": o.backend_reference is not None, "delete_attempts": o.delete_attempts,
             "last_error": o.last_error, "created_at": o.created_at}
            for o in list_orphans(conn, limit=limit)],  # ссылки backend (file_id/message_id) наружу не отдаются
        "failed_deletions": [
            {"blob_id": r["blob_id"], "chunk_index": r["chunk_index"], "status": r["status"],
             "attempts": r["delete_attempts"], "last_error": r["last_error"], "last_attempt_at": r["last_attempt_at"]}
            for r in conn.execute(
                "SELECT blob_id, chunk_index, status, delete_attempts, last_error, last_attempt_at FROM storage_chunks "
                "WHERE status IN ('delete_failed','delete_rejected') ORDER BY last_attempt_at DESC LIMIT ?", (limit,))],
    }
