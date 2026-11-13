"""Telegram Backend Driver (стандартный Bot API, без Local Bot API).

Чанк хранится как документ в чате CLOUD_STORAGE_CHAT_ID. Ссылка (reference) — JSON с
chat_id/message_id/file_id: из неё же берётся chat_id при удалении, поэтому смена чата в
конфигурации не ломает старые данные.

Ограничения Bot API, которые драйвер не может скрыть:
* отправка сообщения не идемпотентна: после таймаута неизвестно, сохранилось ли сообщение;
  драйвер повторяет отправку и честно сообщает число «неопределённых» попыток;
* бот не может перечислить историю чата, поэтому осиротевшие сообщения автоматически не
  находятся — в подпись каждого документа пишется метка polaris-storage:v1 ... для ручного поиска;
* скачивание через getFile ограничено 20 MB, поэтому чанк <= 19 MiB;
* удаление зависит от прав бота в чате; отказ Telegram возвращается как DeleteRejected.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

import requests
from requests.adapters import HTTPAdapter

from polaris.storage.backends.base import (
    BackendDriver,
    BackendMetadata,
    BackendNotFound,
    DeleteRejected,
    DeleteResult,
    HealthStatus,
    PermanentBackendError,
    TransientBackendError,
    UploadContext,
    UploadResult,
)
from polaris.storage.config import MAX_CHUNK_SIZE

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]+")
_NOT_SENT_MARKERS = ("NewConnectionError", "NameResolutionError", "ConnectTimeoutError")
_BACKOFF_CAP = 60.0


@dataclass(frozen=True)
class TelegramConfig:
    token: str = field(repr=False)
    chat_id: str
    api_base: str = "https://api.telegram.org"
    proxy_url: str = field(default="", repr=False)
    force_ipv4: bool = True
    connect_timeout: float = 10.0
    read_timeout: float = 120.0
    max_retries: int = 3
    retry_delay: float = 2.0
    max_retry_after: float = 120.0


class _Transient(TransientBackendError):
    def __init__(self, message: str, *, maybe_applied: bool = False, retry_after: float = 0.0) -> None:
        super().__init__(message)
        self.maybe_applied = maybe_applied  # запрос мог быть выполнен Telegram-ом
        self.retry_after = retry_after


class _MultipartBody:
    """multipart/form-data без копирования данных чанка: head + data + tail читаются по частям."""

    def __init__(self, fields: dict[str, str], file_field: str, filename: str, data: bytes) -> None:
        boundary = uuid.uuid4().hex
        head = b"".join(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode("utf-8")
            for k, v in fields.items()
        )
        head += (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8")
        self.content_type = f"multipart/form-data; boundary={boundary}"
        self._parts = [memoryview(head), memoryview(data), memoryview(f"\r\n--{boundary}--\r\n".encode())]
        self._len = sum(len(p) for p in self._parts)
        self._i = 0
        self._off = 0

    def __len__(self) -> int:
        return self._len

    def read(self, n: int = -1) -> bytes:
        out = []
        want = n if n is not None and n >= 0 else self._len
        while want > 0 and self._i < len(self._parts):
            part = self._parts[self._i]
            take = part[self._off:self._off + want]
            out.append(bytes(take))
            want -= len(take)
            self._off += len(take)
            if self._off >= len(part):
                self._i += 1
                self._off = 0
        return b"".join(out)

    def __iter__(self):
        while True:
            block = self.read(64 * 1024)
            if not block:
                return
            yield block


class _IPv4Adapter(HTTPAdapter):
    """На VPS с кривым IPv6 обращения к Telegram зависают на десятки секунд (см. TELEGRAM_FORCE_IPV4)."""

    def init_poolmanager(self, *args, **kwargs):
        kwargs["source_address"] = ("0.0.0.0", 0)
        super().init_poolmanager(*args, **kwargs)


class TelegramDriver(BackendDriver):
    name = "telegram"

    def __init__(self, config: TelegramConfig, *, sleep: Callable[[float], None] = time.sleep) -> None:
        if not config.token or not str(config.chat_id).strip():
            raise PermanentBackendError("Telegram backend не настроен: нужны токен бота и chat id")
        self.cfg = config
        self._sleep = sleep
        self._local = threading.local()
        chat = str(config.chat_id).strip()
        self._chat: int | str = int(chat) if re.fullmatch(r"-?\d+", chat) else chat
        self._base = config.api_base.rstrip("/")

    def __repr__(self) -> str:  # токен не должен попадать в логи через repr
        return f"TelegramDriver(chat_id={self._chat!r})"

    @property
    def max_chunk_size(self) -> int:
        return MAX_CHUNK_SIZE

    # ─── HTTP ───

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            if self.cfg.proxy_url:
                session.proxies = {"http": self.cfg.proxy_url, "https": self.cfg.proxy_url}
            elif self.cfg.force_ipv4:
                adapter = _IPv4Adapter()
                session.mount("http://", adapter)
                session.mount("https://", adapter)
            self._local.session = session
        return session

    def _scrub(self, text: Any) -> str:
        text = str(text).replace(self.cfg.token, "[hidden]")
        return _TOKEN_RE.sub("bot[hidden]", text)[:300]

    @property
    def _timeout(self) -> tuple[float, float]:
        return (self.cfg.connect_timeout, self.cfg.read_timeout)

    @staticmethod
    def _definitely_not_sent(exc: requests.RequestException) -> bool:
        if isinstance(exc, requests.exceptions.ConnectTimeout):
            return True
        if isinstance(exc, requests.exceptions.ConnectionError):
            text = repr(exc)
            return any(marker in text for marker in _NOT_SENT_MARKERS)
        return False

    def _call_once(self, method: str, *, params: dict | None = None, body: _MultipartBody | None = None) -> Any:
        """Один запрос к Bot API. Возвращает result или бросает _Transient / PermanentBackendError."""
        url = f"{self._base}/bot{self.cfg.token}/{method}"
        try:
            if body is None:
                resp = self._session().post(url, json=params or {}, timeout=self._timeout)
            else:
                resp = self._session().post(
                    url, data=body, headers={"Content-Type": body.content_type}, timeout=self._timeout
                )
        except requests.RequestException as exc:
            raise _Transient(
                f"{method}: сетевая ошибка {type(exc).__name__}: {self._scrub(exc)}",
                maybe_applied=not self._definitely_not_sent(exc),
            ) from None
        try:
            status = resp.status_code
            try:
                payload = resp.json()
            except ValueError:
                payload = None
        finally:
            resp.close()
        if not isinstance(payload, dict) or "ok" not in payload:
            if status == 429 or status >= 500 or status == 200:
                raise _Transient(f"{method}: некорректный ответ Telegram (HTTP {status})", maybe_applied=True)
            raise PermanentBackendError(f"{method}: некорректный ответ Telegram (HTTP {status})", status=status)
        if payload["ok"] is True:
            if "result" not in payload:
                raise _Transient(f"{method}: ответ без result", maybe_applied=True)
            return payload["result"]
        try:
            code = int(payload.get("error_code") or status)
        except (TypeError, ValueError):
            code = status
        description = self._scrub(payload.get("description", "неизвестная ошибка"))
        parameters = payload.get("parameters") if isinstance(payload.get("parameters"), dict) else {}
        if code == 429:
            try:
                retry_after = float(parameters.get("retry_after") or 1)
            except (TypeError, ValueError):
                retry_after = 1.0
            raise _Transient(f"{method}: Telegram просит подождать {retry_after:g} с", retry_after=retry_after)
        if code >= 500:
            raise _Transient(f"{method}: ошибка Telegram {code}: {description}", maybe_applied=True)
        raise PermanentBackendError(f"{method}: {description}", status=code)

    def _retrying(self, op: str, attempt_fn: Callable[[], Any], *, effectful: bool = False) -> tuple[Any, int]:
        """Ограниченные повторы. Возвращает (результат, число неопределённых попыток)."""
        uncertain = 0
        last: _Transient | None = None
        for attempt in range(self.cfg.max_retries + 1):
            try:
                return attempt_fn(), uncertain
            except _Transient as exc:
                last = exc
                if effectful and exc.maybe_applied:
                    uncertain += 1
                if attempt >= self.cfg.max_retries:
                    break
                if exc.retry_after:
                    if exc.retry_after > self.cfg.max_retry_after:
                        break  # слишком долгий flood-wait: не блокируем поток, пусть повторит верхний уровень
                    delay = exc.retry_after + 1
                else:
                    delay = min(self.cfg.retry_delay * (2 ** attempt), _BACKOFF_CAP)
                logger.warning("Telegram %s: %s; повтор %d/%d через %.1f с", op, exc, attempt + 1,
                               self.cfg.max_retries, delay)
                self._sleep(delay)
            except PermanentBackendError as exc:
                exc.uncertain_attempts = uncertain
                raise
        assert last is not None
        final = TransientBackendError(f"{op}: попытки исчерпаны: {last}", uncertain_attempts=uncertain)
        raise final from None

    # ─── интерфейс ───

    def upload(self, data: bytes, ctx: UploadContext) -> UploadResult:
        if not 0 < len(data) <= self.max_chunk_size:
            raise PermanentBackendError(f"размер чанка {len(data)} вне допустимого диапазона")
        fields = {
            "chat_id": str(self._chat),
            "caption": ctx.marker,
            "disable_notification": "true",
            "disable_content_type_detection": "true",
        }
        filename = f"{ctx.blob_id}.{ctx.chunk_index:06d}.part"

        def attempt() -> str:
            body = _MultipartBody(fields, "document", filename, data)
            result = self._call_once("sendDocument", body=body)
            return self._reference_from_sent(result, len(data))

        reference, uncertain = self._retrying("sendDocument", attempt, effectful=True)
        return UploadResult(reference, uncertain)

    def _reference_from_sent(self, result: Any, size: int) -> str:
        message_id = result.get("message_id") if isinstance(result, dict) else None
        document = result.get("document") if isinstance(result, dict) else None
        valid = (
            isinstance(message_id, int)
            and isinstance(document, dict)
            and isinstance(document.get("file_id"), str)
            and document["file_id"]
            and document.get("file_size", size) == size
        )
        if valid:
            return json.dumps(
                {"v": 1, "chat_id": self._chat, "message_id": message_id, "file_id": document["file_id"],
                 "file_unique_id": document.get("file_unique_id", ""), "size": size},
                separators=(",", ":"),
            )
        # Telegram ответил «ок», но ссылку собрать нельзя. Если известен message_id — убираем сообщение.
        cleaned = False
        if isinstance(message_id, int):
            try:
                self._call_once("deleteMessage", params={"chat_id": self._chat, "message_id": message_id})
                cleaned = True
            except Exception:  # noqa: BLE001
                pass
        raise _Transient("sendDocument: ответ Telegram не содержит корректных данных документа",
                         maybe_applied=not cleaned)

    @staticmethod
    def _parse_reference(reference: str) -> dict:
        try:
            ref = json.loads(reference)
            if ref["v"] == 1 and isinstance(ref["message_id"], int) and isinstance(ref["file_id"], str) and "chat_id" in ref:
                return ref
        except (ValueError, KeyError, TypeError):
            pass
        raise PermanentBackendError("некорректная ссылка Telegram backend")

    def _get_file(self, ref: dict) -> dict:
        try:
            info, _ = self._retrying("getFile", lambda: self._call_once("getFile", params={"file_id": ref["file_id"]}))
        except PermanentBackendError as exc:
            if exc.status in (400, 404) and "too big" not in str(exc).lower():
                raise BackendNotFound(str(exc), status=exc.status) from None
            raise
        if not isinstance(info, dict) or not isinstance(info.get("file_path"), str):
            raise PermanentBackendError("getFile: ответ без file_path")
        return info

    def get_metadata(self, reference: str) -> BackendMetadata:
        info = self._get_file(self._parse_reference(reference))
        size = info.get("file_size")
        return BackendMetadata(size if isinstance(size, int) else None)

    def download(self, reference: str) -> bytes | bytearray:
        ref = self._parse_reference(reference)
        expected = ref.get("size") if isinstance(ref.get("size"), int) else None

        def attempt() -> bytearray:
            info = self._call_once("getFile", params={"file_id": ref["file_id"]})
            if not isinstance(info, dict) or not isinstance(info.get("file_path"), str):
                raise PermanentBackendError("getFile: ответ без file_path")
            return self._fetch_file(info["file_path"], expected)

        try:
            data, _ = self._retrying("download", attempt)
        except PermanentBackendError as exc:
            if exc.status in (400, 404) and "too big" not in str(exc).lower():
                raise BackendNotFound(str(exc), status=exc.status) from None
            raise
        return data

    def _fetch_file(self, file_path: str, expected: int | None) -> bytearray:
        url = f"{self._base}/file/bot{self.cfg.token}/{file_path}"
        limit = (expected if expected is not None else self.max_chunk_size) + 1
        try:
            resp = self._session().get(url, stream=True, timeout=self._timeout)
        except requests.RequestException as exc:
            raise _Transient(f"скачивание: сетевая ошибка {type(exc).__name__}: {self._scrub(exc)}") from None
        try:
            if resp.status_code == 404:
                raise BackendNotFound("файл недоступен на серверах Telegram", status=404)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise _Transient(f"скачивание: HTTP {resp.status_code}")
            if resp.status_code != 200:
                raise PermanentBackendError(f"скачивание: HTTP {resp.status_code}", status=resp.status_code)
            buf = bytearray()
            try:
                for block in resp.iter_content(64 * 1024):
                    buf += block
                    if len(buf) > limit:
                        raise PermanentBackendError("скачивание: размер превышает ожидаемый")
            except requests.RequestException as exc:
                raise _Transient(f"скачивание прервано: {type(exc).__name__}: {self._scrub(exc)}") from None
            declared = resp.headers.get("Content-Length")
            if declared is not None and declared.isdigit() and int(declared) != len(buf):
                raise _Transient(f"скачивание оборвано: получено {len(buf)} из {declared} байт")
            if expected is not None and len(buf) != expected:
                raise _Transient(f"размер файла {len(buf)}, ожидалось {expected}")
            return buf
        finally:
            resp.close()

    def delete(self, reference: str) -> DeleteResult:
        ref = self._parse_reference(reference)
        params = {"chat_id": ref["chat_id"], "message_id": ref["message_id"]}
        try:
            result, _ = self._retrying("deleteMessage", lambda: self._call_once("deleteMessage", params=params))
        except PermanentBackendError as exc:
            if exc.status == 400 and "message to delete not found" in str(exc).lower():
                return DeleteResult(already_absent=True)
            raise DeleteRejected(str(exc), status=exc.status) from None
        if result is not True:
            raise DeleteRejected("deleteMessage: Telegram не подтвердил удаление")
        return DeleteResult()

    def health_check(self) -> HealthStatus:
        details: dict[str, Any] = {}
        try:
            me = self._call_once("getMe")
            details["bot"] = me.get("username") if isinstance(me, dict) else None
            chat = self._call_once("getChat", params={"chat_id": self._chat})
            details["chat_type"] = chat.get("type") if isinstance(chat, dict) else None
            member = self._call_once("getChatMember", params={"chat_id": self._chat, "user_id": me["id"]})
        except Exception as exc:  # noqa: BLE001
            return HealthStatus(False, self._scrub(exc), details)
        status = member.get("status") if isinstance(member, dict) else None
        details["bot_status"] = status
        details["can_delete_messages"] = bool(
            isinstance(member, dict) and (status == "creator" or member.get("can_delete_messages"))
        )
        if status not in ("administrator", "creator", "member"):
            return HealthStatus(False, f"бот не может писать в чат (статус: {status})", details)
        if not details["can_delete_messages"]:
            details["warning"] = "у бота нет права удалять сообщения: физическое удаление может отклоняться"
        return HealthStatus(True, "ok", details)
