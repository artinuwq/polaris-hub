"""Management-слой Storage: статус, диагностика, конфигурация, buckets, объекты, S3-ключи, multipart.

Не зависит от веб-фреймворка: методы возвращают словари и бросают AdminError (api.py переводит её в HTTP).
Секреты (Bot Token, Secret Access Key) наружу не отдаются: токен маскируется, секрет ключа показывается
один раз в ответе на создание/ротацию и нигде не сохраняется и не логируется.
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from polaris.infra.config_store import read_env_value, write_env_value
from polaris.infra.settings import Settings
from polaris.modules.storage import download_links, media
from polaris.modules.storage.config_fields import BY_KEY, FIELDS, FieldError, from_env, mask_secret, to_env
from polaris.storage import core as storage_core
from polaris.storage.access_keys import PERMISSIONS, AccessKey, AccessKeyError, AccessKeyStore
from polaris.storage.backends.telegram import TelegramConfig, TelegramDriver
from polaris.storage.database import load_migrations, utcnow_iso
from polaris.storage.errors import (
    BucketExists, BucketNotEmpty, BucketNotFound, IntegrityError, InvalidName, MultipartError, ObjectConflict, ObjectNotFound,
    ObjectNotReady, StorageBusy, StorageError, StorageUnavailable,
)
from polaris.storage.multipart import MAX_PARTS

logger = logging.getLogger(__name__)

ENV_PATH = Path(__file__).resolve().parents[4] / ".env"
CHECKS = ("database", "core", "telegram", "s3", "credentials")
TEST_STEPS = ("config", "telegram", "write", "read", "hash", "delete", "s3")
_STEP_LABELS = {
    "config": "Проверка конфигурации", "telegram": "Проверка Telegram Backend", "write": "Запись тестового объекта",
    "read": "Чтение тестового объекта", "hash": "Проверка SHA-256 после скачивания",
    "delete": "Удаление тестового объекта", "s3": "Полный цикл S3-запроса",
}
_REQUIRES = {"read": ("write",), "hash": ("write", "read"), "delete": ("write",)}
SELFTEST_BUCKET = storage_core.INTERNAL_BUCKET_PREFIX + "selftest"
_UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class AdminError(Exception):
    def __init__(self, status: int, detail: str, errors: dict[str, str] | None = None) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.errors = errors or {}


def _now() -> str:
    return utcnow_iso()


class StorageAdmin:
    def __init__(self, settings: Settings, *, env_path: Path | None = None, get_service: Callable | None = None,
                 get_s3: Callable | None = None) -> None:
        self.settings = settings
        self.env_path = env_path or ENV_PATH
        if get_service is None:
            from polaris.storage import get_storage_service as get_service
        if get_s3 is None:
            from polaris.s3 import get_s3_app as get_s3
        self._get_service, self._get_s3 = get_service, get_s3
        self._health: dict[str, dict] = {}
        self._health_lock = threading.Lock()
        self._test_lock = threading.Lock()
        self.last_tests: dict | None = None

    # ─── доступ к подсистемам ───

    def _service(self):
        return self._get_service()

    def _ready_service(self):
        service = self._service()
        if service is None or service.state != "ready":
            reasons = "; ".join(service.reasons) if service is not None and service.reasons else \
                "Storage выключен: включите его в разделе Configuration и перезапустите Polaris"
            raise AdminError(503, f"Storage недоступен: {reasons}")
        return service

    def _core(self) -> storage_core.StorageCore:
        return self._ready_service().get_core()

    def _store(self) -> AccessKeyStore:
        service = self._ready_service()
        if service.access_keys is None:
            raise AdminError(503, "Управление ключами недоступно: " + (service.access_keys_error or "мастер-ключ не загружен"))
        return service.access_keys

    # ─── статус и диагностика ───

    def status(self) -> dict:
        """Быстрый статус без сетевых вызовов: последние результаты реальных проверок и точная статистика БД."""
        service = self._service()
        state = service.state if service is not None else "disabled"
        stats = None
        if state == "ready":
            try:
                stats = self._core().stats()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Storage: не удалось собрать статистику: %s", type(exc).__name__)
        app = self._get_s3()
        saved_prefix = self._saved("s3_path_prefix")
        return {
            "storage": {"state": state, "reasons": list(service.reasons) if service is not None else []},
            "components": {name: self._record(name) for name in CHECKS},
            "stats": stats,
            "s3": {"enabled": bool(self.settings.s3_enabled), "running": app is not None,
                   "path_prefix": app.config.path_prefix if app is not None else saved_prefix,
                   "region": app.config.region if app is not None else self._saved("s3_region"),
                   "addressing_style": "path"},
            "restart_required": any(f["pending_restart"] for f in self._fields_view()),
        }

    def _record(self, name: str) -> dict:
        with self._health_lock:
            return dict(self._health.get(name) or {"name": name, "status": "unknown", "message": "Ещё не проверялось",
                                                   "checked_at": None, "last_ok_at": None, "duration_ms": None,
                                                   "details": {}})

    def run_checks(self, names: list[str] | None = None) -> dict:
        names = list(names or CHECKS)
        unknown = [n for n in names if n not in CHECKS]
        if unknown:
            raise AdminError(422, f"неизвестные проверки: {', '.join(unknown)}")
        results = {}
        for name in names:
            started = time.monotonic()
            try:
                status, message, details = getattr(self, f"_check_{name}")()
            except Exception as exc:  # noqa: BLE001
                logger.exception("Storage: проверка %s завершилась исключением", name)
                status, message, details = "error", f"Проверка не выполнилась: {type(exc).__name__}", {}
            duration = int((time.monotonic() - started) * 1000)
            now = _now()
            with self._health_lock:
                previous = self._health.get(name) or {}
                record = {"name": name, "status": status, "message": message, "details": details, "checked_at": now,
                          "duration_ms": duration,
                          "last_ok_at": now if status in ("ok", "warning") else previous.get("last_ok_at")}
                self._health[name] = record
            results[name] = dict(record)
        return results

    def _check_database(self):
        service = self._service()
        if service is None or service.state != "ready":
            return "unavailable", self._why_unavailable(service), {}
        db = service._db
        db.connection().execute("SELECT 1").fetchone()
        version, expected = db.schema_version(), load_migrations()[-1].version
        details = {"schema_version": version, "expected_schema_version": expected,
                   "size_bytes": Path(db.path).stat().st_size if Path(db.path).exists() else 0,
                   "journal_mode": db.connection().execute("PRAGMA journal_mode").fetchone()[0]}
        if version != expected:
            return "error", f"Версия схемы {version}, ожидается {expected}", details
        return "ok", "База доступна, схема актуальна", details

    @staticmethod
    def _why_unavailable(service) -> str:
        if service is None or service.state == "disabled":
            return "Storage выключен (STORAGE_ENABLED=false)"
        return "; ".join(service.reasons) or "Storage не запущен"

    def _check_core(self):
        service = self._service()
        if service is None or service.state == "disabled":
            return "disabled", "Storage выключен (STORAGE_ENABLED=false)", {}
        if service.state != "ready":
            return "error", self._why_unavailable(service), {}
        report = service.last_report
        details = {"last_reconcile_at": service.last_report_at,
                   "reconcile": report.summary() if report is not None else None}
        if report is not None and report.needs_attention:
            return "warning", "Работает; сверка нашла записи, требующие внимания (см. Diagnostics → Recovery)", details
        return "ok", "Storage Core запущен", details

    def _check_telegram(self):
        service = self._service()
        if service is None or service.state != "ready":
            problems = service.config.backend_problems() if service is not None else []
            if problems:
                return "unavailable", "Telegram backend не настроен: " + ", ".join(problems), {}
            return "unavailable", self._why_unavailable(service), {}
        health = service.get_core().backend_health()
        keys = ("bot", "chat_type", "bot_status", "can_delete_messages", "warning")
        details = {k: health.details[k] for k in keys if k in health.details}
        if not health.ok:
            return "error", health.message or "Telegram недоступен", details
        if health.details.get("warning"):
            return "warning", health.details["warning"], details
        return "ok", "Бот отвечает, чат доступен", details

    def _check_s3(self):
        app = self._get_s3()
        if app is None:
            if self.settings.s3_enabled:
                return "error", "S3 включён в конфигурации, но не запущен (причина в журнале Polaris)", {}
            return "disabled", "S3 API выключен (S3_ENABLED=false)", {}
        service = self._service()
        s = app.stats
        details = {"path_prefix": app.config.path_prefix, "region": app.config.region, "requests": s.requests,
                   "errors_5xx": s.errors_5xx, "last_request_at": s.last_request_at, "last_ok_at": s.last_ok_at,
                   "last_error": s.last_error}
        if service is None or service.state != "ready":
            return "error", "S3 запущен, но Storage недоступен: " + self._why_unavailable(service), details
        return "ok", "S3 API принимает запросы (полный цикл проверяет Storage Test)", details

    def _check_credentials(self):
        service = self._service()
        if service is None or service.state != "ready":
            return "unavailable", self._why_unavailable(service), {}
        if service.access_keys is None:
            return "error", "Access Management недоступен: " + (service.access_keys_error or "мастер-ключ не загружен"), {}
        summary = service.access_keys.summary()
        app = self._get_s3()
        config_keys = len(app.static_key_ids) if app is not None else 0
        details = {**summary, "config_keys": config_keys}
        if summary["needs_rotation"]:
            return "warning", f"{summary['needs_rotation']} ключ(ей) создано другим мастер-ключом — ротируйте их", details
        if summary["active"] + config_keys == 0:
            return "warning", "Нет ни одного действующего ключа — создайте ключ в разделе Access Keys", details
        return "ok", "Источник ключей доступен", details

    def diagnostics(self) -> dict:
        service = self._service()
        ready = service is not None and service.state == "ready"
        report = service.last_report if ready else None
        return {
            "checks": {name: self._record(name) for name in CHECKS},
            "multipart": self._core().multipart.list_all() if ready else [],
            "recovery": self._core().recovery_overview() if ready else None,
            "last_reconcile": {"at": service.last_report_at, "summary": report.summary(),
                               "needs_attention": report.needs_attention} if report is not None else None,
            "last_tests": self.last_tests,
        }

    # ─── конфигурация ───

    def _saved(self, key: str) -> Any:
        """Значение, которое будет действовать после перезапуска: из .env, иначе — действующее сейчас."""
        field = BY_KEY[key]
        text = read_env_value(self.env_path, field.env)
        if text is None:
            return getattr(self.settings, field.attr)
        return from_env(field, text)

    def _fields_view(self) -> list[dict]:
        out = []
        for field in FIELDS:
            running, saved = getattr(self.settings, field.attr), self._saved(field.key)
            item = {"key": field.key, "group": field.group, "label": field.label, "kind": field.kind,
                    "help": field.help, "pending_restart": saved != running, "secret": field.secret}
            if field.secret:
                item.update({"configured": bool(saved), "masked": mask_secret(saved), "applied_configured": bool(running),
                             "uses_main_bot_token": not saved})
            else:
                item.update({"value": saved, "applied": running})
            out.append(item)
        return out

    def config_get(self) -> dict:
        fields = self._fields_view()
        groups: dict[str, list[dict]] = {}
        for f in fields:
            groups.setdefault(f["group"], []).append(f)
        env_ok = os.access(self.env_path if self.env_path.exists() else self.env_path.parent, os.W_OK)
        return {"groups": groups, "restart_required": any(f["pending_restart"] for f in fields),
                "env_file": {"writable": env_ok}, "s3": {"addressing_style": "path",
                                                         "path_prefix": self._saved("s3_path_prefix"),
                                                         "region": self._saved("s3_region")}}

    def config_update(self, changes: dict, confirm: bool) -> dict:
        if not isinstance(changes, dict) or not changes:
            raise AdminError(422, "нет изменений", {"changes": "ожидается непустой объект"})
        errors: dict[str, str] = {}
        normalized: dict[str, Any] = {}
        for key, raw in changes.items():
            field = BY_KEY.get(key)
            if field is None:
                errors[str(key)] = "неизвестный параметр"
                continue
            if raw is None:
                continue  # null = не менять (для секретов — оставить прежний)
            try:
                normalized[key] = field.parse(raw)
            except FieldError as exc:
                errors[key] = str(exc)
        if errors:
            raise AdminError(422, "Проверьте значения", errors)
        diff = []
        for key, new in normalized.items():
            field, old = BY_KEY[key], self._saved(key)
            if new == old:
                continue
            entry = {"key": key, "label": field.label, "secret": field.secret, "restart_required": True}
            if field.secret:
                entry.update({"old": mask_secret(old) or "не задан (основной токен)",
                              "new": mask_secret(new) if new else "очистить (основной токен)", "clear": not new})
            else:
                entry.update({"old": to_env(old), "new": to_env(new)})
            diff.append(entry)
        warnings = []
        effective = {k: normalized.get(k, self._saved(k)) for k in ("storage_enabled", "s3_enabled", "telegram_chat_id")}
        if effective["s3_enabled"] and not effective["storage_enabled"]:
            warnings.append("S3 включён, но Storage выключен: S3 будет отвечать 503")
        if effective["storage_enabled"] and not effective["telegram_chat_id"]:
            warnings.append("Storage включён, но не задан Telegram Chat ID: backend останется недоступным")
        if not diff:
            return {"applied": False, "diff": [], "warnings": warnings, "message": "Изменений нет",
                    "restart_required": any(f["pending_restart"] for f in self._fields_view())}
        if not confirm:
            return {"applied": False, "diff": diff, "warnings": warnings, "restart_required": True,
                    "message": "Проверьте изменения и подтвердите применение"}
        try:
            for entry in diff:
                write_env_value(self.env_path, BY_KEY[entry["key"]].env, to_env(normalized[entry["key"]]))
        except (OSError, ValueError) as exc:
            logger.error("Storage: не удалось записать конфигурацию: %s", type(exc).__name__)
            raise AdminError(500, "Не удалось записать .env (проверьте права на файл)") from None
        logger.info("Storage: сохранена конфигурация, параметры: %s", ", ".join(e["key"] for e in diff))
        return {"applied": True, "diff": diff, "warnings": warnings, "restart_required": True,
                "message": "Сохранено в .env. Изменения вступят в силу после перезапуска Polaris — "
                           "автоматически он не перезапускался."}

    # ─── buckets ───

    @staticmethod
    def _bucket_dict(row: dict) -> dict:
        return {"name": row["name"], "created_at": row["created_at"], "object_count": row["objects"],
                "total_size": row["bytes"]}

    def buckets_list(self) -> dict:
        return {"buckets": [self._bucket_dict(r) for r in self._core().bucket_summaries()]}

    def _visible_bucket(self, name: str) -> dict:
        if storage_core.is_hidden_bucket(name):
            raise AdminError(404, "Bucket не найден")
        for row in self._core().bucket_summaries():
            if row["name"] == name:
                return self._bucket_dict(row)
        raise AdminError(404, "Bucket не найден")

    def bucket_create(self, name: Any) -> dict:
        from polaris.s3 import errors as s3errors
        from polaris.s3.util import validate_bucket_name

        if not isinstance(name, str):
            raise AdminError(422, "Проверьте значения", {"name": "укажите имя bucket"})
        name = name.strip()
        try:
            validate_bucket_name(name)
        except s3errors.S3Error:
            raise AdminError(422, "Проверьте значения", {
                "name": "3–63 символа: строчные латинские буквы, цифры, «-» и «.»; начало и конец — буква или цифра; "
                        "не IP-адрес"}) from None
        try:
            self._core().create_bucket(name)
        except BucketExists:
            raise AdminError(409, "Bucket с таким именем уже существует", {"name": "имя занято"}) from None
        except InvalidName:
            raise AdminError(422, "Проверьте значения", {"name": "имя зарезервировано"}) from None
        logger.info("Storage: создан bucket %s", name)
        return self._visible_bucket(name)

    def bucket_get(self, name: str) -> dict:
        return self._visible_bucket(name)

    def bucket_objects(self, name: str, prefix: str = "", after: str = "", limit: int = 100) -> dict:
        self._visible_bucket(name)
        limit = max(1, min(int(limit), 500))
        objs = self._core().list_objects(name, prefix=prefix or "", from_key=(after + "\0") if after else "",
                                         limit=limit + 1)
        page = objs[:limit]
        return {"objects": [self._object_brief(o) for o in page], "truncated": len(objs) > limit,
                "next_after": page[-1].object_key if len(objs) > limit and page else None}

    def bucket_delete(self, name: str) -> dict:
        self._visible_bucket(name)
        try:
            self._core().delete_bucket(name)
        except BucketNotEmpty:
            raise AdminError(409, "Bucket не пуст: сначала удалите объекты и отмените multipart-загрузки") from None
        except BucketNotFound:
            raise AdminError(404, "Bucket не найден") from None
        logger.info("Storage: удалён bucket %s", name)
        return {}

    # ─── объекты ───

    @staticmethod
    def _object_brief(o) -> dict:
        return {"id": o.id, "key": o.object_key, "size": o.size_bytes, "content_type": o.mime_type,
                "last_modified": o.completed_at or o.created_at, "etag": o.etag}

    def _object(self, object_id: str):
        try:
            obj = self._core().get_metadata(object_id)
        except (ObjectNotFound, ObjectNotReady):
            raise AdminError(404, "Объект не найден") from None
        if storage_core.is_hidden_bucket(obj.bucket):
            raise AdminError(404, "Объект не найден")
        return obj

    def object_get(self, object_id: str) -> dict:
        o = self._object(object_id)
        s3 = o.metadata.get("s3", {}) if isinstance(o.metadata, dict) else {}
        return {**self._object_brief(o), "bucket": o.bucket, "created_at": o.created_at, "status": o.status,
                "user_metadata": s3.get("meta", {}), "headers": s3.get("headers", {})}

    def object_delete(self, object_id: str) -> dict:
        self._object(object_id)
        try:
            complete = self._core().delete_object(object_id)
        except ObjectNotFound:
            raise AdminError(404, "Объект не найден") from None
        except ObjectConflict as exc:
            raise AdminError(409, str(exc)) from None
        return {"physical_complete": complete}

    def object_download(self, object_id: str) -> tuple[dict, Iterator[bytes]]:
        o = self._object(object_id)
        try:
            return {"key": o.object_key, "content_type": o.mime_type, "size": o.size_bytes}, self._core().stream(o.id)
        except StorageBusy:
            raise AdminError(503, "Слишком много одновременных скачиваний, повторите позже") from None

    def _link_secret(self) -> str:
        secret = self.settings.telegram_bot_token or self.settings.update_api_token
        if not secret:
            raise AdminError(503, "Не задан секрет для подписи ссылок (TELEGRAM_BOT_TOKEN или UPDATE_API_TOKEN)")
        return secret

    def download_link(self, object_id: str) -> dict:
        """Одноразово-читаемая ссылка на скачивание без заголовков авторизации (см. download_links)."""
        o = self._object(object_id)
        token = download_links.sign(self._link_secret(), o.id)
        return {"path": f"/api/storage/download/{token}", "file_name": o.object_key.rsplit("/", 1)[-1] or "object",
                "expires_in": download_links.TTL_SECONDS}

    def download_by_token(self, token: str) -> tuple[dict, Iterator[bytes]]:
        object_id = download_links.verify(self._link_secret(), token)
        if object_id is None:
            raise AdminError(403, "Ссылка недействительна или истекла")
        return self.object_download(object_id)

    # ─── воспроизведение видео/аудио (Range) ───

    def media_link(self, object_id: str) -> dict:
        """Ссылка для встроенного плеера: без заголовков авторизации, на один объект, на несколько часов."""
        o = self._object(object_id)
        mime = media.media_mime(o.object_key, o.mime_type)
        if mime is None:
            raise AdminError(415, "Этот тип файла не воспроизводится в плеере — его можно скачать")
        token = download_links.sign(self._link_secret(), media.MEDIA_PREFIX + o.id, ttl=media.MEDIA_TTL)
        return {"path": f"/api/storage/media/{token}", "file_name": o.object_key.rsplit("/", 1)[-1] or "object",
                "content_type": mime, "size": o.size_bytes, "expires_in": media.MEDIA_TTL}

    def media_open(self, token: str, range_header: str | None) -> tuple[dict, Iterator[bytes] | None]:
        """Кусок файла для плеера. info: status (200/206/416), content_type, size, start, end.
        Поток — None при 416. Первая порция читается сразу: сбой Storage станет честным 503/502, а не обрывом ответа."""
        subject = download_links.verify(self._link_secret(), token)
        if subject is None or not subject.startswith(media.MEDIA_PREFIX):
            raise AdminError(403, "Ссылка недействительна или истекла")
        o = self._object(subject[len(media.MEDIA_PREFIX):])
        mime = media.media_mime(o.object_key, o.mime_type)
        if mime is None:
            raise AdminError(415, "Этот тип файла не воспроизводится в плеере")
        size = o.size_bytes
        info = {"status": 200, "content_type": mime, "size": size, "start": 0, "end": max(0, size - 1)}
        try:
            span = media.parse_range(range_header, size)
        except media.RangeNotSatisfiable:
            return {**info, "status": 416}, None
        if span is not None:
            info.update(status=206, start=span[0], end=span[1])
        if size == 0:
            return info, iter(())
        core = self._core()
        try:
            stream = iter(core.stream(o.id, info["start"], info["end"], read_ahead=True))  # и для 200: через кэш чанков, без полного хеширования
            first = next(stream, b"")
        except StorageBusy:
            raise AdminError(503, "Слишком много одновременных скачиваний, повторите позже") from None
        except ObjectNotFound:
            raise AdminError(404, "Объект не найден") from None
        except IntegrityError:
            logger.error("Storage: проверка целостности не пройдена при воспроизведении")
            raise AdminError(502, "Файл в Storage повреждён") from None
        return info, itertools.chain([first], stream)

    # ─── клиентская нарезка: multipart для Mini App ───

    _CLIENT_MAX_PARALLEL = 4

    @staticmethod
    def _multipart_error(exc: MultipartError) -> AdminError:
        return AdminError(404 if exc.code == "NoSuchUpload" else 422, str(exc))

    def multipart_start(self, bucket: str, key: Any, content_type: Any = None) -> dict:
        """Начать multipart-загрузку. Режет файл на части браузер; размер части задаёт сервер: ровно один чанк
        (одно сообщение Telegram). Число параллельных частей не больше слотов загрузки Storage (и 4)."""
        self._visible_bucket(bucket)
        key = self._clean_path(key, "key", folder=False)
        core = self._core()
        mime = content_type.strip() if isinstance(content_type, str) and content_type.strip() else "application/octet-stream"
        try:
            upload = core.multipart.create(bucket, key, mime_type=mime)
        except InvalidName as exc:
            raise AdminError(422, "Проверьте значения", {"key": str(exc)}) from None
        parallel = max(1, min(self._CLIENT_MAX_PARALLEL, int(self.settings.storage_max_concurrent_uploads)))
        logger.info("Storage: начата клиентская multipart-загрузка %s/%s (%s)", bucket, key, upload.id)
        return {"upload_id": upload.id, "part_size": max(core.chunk_size, core.multipart.min_part_size),
                "max_parallel": parallel, "max_parts": MAX_PARTS}

    def multipart_part(self, upload_id: str, part_number: int, data: Any) -> dict:
        if not _UPLOAD_ID_RE.match(upload_id or ""):
            raise AdminError(404, "Загрузка не найдена")
        try:
            session = self._core().multipart.begin_part(upload_id, part_number)
            with session:
                view = memoryview(data)
                for pos in range(0, len(view), 1 << 20):
                    session.write(view[pos:pos + (1 << 20)])
                obj = session.complete()
        except MultipartError as exc:
            raise self._multipart_error(exc) from None
        except StorageBusy:
            raise AdminError(503, "Слишком много одновременных загрузок, повторите позже") from None
        return {"part_number": part_number, "etag": obj.etag, "size": obj.size_bytes}

    def multipart_finish(self, upload_id: str, parts: Any) -> dict:
        if not _UPLOAD_ID_RE.match(upload_id or ""):
            raise AdminError(404, "Загрузка не найдена")
        try:
            requested = [(int(p["part_number"]), str(p["etag"])) for p in parts]
            if not requested:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise AdminError(422, "Проверьте значения", {"parts": "ожидается непустой список [{part_number, etag}]"}) from None
        try:
            obj = self._core().multipart.complete(upload_id, requested)
        except MultipartError as exc:
            raise self._multipart_error(exc) from None
        except StorageBusy:
            raise AdminError(503, "Слишком много одновременных операций, повторите позже") from None
        logger.info("Storage: собран объект %s/%s из %d частей", obj.bucket, obj.object_key, len(requested))
        return self._object_brief(obj)

    # ─── файловый менеджер: папки = общие префиксы ключей ───

    _LIST_PAGE = 500

    @staticmethod
    def _clean_path(value: Any, field: str, *, folder: bool) -> str:
        """Ключ/префикс из интерфейса: без ведущего «/», «//», сегментов «.»/«..» и управляющих символов."""
        if not isinstance(value, str) or not value.strip():
            raise AdminError(422, "Проверьте значения", {field: "укажите путь"})
        if value.startswith("/") or "//" in value or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise AdminError(422, "Проверьте значения", {field: "путь без ведущего «/», «//» и управляющих символов"})
        if folder and not value.endswith("/"):
            value += "/"
        if not folder and value.endswith("/"):
            raise AdminError(422, "Проверьте значения", {field: "имя файла не может оканчиваться на «/»"})
        if any(part in (".", "..") for part in value.split("/")):
            raise AdminError(422, "Проверьте значения", {field: "сегменты «.» и «..» запрещены"})
        if len(value.encode("utf-8")) > 1024:
            raise AdminError(422, "Проверьте значения", {field: "путь длиннее 1024 байт"})
        return value

    def bucket_browse(self, name: str, prefix: str = "", cursor: str = "", limit: int = 200) -> dict:
        """Содержимое «папки» prefix: подпапки (общие префиксы по «/») и файлы. Маркер самой папки скрыт.
        cursor — значение next_cursor предыдущего ответа (ключ или префикс, с которого продолжать)."""
        self._visible_bucket(name)
        prefix = self._clean_path(prefix, "prefix", folder=True) if prefix else ""
        limit = max(1, min(int(limit), 500))
        core = self._core()
        folders: list[dict] = []
        objects: list[dict] = []
        next_cursor: str | None = None
        from_key = cursor or ""
        done = False
        while not done:
            batch = core.list_objects(name, prefix=prefix, from_key=from_key, limit=self._LIST_PAGE)
            if not batch:
                break
            jumped = False
            for obj in batch:
                key = obj.object_key
                rest = key[len(prefix):]
                if not rest:
                    continue  # маркер текущей папки
                slash = rest.find("/")
                if slash >= 0:
                    folder = prefix + rest[:slash + 1]
                    if len(folders) + len(objects) >= limit:
                        next_cursor, done = folder, True
                        break
                    folders.append({"name": rest[:slash + 1], "prefix": folder})
                    from_key = folder[:-1] + "0"  # «0» идёт сразу после «/»: перепрыгиваем всё содержимое папки
                    jumped = True
                    break
                if len(folders) + len(objects) >= limit:
                    next_cursor, done = key, True
                    break
                objects.append({**self._object_brief(obj), "name": rest})
            if done:
                break
            if not jumped:
                if len(batch) < self._LIST_PAGE:
                    break
                from_key = batch[-1].object_key + "\0"
        return {"bucket": name, "prefix": prefix, "folders": folders, "objects": objects, "next_cursor": next_cursor}

    def folder_create(self, bucket: str, prefix: Any) -> dict:
        self._visible_bucket(bucket)
        prefix = self._clean_path(prefix, "prefix", folder=True)
        try:
            self._core().put_object(bucket, prefix, b"", filename=prefix.rstrip("/").rsplit("/", 1)[-1],
                                    mime_type="application/x-directory")
        except InvalidName as exc:
            raise AdminError(422, "Проверьте значения", {"prefix": str(exc)}) from None
        except StorageBusy:
            raise AdminError(503, "Слишком много одновременных операций, повторите позже") from None
        logger.info("Storage: создана папка %s/%s", bucket, prefix)
        return {"prefix": prefix}

    def folder_delete(self, bucket: str, prefix: Any) -> dict:
        """Удаляет все объекты с префиксом prefix (и маркер папки). Корень bucket-а так не очищается."""
        self._visible_bucket(bucket)
        prefix = self._clean_path(prefix, "prefix", folder=True)
        core = self._core()
        deleted = failed = 0
        from_key = ""
        while True:
            batch = core.list_objects(bucket, prefix=prefix, from_key=from_key, limit=self._LIST_PAGE)
            if not batch:
                break
            for obj in batch:
                try:
                    core.delete_object(obj.id)
                    deleted += 1
                except ObjectNotFound:
                    pass
                except (ObjectConflict, StorageError):
                    failed += 1
            if len(batch) < self._LIST_PAGE:
                break
            from_key = batch[-1].object_key + "\0"
        logger.info("Storage: удалена папка %s/%s (объектов: %d, ошибок: %d)", bucket, prefix, deleted, failed)
        return {"deleted": deleted, "failed": failed}

    def object_upload(self, bucket: str, key: Any, source: Any, mime_type: str | None = None) -> dict:
        self._visible_bucket(bucket)
        key = self._clean_path(key, "key", folder=False)
        try:
            obj = self._core().put_object(bucket, key, source, mime_type=mime_type)
        except InvalidName as exc:
            raise AdminError(422, "Проверьте значения", {"key": str(exc)}) from None
        except StorageBusy:
            raise AdminError(503, "Слишком много одновременных загрузок, повторите позже") from None
        except ObjectConflict as exc:
            raise AdminError(409, str(exc)) from None
        logger.info("Storage: загружен объект %s/%s (%d байт)", bucket, key, obj.size_bytes or 0)
        return self._object_brief(obj)

    # ─── ключи доступа ───

    @staticmethod
    def _key_dict(k: AccessKey) -> dict:
        return {"id": k.id, "name": k.name, "description": k.description, "access_key_id": k.access_key_id,
                "source": "database", "permissions": list(k.permissions), "all_buckets": k.all_buckets,
                "buckets": list(k.buckets), "status": "active" if k.status == "active" else "revoked",
                "created_at": k.created_at, "rotated_at": k.rotated_at, "revoked_at": k.revoked_at,
                "last_used_at": k.last_used_at, "needs_rotation": k.status == "active" and not k.master_ok}

    def credentials_list(self) -> dict:
        items = [self._key_dict(k) for k in self._store().list()]
        app = self._get_s3()
        config_keys = [{"id": f"config:{i}", "name": "Ключ из .env", "description": "Задан в конфигурации Polaris",
                        "access_key_id": key_id, "source": "config", "permissions": list(PERMISSIONS),
                        "all_buckets": True, "buckets": [], "status": "active", "created_at": None, "rotated_at": None,
                        "revoked_at": None, "last_used_at": None, "needs_rotation": False, "managed_in_env": True}
                       for i, key_id in enumerate(app.static_key_ids if app is not None else [])]
        return {"credentials": config_keys + items, "permissions": list(PERMISSIONS)}

    def _guard_config_key(self, credential_id: str) -> None:
        if credential_id.startswith("config:"):
            raise AdminError(409, "Этот ключ задан в .env (S3_ACCESS_KEY_ID / S3_CREDENTIALS): "
                                  "меняйте его в конфигурации и перезапускайте Polaris")

    def _key_call(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except AccessKeyError as exc:
            if exc.field:
                raise AdminError(422, "Проверьте значения", {exc.field: str(exc)}) from None
            raise AdminError(exc.status, str(exc)) from None

    def credential_create(self, body: dict) -> dict:
        if not isinstance(body, dict):
            raise AdminError(422, "Ожидается объект")
        errors: dict[str, str] = {}
        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            errors["name"] = "Название: обязательное поле"
        perms = body.get("permissions")
        if not isinstance(perms, list) or not perms:
            errors["permissions"] = "выберите хотя бы одно право"
        buckets_arg: list[str] | None = None
        if body.get("all_buckets") is not True:
            buckets = body.get("buckets")
            if not isinstance(buckets, list) or not buckets or not all(isinstance(b, str) for b in buckets):
                errors["buckets"] = "выберите buckets или разрешите доступ ко всем"
            else:
                known = {b.name for b in self._core().list_buckets()}
                missing = [b for b in buckets if b not in known]
                if missing:
                    errors["buckets"] = f"bucket не найден: {', '.join(missing)}"
                buckets_arg = buckets
        if errors:
            raise AdminError(422, "Проверьте значения", errors)
        key, secret = self._key_call(self._store().create, name, body.get("description", ""), perms, buckets_arg)
        return {"credential": self._key_dict(key), "access_key_id": key.access_key_id, "secret_access_key": secret}

    def credential_revoke(self, credential_id: str) -> dict:
        self._guard_config_key(credential_id)
        return {"credential": self._key_dict(self._key_call(self._store().revoke, credential_id))}

    def credential_rotate(self, credential_id: str) -> dict:
        self._guard_config_key(credential_id)
        key, secret = self._key_call(self._store().rotate, credential_id)
        return {"credential": self._key_dict(key), "access_key_id": key.access_key_id, "secret_access_key": secret}

    def credential_update(self, credential_id: str, body: dict) -> dict:
        self._guard_config_key(credential_id)
        if not isinstance(body, dict) or not ({"name", "description"} & body.keys()):
            raise AdminError(422, "Можно изменить название и описание")
        key = self._key_call(self._store().update, credential_id, name=body.get("name"), description=body.get("description"))
        return {"credential": self._key_dict(key)}

    def credential_delete(self, credential_id: str) -> dict:
        self._guard_config_key(credential_id)
        self._key_call(self._store().delete, credential_id)
        return {}

    # ─── multipart ───

    def multipart_list(self) -> dict:
        now = datetime.now(timezone.utc)
        rows = self._core().multipart.list_all()
        for row in rows:
            created = datetime.strptime(row["created_at"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
            row["age_seconds"] = int((now - created).total_seconds())
        return {"uploads": rows}

    def multipart_abort(self, upload_id: str) -> dict:
        if not _UPLOAD_ID_RE.match(upload_id or ""):
            raise AdminError(404, "Загрузка не найдена")
        try:
            self._core().multipart.abort(upload_id)
        except MultipartError:
            raise AdminError(404, "Загрузка не найдена или уже завершена") from None
        logger.info("Storage: администратор отменил multipart-загрузку %s", upload_id)
        return {}

    # ─── тесты хранилища ───

    def _telegram_for_test(self, use_saved: bool):
        """Драйвер для проверки. Если в .env нет неприменённых изменений Telegram — проверяется действующий драйвер
        Storage (с его прокси и таймаутами); иначе — драйвер, собранный из сохранённой, но ещё не применённой конфигурации."""
        service = self._service()
        pending = any(f["pending_restart"] for f in self._fields_view() if f["group"] == "telegram")
        if service is not None and service.state == "ready" and not (use_saved and pending):
            return service.get_core().driver, "действующая конфигурация"
        env = lambda key, attr: (read_env_value(self.env_path, key) if use_saved else None) or getattr(self.settings, attr)  # noqa: E731
        token = env("STORAGE_TELEGRAM_BOT_TOKEN", "storage_telegram_bot_token") or \
            env("TELEGRAM_BOT_TOKEN", "telegram_bot_token")
        chat = env("CLOUD_STORAGE_CHAT_ID", "cloud_storage_chat_id")
        if not token or not chat:
            missing = [n for n, v in (("токен бота", token), ("Chat ID", chat)) if not v]
            raise AdminError(422, "Telegram не настроен: не задан " + " и ".join(missing))
        s = self.settings
        driver = TelegramDriver(TelegramConfig(
            token=token, chat_id=chat, api_base=s.telegram_api_base, proxy_url=s.telegram_proxy_url,
            force_ipv4=s.telegram_force_ipv4, connect_timeout=s.storage_connect_timeout, read_timeout=s.storage_read_timeout,
            max_retries=0))
        return driver, "сохранённая конфигурация" + (" (ещё не применена — нужен перезапуск)" if pending else "")

    def run_tests(self, steps: list[str] | None = None, use_saved: bool = True) -> dict:
        requested = list(steps or TEST_STEPS)
        unknown = [s for s in requested if s not in TEST_STEPS]
        if unknown:
            raise AdminError(422, f"неизвестные шаги: {', '.join(unknown)}")
        wanted = {s for s in requested}
        for step in list(wanted):
            wanted.update(_REQUIRES.get(step, ()))
        order = [s for s in TEST_STEPS if s in wanted]
        if not self._test_lock.acquire(blocking=False):
            raise AdminError(409, "Тест уже выполняется")
        try:
            results = self._run_steps(order, use_saved)
        finally:
            self._test_lock.release()
        outcome = {"ok": all(r["status"] in ("ok", "skipped") for r in results), "finished_at": _now(), "steps": results}
        self.last_tests = outcome
        return outcome

    def _run_steps(self, order: list[str], use_saved: bool) -> list[dict]:
        results: list[dict] = []
        ctx: dict[str, Any] = {}
        service = self._service()
        ready = service is not None and service.state == "ready"
        try:
            if ready:
                self._cleanup_selftest_leftovers()
            for step in order:
                started = time.monotonic()
                try:
                    status, message = getattr(self, f"_step_{step}")(ctx, ready, use_saved)
                except AdminError as exc:
                    status, message = "error", exc.detail
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Storage: шаг теста %s упал", step)
                    status, message = "error", f"{type(exc).__name__}"
                results.append({"step": step, "label": _STEP_LABELS[step], "status": status, "message": message,
                                "duration_ms": int((time.monotonic() - started) * 1000)})
        finally:
            ctx_cleanup = self._cleanup_object(ctx)
            if ctx_cleanup:
                results.append({"step": "cleanup", "label": "Очистка тестовых данных", "status": "error",
                                "message": ctx_cleanup, "duration_ms": 0})
        return results

    def _skip_unless_ready(self, ready: bool):
        if not ready:
            return "skipped", "Storage недоступен — шаг пропущен"
        return None

    def _step_config(self, ctx, ready, use_saved):
        problems = []
        service = self._service()
        if service is not None:
            problems += service.config.validate() + service.config.backend_problems()
        else:
            from polaris.storage.config import StorageConfig

            cfg = StorageConfig.from_settings(self.settings)
            problems += cfg.validate() + cfg.backend_problems()
        from polaris.s3.config import S3Config

        if self.settings.s3_enabled:
            problems += S3Config.from_settings(self.settings).validate()
        if problems:
            return "error", "; ".join(problems)
        return "ok", "Конфигурация корректна"

    def _step_telegram(self, ctx, ready, use_saved):
        driver, source = self._telegram_for_test(use_saved)
        health = driver.health_check()
        if not health.ok:
            return "error", f"{health.message} — {source}"
        suffix = f"; {health.details['warning']}" if health.details.get("warning") else ""
        return "ok", f"Бот @{health.details.get('bot', '?')} отвечает, чат доступен — {source}{suffix}"

    def _step_write(self, ctx, ready, use_saved):
        skipped = self._skip_unless_ready(ready)
        if skipped:
            return skipped
        core = self._core()
        core.ensure_bucket(SELFTEST_BUCKET)
        data = os.urandom(64 * 1024)
        ctx["data"], ctx["sha"] = data, hashlib.sha256(data).hexdigest()
        obj = core.put_object(SELFTEST_BUCKET, f"selftest/{uuid.uuid4().hex}", data, mime_type="application/octet-stream")
        ctx["object_id"] = obj.id
        return "ok", f"Записано {len(data)} байт"

    def _step_read(self, ctx, ready, use_saved):
        if "object_id" not in ctx:
            return "skipped", "Нет тестового объекта (запись не удалась)"
        ctx["read"] = b"".join(self._core().stream(ctx["object_id"]))
        return "ok", f"Прочитано {len(ctx['read'])} байт"

    def _step_hash(self, ctx, ready, use_saved):
        if "read" not in ctx:
            return "skipped", "Нет прочитанных данных"
        digest = hashlib.sha256(ctx["read"]).hexdigest()
        if digest != ctx["sha"]:
            return "error", "SHA-256 после скачивания не совпал с записанным"
        return "ok", "SHA-256 совпал"

    def _step_delete(self, ctx, ready, use_saved):
        if "object_id" not in ctx:
            return "skipped", "Нет тестового объекта"
        object_id = ctx.pop("object_id")
        if self._core().delete_object(object_id):
            return "ok", "Объект удалён физически"
        return "error", "Объект удалён логически, но backend не подтвердил физическое удаление (см. Recovery)"

    def _cleanup_object(self, ctx: dict) -> str:
        """Гарантированная очистка: тестовый объект удаляется даже если шаги упали."""
        object_id = ctx.pop("object_id", None)
        if not object_id:
            return ""
        try:
            self._core().delete_object(object_id)
        except ObjectNotFound:
            return ""
        except Exception as exc:  # noqa: BLE001
            return f"не удалось удалить тестовый объект: {type(exc).__name__}"
        return ""

    def _cleanup_selftest_leftovers(self) -> None:
        """Убрать остатки прерванных тестов: служебные ключи, временные bucket-ы и объекты (только наши префиксы)."""
        core, service = self._core(), self._service()
        if service.access_keys is not None:
            service.access_keys.purge_system_keys()
        for bucket in core.list_buckets(include_internal=True):
            if bucket.name == SELFTEST_BUCKET or bucket.name.startswith(storage_core.SELFTEST_BUCKET_PREFIX):
                for obj in core.list_objects(bucket.name, limit=10_000):
                    try:
                        core.delete_object(obj.id)
                    except StorageError:
                        pass
                if bucket.name != SELFTEST_BUCKET:
                    try:
                        core.delete_bucket(bucket.name)
                    except StorageError:
                        pass

    def _step_s3(self, ctx, ready, use_saved):
        skipped = self._skip_unless_ready(ready)
        if skipped:
            return skipped
        app = self._get_s3()
        if app is None:
            return "skipped", "S3 API выключен или не запущен"
        store = self._store()
        core = self._core()
        bucket = f"{storage_core.SELFTEST_BUCKET_PREFIX}{uuid.uuid4().hex[:10]}"
        key_row = None
        try:
            core.ensure_bucket(bucket)
            key_row, secret = store.create("Polaris self-test", "временный ключ самопроверки", list(PERMISSIONS),
                                           [bucket], system=True)
            return self._s3_cycle(app, store, key_row, secret, bucket)
        finally:
            if key_row is not None:
                try:
                    store.revoke(key_row.id)
                    store.delete(key_row.id)
                except Exception:  # noqa: BLE001
                    logger.exception("Storage: не удалось удалить временный ключ самотеста")
            self._cleanup_selftest_leftovers()

    def _s3_cycle(self, app, store, key_row, secret: str, bucket: str):
        from polaris.s3.signer import sign_headers
        from polaris.s3.util import aws_quote

        region, prefix, host = app.config.region, app.config.path_prefix, "polaris-selftest.local"
        data = os.urandom(32 * 1024)
        path = f"{prefix}/{bucket}/selftest/{uuid.uuid4().hex}.bin"

        def call(method, raw_path, query="", body=b"", headers=None, use_secret=secret, key_id=key_row.access_key_id):
            hdrs = sign_headers(use_secret, key_id, region, method, raw_path, query, {"host": host, **(headers or {})}, body)
            return asyncio.run(app.local_request(method, raw_path, query, hdrs, body))

        steps = []

        def check(name, condition, detail=""):
            steps.append((name, bool(condition), detail))
            return bool(condition)

        status, headers, _ = call("PUT", path, body=data)
        if check("PUT", status == 200 and headers.get("etag") == f'"{hashlib.md5(data).hexdigest()}"', f"HTTP {status}"):
            status, _, body = call("GET", path)
            check("GET + SHA-256", status == 200 and hashlib.sha256(body).digest() == hashlib.sha256(data).digest(), f"HTTP {status}")
            status, headers, _ = call("HEAD", path)
            check("HEAD", status == 200 and headers.get("content-length") == str(len(data)), f"HTTP {status}")
            status, _, body = call("GET", f"{prefix}/{bucket}", "list-type=2&prefix=selftest%2F")
            check("List", status == 200 and path.rsplit("/", 1)[1].encode() in body, f"HTTP {status}")
            status, _, body = call("GET", path, headers={"range": "bytes=10-19"})
            check("Range", status == 206 and body == data[10:20], f"HTTP {status}")
            status, _, _ = call("DELETE", path)
            check("DELETE", status == 204, f"HTTP {status}")
            status, _, _ = call("GET", path)
            check("GET после DELETE → 404", status == 404, f"HTTP {status}")
        status, _, body = call("GET", f"{prefix}/{bucket}", "list-type=2", use_secret=secret + "x")
        check("неверная подпись отклоняется", status == 403 and b"SignatureDoesNotMatch" in body, f"HTTP {status}")
        status, _, _ = call("GET", f"{prefix}/other-bucket-{uuid.uuid4().hex[:6]}", "list-type=2")
        check("доступ вне разрешённых buckets запрещён", status == 403, f"HTTP {status}")
        store.revoke(key_row.id)
        status, _, body = call("GET", f"{prefix}/{bucket}", "list-type=2")
        check("отозванный ключ отклоняется", status == 403 and b"InvalidAccessKeyId" in body, f"HTTP {status}")
        failed = [f"{n} ({d})" for n, ok, d in steps if not ok]
        if failed:
            return "error", "Не прошло: " + "; ".join(failed)
        return "ok", f"Пройдено {len(steps)} проверок: " + ", ".join(n for n, _, _ in steps)
