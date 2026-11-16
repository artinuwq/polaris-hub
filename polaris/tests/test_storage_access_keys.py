"""Access Management: хранение ключей, секреты, отзыв, ротация, мастер-ключ."""
from __future__ import annotations

import base64
import os
import stat
import sys
import unittest

from polaris.storage.access_keys import PERMISSIONS, AccessKeyError, AccessKeyStore, MasterKey
from tests.storage_helpers import StorageTestCase


class MasterKeyTests(unittest.TestCase):
    def setUp(self):
        import tempfile, shutil
        from pathlib import Path
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_created_once_with_private_permissions(self):
        path = self.tmp / "sub" / "master.key"
        first = MasterKey.load(path)
        self.assertTrue(path.exists())
        if sys.platform != "win32":
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        again = MasterKey.load(path)
        self.assertEqual(first.fingerprint, again.fingerprint)
        self.assertNotIn(path.read_text().strip(), repr(first))

    def test_env_value_takes_priority_and_is_validated(self):
        raw = os.urandom(32)
        for value in (raw.hex(), base64.b64encode(raw).decode()):
            self.assertEqual(MasterKey.load(self.tmp / "unused.key", value).fingerprint, MasterKey(raw).fingerprint)
        self.assertFalse((self.tmp / "unused.key").exists())
        for bad in ("short", base64.b64encode(b"x" * 8).decode(), "!!notbase64!!"):
            with self.assertRaises(AccessKeyError):
                MasterKey.load(self.tmp / "x.key", bad)

    def test_corrupted_file_is_reported(self):
        path = self.tmp / "bad.key"
        path.write_text("garbage")
        with self.assertRaises(AccessKeyError):
            MasterKey.load(path)

    def test_derivation_is_deterministic_and_separated(self):
        m = MasterKey(b"k" * 32)
        a = m.derive_secret("ID1", "seed", 1)
        self.assertEqual(a, m.derive_secret("ID1", "seed", 1))
        self.assertEqual(len(a), 40)
        for other in (m.derive_secret("ID2", "seed", 1), m.derive_secret("ID1", "seed2", 1), m.derive_secret("ID1", "seed", 2),
                      MasterKey(b"j" * 32).derive_secret("ID1", "seed", 1)):
            self.assertNotEqual(a, other)


