"""Access Management: хранилище S3-ключей с правами по buckets.

Secret Access Key нигде не хранится: показывается один раз при создании/ротации, а при проверке подписи
выводится заново из мастер-ключа (см. миграцию 0003). Потеря мастер-ключа делает ключи непригодными
(их нужно ротировать), утечка БД без мастер-ключа секретов не раскрывает.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from polaris.storage.database import StorageDatabase, utcnow_iso
from polaris.storage.errors import StorageError

logger = logging.getLogger(__name__)

PERMISSIONS = ("ListBucket", "ReadObject", "WriteObject", "DeleteObject", "MultipartUpload")
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_TOUCH_INTERVAL = timedelta(seconds=60)
SYSTEM_KEY_PREFIX = "polaris-selftest"


class AccessKeyError(StorageError):
    """Ошибка управления ключами; status — рекомендуемый HTTP-статус для management API."""

    def __init__(self, message: str, status: int = 400, field: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.field = field  # поле формы, к которому относится ошибка (для подсветки в интерфейсе)


class MasterKey:
    """32 случайных байта вне БД. Из него выводятся секреты ключей."""

    def __init__(self, key: bytes) -> None:
        if len(key) < 32:
            raise AccessKeyError("мастер-ключ должен быть не короче 32 байт", 500)
        self._key = key

    def __repr__(self) -> str:
        return f"MasterKey(fp={self.fingerprint})"

    @property
    def fingerprint(self) -> str:
        return hmac.new(self._key, b"polaris-master-fingerprint-v1", hashlib.sha256).hexdigest()[:16]

    def derive_secret(self, access_key_id: str, seed: str, version: int) -> str:
        msg = b"\0".join([b"polaris-s3-secret-v1", access_key_id.encode(), seed.encode(), str(version).encode()])
        digest = hmac.new(self._key, msg, hashlib.sha256).digest()[:30]
        return base64.urlsafe_b64encode(digest).decode()  # 40 символов [A-Za-z0-9_-]

    @classmethod
    def load(cls, path: Path, env_value: str = "") -> "MasterKey":
        """Источник по приоритету: переменная окружения (hex/base64), затем файл; файла нет — создаётся (0600)."""
        env_value = (env_value or "").strip()
        if env_value:
            return cls(_decode_key(env_value))
        path = Path(path)
        if path.exists():
            return cls(_decode_key(path.read_text(encoding="ascii").strip()))
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = base64.b64encode(secrets.token_bytes(32)).decode()
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:  # параллельный старт другого процесса
            return cls(_decode_key(path.read_text(encoding="ascii").strip()))
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(encoded + "\n")
        logger.info("Storage: создан мастер-ключ S3-ключей %s (права 0600)", path)
        return cls(_decode_key(encoded))


def _decode_key(text: str) -> bytes:
    try:
        raw = bytes.fromhex(text) if re.fullmatch(r"[0-9a-fA-F]{64,}", text) else base64.b64decode(text, validate=True)
    except (ValueError, binascii.Error):
        raise AccessKeyError("мастер-ключ повреждён: ожидается hex или base64 (32+ байт)", 500) from None
    if len(raw) < 32:
        raise AccessKeyError("мастер-ключ слишком короткий (нужно 32+ байт)", 500)
    return raw


@dataclass(frozen=True)
class AccessKey:
    id: str
    name: str
    description: str
    access_key_id: str
    permissions: tuple[str, ...]
    all_buckets: bool
    buckets: tuple[str, ...]
    status: str
    system: bool
    secret_version: int
    master_ok: bool  # ключ создан/ротирован текущим мастер-ключом
    created_at: str
    updated_at: str
    revoked_at: str | None = None
    rotated_at: str | None = None
    last_used_at: str | None = None


@dataclass(frozen=True)
class ResolvedKey:
    """Результат поиска активного ключа для проверки подписи (секрет только в памяти)."""

    key: AccessKey
    secret: str = field(repr=False)


class AccessKeyStore:
    def __init__(self, db: StorageDatabase, master: MasterKey) -> None:
        self.db = db
        self.master = master

    # ─── валидация ───

    @staticmethod
    def _clean_text(value: str, label: str, limit: int, required: bool, field: str = "name") -> str:
        value = (value or "").strip() if isinstance(value, str) or value is None else str(value)
        if required and not value:
            raise AccessKeyError(f"{label}: обязательное поле", field=field)
        if len(value) > limit or any(ord(c) < 32 for c in value):
            raise AccessKeyError(f"{label}: до {limit} символов, без управляющих символов", field=field)
        return value

    def _validate(self, name, description, permissions, buckets, *, system: bool):
        name = self._clean_text(name, "Название", 80, True)
        description = self._clean_text(description, "Описание", 500, False, "description")
        perms = tuple(p for p in PERMISSIONS if p in set(permissions or ()))
        unknown = set(permissions or ()) - set(PERMISSIONS)
        if unknown:
            raise AccessKeyError(f"неизвестные права: {', '.join(sorted(map(str, unknown)))}", field="permissions")
        if not perms:
            raise AccessKeyError("выберите хотя бы одно право", field="permissions")
        if "MultipartUpload" in perms and "WriteObject" not in perms:
            raise AccessKeyError("MultipartUpload требует WriteObject: multipart-загрузка создаёт объекты", field="permissions")
        if buckets is None:
            all_buckets, names = True, ()
        else:
            names = tuple(sorted({str(b) for b in buckets}))
            if not names:
                raise AccessKeyError("укажите buckets или разрешите доступ ко всем", field="buckets")
            for b in names:
                if not _BUCKET_RE.match(b) or (not system and b.startswith(("polaris-internal.", "polaris-selftest-"))):
                    raise AccessKeyError(f"недопустимое имя bucket: {b}", field="buckets")
            all_buckets = False
        return name, description, perms, all_buckets, names

    # ─── CRUD ───

    def create(self, name: str, description: str, permissions, buckets, *, system: bool = False) -> tuple[AccessKey, str]:
        """Создать ключ. Возвращает (ключ, секрет): секрет больше нигде не сохраняется."""
        name, description, perms, all_buckets, names = self._validate(name, description, permissions, buckets,
                                                                      system=system)
        seed = secrets.token_hex(32)
        for _ in range(5):
            access_key_id = "PLRS" + base64.b32encode(secrets.token_bytes(10)).decode()
            key_uuid, now = secrets.token_hex(16), utcnow_iso()
            try:
                with self.db.transaction() as conn:
                    conn.execute(
                        "INSERT INTO storage_access_keys (id, name, description, access_key_id, secret_seed, "
                        "master_fp, permissions, all_buckets, status, system, created_at, updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?, 'active', ?, ?,?)",
                        (key_uuid, name, description, access_key_id, seed, self.master.fingerprint,
                         json.dumps(list(perms)), int(all_buckets), int(system), now, now))
                    conn.executemany("INSERT INTO storage_access_key_buckets (key_id, bucket_name) VALUES (?,?)",
                                     [(key_uuid, b) for b in names])
                break
            except Exception as exc:  # noqa: BLE001
                if "access_key_id" not in str(exc):
                    raise
        else:
            raise AccessKeyError("не удалось сгенерировать уникальный Access Key ID", 500)
        logger.info("Storage: создан S3-ключ %s (%s)", access_key_id, name)
        return self.get(key_uuid), self.master.derive_secret(access_key_id, seed, 1)

    def _row_to_key(self, conn, row) -> AccessKey:
        buckets = tuple(r[0] for r in conn.execute(
            "SELECT bucket_name FROM storage_access_key_buckets WHERE key_id=? ORDER BY bucket_name", (row["id"],)))
        return AccessKey(
            id=row["id"], name=row["name"], description=row["description"], access_key_id=row["access_key_id"],
            permissions=tuple(json.loads(row["permissions"])), all_buckets=bool(row["all_buckets"]), buckets=buckets,
            status=row["status"], system=bool(row["system"]), secret_version=row["secret_version"],
            master_ok=row["master_fp"] == self.master.fingerprint, created_at=row["created_at"],
            updated_at=row["updated_at"], revoked_at=row["revoked_at"], rotated_at=row["rotated_at"],
            last_used_at=row["last_used_at"])

    def get(self, key_id: str) -> AccessKey:
        conn = self.db.connection()
        row = conn.execute("SELECT * FROM storage_access_keys WHERE id=?", (key_id,)).fetchone()
        if row is None:
            raise AccessKeyError("ключ не найден", 404)
        return self._row_to_key(conn, row)

    def list(self, *, include_system: bool = False) -> list[AccessKey]:
        conn = self.db.connection()
        sql = "SELECT * FROM storage_access_keys" + ("" if include_system else " WHERE system=0") + " ORDER BY created_at"
        return [self._row_to_key(conn, r) for r in conn.execute(sql).fetchall()]

    def update(self, key_id: str, *, name: str | None = None, description: str | None = None) -> AccessKey:
        current = self.get(key_id)
        new_name = self._clean_text(name, "Название", 80, True) if name is not None else current.name
        new_desc = self._clean_text(description, "Описание", 500, False, "description") if description is not None else current.description
        with self.db.transaction() as conn:
            conn.execute("UPDATE storage_access_keys SET name=?, description=?, updated_at=? WHERE id=?",
                         (new_name, new_desc, utcnow_iso(), key_id))
        return self.get(key_id)

    def revoke(self, key_id: str) -> AccessKey:
        """Отозвать: ключ немедленно перестаёт проходить аутентификацию (каждая проверка читает БД)."""
        self.get(key_id)
        with self.db.transaction() as conn:
            conn.execute("UPDATE storage_access_keys SET status='revoked', revoked_at=COALESCE(revoked_at, ?), "
                         "updated_at=? WHERE id=?", (utcnow_iso(), utcnow_iso(), key_id))
        logger.info("Storage: отозван S3-ключ id=%s", key_id)
        return self.get(key_id)

    def rotate(self, key_id: str) -> tuple[AccessKey, str]:
        """Новый секрет для того же Access Key ID; прежний перестаёт работать сразу и не раскрывается."""
        key = self.get(key_id)
        if key.status != "active":
            raise AccessKeyError("отозванный ключ нельзя ротировать — создайте новый", 409)
        seed, version, now = secrets.token_hex(32), key.secret_version + 1, utcnow_iso()
        with self.db.transaction() as conn:
            conn.execute("UPDATE storage_access_keys SET secret_seed=?, secret_version=?, master_fp=?, rotated_at=?, "
                         "updated_at=? WHERE id=? AND status='active'",
                         (seed, version, self.master.fingerprint, now, now, key_id))
        logger.info("Storage: ротирован S3-ключ %s", key.access_key_id)
        return self.get(key_id), self.master.derive_secret(key.access_key_id, seed, version)

    def delete(self, key_id: str) -> None:
        key = self.get(key_id)
        if key.status != "revoked":
            raise AccessKeyError("удалить можно только отозванный ключ: сначала отзовите его", 409)
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM storage_access_keys WHERE id=?", (key_id,))

    def purge_system_keys(self) -> int:
        """Удалить служебные ключи самотестов (на случай прерванного теста)."""
        with self.db.transaction() as conn:
            return conn.execute("DELETE FROM storage_access_keys WHERE system=1").rowcount

    # ─── проверка подписи ───

    def resolve(self, access_key_id: str) -> ResolvedKey | None:
        """Активный ключ с текущим мастер-ключом и его секрет — для SigV4. Отозванные и чужие — None."""
        conn = self.db.connection()
        row = conn.execute("SELECT * FROM storage_access_keys WHERE access_key_id=? AND status='active'",
                           (access_key_id,)).fetchone()
        if row is None:
            return None
        if row["master_fp"] != self.master.fingerprint:
            logger.error("Storage: ключ %s создан другим мастер-ключом — нужна ротация", access_key_id)
            return None
        return ResolvedKey(self._row_to_key(conn, row),
                           self.master.derive_secret(access_key_id, row["secret_seed"], row["secret_version"]))

    def touch(self, key_id: str) -> None:
        """Отметить использование (не чаще раза в минуту на ключ)."""
        conn = self.db.connection()
        row = conn.execute("SELECT last_used_at FROM storage_access_keys WHERE id=?", (key_id,)).fetchone()
        if row is None:
            return
        last = row[0]
        if last:
            age = datetime.now(timezone.utc) - datetime.strptime(last, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
            if age < _TOUCH_INTERVAL:
                return
        with self.db.transaction() as c:
            c.execute("UPDATE storage_access_keys SET last_used_at=? WHERE id=?", (utcnow_iso(), key_id))

    def summary(self) -> dict:
        keys = self.list()
        return {"active": sum(k.status == "active" for k in keys), "revoked": sum(k.status == "revoked" for k in keys),
                "needs_rotation": sum(k.status == "active" and not k.master_ok for k in keys)}
