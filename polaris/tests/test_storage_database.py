"""Тесты слоя БД Storage: схема, миграции, ограничения, транзакции, потоки."""
from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from polaris.storage.database import Migration, StorageDatabase, load_migrations, split_statements
from polaris.storage.errors import MigrationError

from tests.storage_helpers import StorageTestCase


class DatabaseTests(StorageTestCase):
    def test_tables_created(self):
        names = {r["name"] for r in self.q("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"storage_buckets", "storage_blobs", "storage_objects", "storage_chunks",
                         "storage_orphans", "schema_migrations"} <= names)

    def test_pragmas(self):
        conn = self.db.connection()
        self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 5000)

    def test_migrate_is_idempotent_and_versioned(self):
        version = self.db.schema_version()
        self.assertEqual(version, load_migrations()[-1].version)
        self.assertEqual(self.db.migrate(), version)
        self.assertEqual(len(self.q("SELECT * FROM schema_migrations")), len(load_migrations()))

    def test_incremental_migration_applies_only_new(self):
        extra = Migration(2, "0002_extra", "CREATE TABLE extra_t (id INTEGER PRIMARY KEY);\n-- коммент\n")
        self.assertEqual(self.db.migrate(load_migrations() + [extra]), 2)
        self.assertTrue(self.q("SELECT name FROM sqlite_master WHERE name='extra_t'"))

    def test_failed_migration_rolls_back(self):
        bad = Migration(2, "0002_bad", "CREATE TABLE ok_t (id INTEGER);\nCREATE TABLE ok_t (id INTEGER);")
        with self.assertRaises(sqlite3.OperationalError):
            self.db.migrate(load_migrations() + [bad])
        self.assertFalse(self.q("SELECT name FROM sqlite_master WHERE name='ok_t'"))
        self.assertEqual(self.db.schema_version(), 1)

    def test_changed_applied_migration_is_rejected(self):
        changed = Migration(1, "0001_initial", "SELECT 1;")
        with self.assertRaises(MigrationError):
            self.db.migrate([changed])

    def test_database_from_newer_version_is_rejected(self):
        self.db.migrate(load_migrations() + [Migration(2, "0002_x", "SELECT 1;")])
        with self.assertRaises(MigrationError):
            self.db.migrate()  # «откат кода» на базу со схемой 2

    def test_split_statements_handles_comments_and_trailing(self):
        self.assertEqual(list(split_statements("-- a\nSELECT 1; -- b\nSELECT 2;\n-- tail\n")),
                         ["-- a\nSELECT 1; -- b", "SELECT 2;"])

    def test_unique_chunk_index_per_blob(self):
        c = self.db.connection()
        c.execute("INSERT INTO storage_blobs (id,dedup_scope,backend,status,created_at,updated_at) VALUES ('b','s','m','uploading','t','t')")
        ins = "INSERT INTO storage_chunks (id,blob_id,chunk_index,size_bytes,checksum_sha256,status,created_at) VALUES (?, 'b', 0, 1, ?, 'pending', 't')"
        c.execute(ins, ("c1", "0" * 64))
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute(ins, ("c2", "0" * 64))

    def test_foreign_keys_enforced(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.connection().execute(
                "INSERT INTO storage_chunks (id,blob_id,chunk_index,size_bytes,checksum_sha256,status,created_at) "
                "VALUES ('c','missing',0,1,?, 'pending','t')", ("0" * 64,))

    def test_check_constraints(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.connection().execute(
                "INSERT INTO storage_blobs (id,dedup_scope,backend,status,created_at,updated_at) VALUES ('b','s','m','ready','t','t')")

    def test_transaction_commit_and_rollback(self):
        with self.db.transaction() as c:
            c.execute("INSERT INTO storage_buckets VALUES ('1','one','one','t','{}')")
        with self.assertRaises(RuntimeError):
            with self.db.transaction() as c:
                c.execute("INSERT INTO storage_buckets VALUES ('2','two','two','t','{}')")
                raise RuntimeError
        self.assertEqual([r["name"] for r in self.q("SELECT name FROM storage_buckets WHERE id IN ('1','2')")], ["one"])

    def test_nested_transaction_joins_outer(self):
        with self.assertRaises(RuntimeError):
            with self.db.transaction() as c:
                with self.db.transaction() as c2:
                    c2.execute("INSERT INTO storage_buckets VALUES ('3','three','three','t','{}')")
                raise RuntimeError
        self.assertFalse(self.q("SELECT 1 FROM storage_buckets WHERE id='3'"))

    def test_connection_per_thread(self):
        seen = []
        t = threading.Thread(target=lambda: seen.append(self.db.connection()))
        t.start(); t.join()
        self.assertIsNot(seen[0], self.db.connection())

    def test_concurrent_writers_do_not_fail(self):
        errors = []

        def work(n):
            try:
                for i in range(25):
                    with self.db.transaction() as c:
                        c.execute("INSERT INTO storage_buckets VALUES (?,?,?,?,'{}')",
                                  (f"{n}-{i}", f"b{n}-{i}", "s", "t"))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
        [t.start() for t in threads]; [t.join() for t in threads]
        self.assertEqual(errors, [])
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM storage_buckets")[0]["n"], 8 * 25 + 1)  # +files

    def test_parallel_migrate_from_two_connections(self):
        path = self.tmp / "other.db"
        errors = []

        def run():
            try:
                d = StorageDatabase(path); d.migrate(); d.close()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        ts = [threading.Thread(target=run) for _ in range(4)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