class AccessKeyStoreTests(StorageTestCase):
    def setUp(self):
        super().setUp()
        self.master = MasterKey(b"m" * 32)
        self.store = AccessKeyStore(self.db, self.master)

    def make(self, **kw):
        args = dict(name="ci", description="d", permissions=["ReadObject"], buckets=["files"])
        args.update(kw)
        return self.store.create(**args)

    def test_create_returns_secret_once_and_never_stores_it(self):
        key, secret = self.make()
        self.assertTrue(key.access_key_id.startswith("PLRS") and len(key.access_key_id) == 20)
        self.assertEqual(len(secret), 40)
        # секрета нет ни в одной таблице и ни в одном байте файлов БД (включая WAL)
        for row in self.q("SELECT * FROM storage_access_keys"):
            self.assertNotIn(secret, " ".join(str(v) for v in tuple(row)))
        self.db.connection().execute("PRAGMA wal_checkpoint(FULL)")
        for path in self.tmp.glob("storage.db*"):
            self.assertNotIn(secret.encode(), path.read_bytes())
        self.assertNotIn(secret, repr(key))
        resolved = self.store.resolve(key.access_key_id)
        self.assertEqual(resolved.secret, secret)  # выводится из мастер-ключа, не читается

    def test_without_master_key_database_is_useless(self):
        key, secret = self.make()
        other = AccessKeyStore(self.db, MasterKey(b"x" * 32))
        self.assertIsNone(other.resolve(key.access_key_id))  # ключ создан другим мастер-ключом
        self.assertFalse(other.get(key.id).master_ok)
        self.assertEqual(other.summary()["needs_rotation"], 1)
        rotated, new_secret = other.rotate(key.id)  # ротация перевыпускает ключ под новый мастер-ключ
        self.assertTrue(rotated.master_ok)
        self.assertEqual(other.resolve(key.access_key_id).secret, new_secret)
        self.assertNotEqual(secret, new_secret)

    def test_validation(self):
        bad = [dict(name=""), dict(name="x" * 81), dict(name="a\nb"), dict(description="x" * 501), dict(permissions=[]),
               dict(permissions=["ReadObject", "Nope"]), dict(permissions=["MultipartUpload"]),
               dict(buckets=[]), dict(buckets=["UPPER"]), dict(buckets=["polaris-internal.multipart"]),
               dict(buckets=["polaris-selftest-abc"])]
        for kw in bad:
            with self.assertRaises(AccessKeyError, msg=kw):
                self.make(**kw)
        self.make(permissions=["WriteObject", "MultipartUpload"])
        self.make(buckets=None)  # все buckets

    def test_least_privilege_exactly_what_was_selected(self):
        key, _ = self.make(permissions=["DeleteObject", "ListBucket"], buckets=["bucket-one", "bucket-two"])
        self.assertEqual(key.permissions, ("ListBucket", "DeleteObject"))
        self.assertEqual((key.all_buckets, key.buckets), (False, ("bucket-one", "bucket-two")))
        all_key, _ = self.make(buckets=None)
        self.assertEqual((all_key.all_buckets, all_key.buckets), (True, ()))
        self.assertEqual(set(PERMISSIONS), {"ListBucket", "ReadObject", "WriteObject", "DeleteObject", "MultipartUpload"})

    def test_revoke_is_immediate_and_idempotent(self):
        key, _ = self.make()
        self.assertIsNotNone(self.store.resolve(key.access_key_id))
        revoked = self.store.revoke(key.id)
        self.assertEqual(revoked.status, "revoked")
        self.assertIsNone(self.store.resolve(key.access_key_id))
        self.store.revoke(key.id)
        self.assertEqual(self.store.summary(), {"active": 0, "revoked": 1, "needs_rotation": 0})

    def test_rotate_changes_secret_keeps_identity(self):
        key, old = self.make()
        rotated, new = self.store.rotate(key.id)
        self.assertNotEqual(old, new)
        self.assertEqual((rotated.access_key_id, rotated.secret_version), (key.access_key_id, 2))
        self.assertEqual(self.store.resolve(key.access_key_id).secret, new)
        self.assertNotEqual(self.store.resolve(key.access_key_id).secret, old)
        self.assertIsNotNone(rotated.rotated_at)
        self.store.revoke(key.id)
        with self.assertRaises(AccessKeyError) as cm:
            self.store.rotate(key.id)
        self.assertEqual(cm.exception.status, 409)

    def test_delete_only_after_revoke(self):
        key, _ = self.make()
        with self.assertRaises(AccessKeyError) as cm:
            self.store.delete(key.id)
        self.assertEqual(cm.exception.status, 409)
        self.store.revoke(key.id)
        self.store.delete(key.id)
        with self.assertRaises(AccessKeyError) as cm:
            self.store.get(key.id)
        self.assertEqual(cm.exception.status, 404)
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM storage_access_key_buckets")[0]["n"], 0)

    def test_update_name_and_description_only(self):
        key, _ = self.make()
        updated = self.store.update(key.id, description="новое описание")
        self.assertEqual((updated.name, updated.description), ("ci", "новое описание"))
        self.assertEqual(self.store.update(key.id, name="renamed").name, "renamed")
        with self.assertRaises(AccessKeyError):
            self.store.update(key.id, name="")

    def test_unknown_and_foreign_ids(self):
        self.assertIsNone(self.store.resolve("PLRSNOPE"))
        with self.assertRaises(AccessKeyError):
            self.store.get("nope")

    def test_last_used_is_throttled(self):
        key, _ = self.make()
        self.assertIsNone(self.store.get(key.id).last_used_at)
        self.store.touch(key.id)
        first = self.store.get(key.id).last_used_at
        self.assertIsNotNone(first)
        self.store.touch(key.id)
        self.assertEqual(self.store.get(key.id).last_used_at, first)  # не чаще раза в минуту

    def test_system_keys_hidden_and_purgeable(self):
        self.make(name="visible")
        self.store.create("sys", "", ["ReadObject"], ["polaris-selftest-abc"], system=True)
        self.assertEqual([k.name for k in self.store.list()], ["visible"])
        self.assertEqual(len(self.store.list(include_system=True)), 2)
        self.assertEqual(self.store.purge_system_keys(), 1)

    def test_ids_are_unique(self):
        ids = {self.make()[0].access_key_id for _ in range(30)}
        self.assertEqual(len(ids), 30)


if __name__ == "__main__":
    unittest.main()
