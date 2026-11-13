"""Общие средства тестов Storage: mock Telegram Bot API, in-memory драйвер, базовый TestCase."""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import threading
import time
import unittest
from email.parser import BytesParser
from email.policy import default as default_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from polaris.storage.backends.base import (
    BackendDriver,
    BackendMetadata,
    BackendNotFound,
    DeleteRejected,
    DeleteResult,
    HealthStatus,
    TransientBackendError,
    UploadResult,
)
from polaris.storage.core import StorageCore
from polaris.storage.database import StorageDatabase

TOKEN = "123456:SECRET-TOKEN-abcdef"
CHAT = -100123


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class MemoryDriver(BackendDriver):
    """Backend в памяти с внедрением сбоев. Доказывает, что ядро не зависит от Telegram."""

    name = "memory"
    max_chunk_size = 1024 * 1024

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.upload_count = 0
        self.deleted: list[str] = []
        self.upload_hook = None  # callable(data, ctx) -> может бросить исключение
        self.delete_errors: list[Exception] = []  # очередь: исключения для следующих delete
        self.lock = threading.Lock()
        self._n = 0

    def upload(self, data, ctx):
        if self.upload_hook:
            self.upload_hook(data, ctx)
        with self.lock:
            self._n += 1
            ref = f"mem-{self._n}"
            self.blobs[ref] = bytes(data)
            self.upload_count += 1
        return UploadResult(ref)

    def download(self, reference):
        try:
            return self.blobs[reference]
        except KeyError:
            raise BackendNotFound("нет такого чанка") from None

    def delete(self, reference):
        if self.delete_errors:
            raise self.delete_errors.pop(0)
        if reference not in self.blobs:
            return DeleteResult(already_absent=True)
        del self.blobs[reference]
        self.deleted.append(reference)
        return DeleteResult()

    def get_metadata(self, reference):
        if reference not in self.blobs:
            raise BackendNotFound("нет такого чанка")
        return BackendMetadata(len(self.blobs[reference]))

    def health_check(self):
        return HealthStatus(True, "ok")


class StorageTestCase(unittest.TestCase):
    chunk_size = 10

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="polaris-storage-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.db = StorageDatabase(self.tmp / "storage.db", busy_timeout_ms=5000)
        self.addCleanup(self.db.close)
        self.db.migrate()
        self.driver = MemoryDriver()
        self.core = self.make_core(self.driver)
        self.core.ensure_bucket("files")

    def make_core(self, driver, **kw) -> StorageCore:
        kw.setdefault("chunk_size", self.chunk_size)
        kw.setdefault("sleep", lambda s: None)
        return StorageCore(self.db, driver, **kw)

    def q(self, sql, *args):
        return self.db.connection().execute(sql, args).fetchall()


# ───────────── mock Telegram Bot API ─────────────

class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # тишина
        pass

    def _send(self, status, payload, raw=None):
        body = raw if raw is not None else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def do_POST(self):
        srv: MockTelegram = self.server.mock  # type: ignore[attr-defined]
        body = self._read_body()
        prefix = f"/bot{TOKEN}/"
        if not self.path.startswith(prefix):
            return self._send(404, {"ok": False, "error_code": 404, "description": "Not Found"})
        method = self.path[len(prefix):]
        with srv.lock:
            srv.requests.append(method)
            fault = srv.faults.get(method, []).pop(0) if srv.faults.get(method) else None
        params, files = {}, {}
        ctype = self.headers.get("Content-Type", "")
        if ctype.startswith("multipart/"):
            msg = BytesParser(policy=default_policy).parsebytes(
                b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + body)
            for part in msg.iter_parts():
                name = part.get_param("name", header="content-disposition")
                if part.get_filename():
                    files[name] = part.get_payload(decode=True)
                else:
                    params[name] = part.get_payload(decode=True).decode()
        elif body:
            params = json.loads(body)
        return srv.dispatch(self, method, params, files, fault)

    def do_GET(self):
        srv: MockTelegram = self.server.mock  # type: ignore[attr-defined]
        prefix = f"/file/bot{TOKEN}/"
        with srv.lock:
            srv.requests.append("download")
            fault = srv.faults.get("download", []).pop(0) if srv.faults.get("download") else None
        if not self.path.startswith(prefix):
            return self._send(404, {"ok": False})
        data = srv.files.get(self.path[len(prefix):])
        if data is None:
            return self._send(404, {"ok": False, "error_code": 404, "description": "Not Found"})
        if fault == "500":
            return self._send(500, {"ok": False})
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if fault == "truncate":
            self.wfile.write(data[: len(data) // 2])
            self.wfile.flush()
            self.close_connection = True
            return
        self.wfile.write(data)


class _QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):  # клиент закрыл соединение по таймауту — это штатно
        pass


class MockTelegram:
    """Эмулятор Bot API: sendDocument/getFile/скачивание/deleteMessage/getMe/getChat/getChatMember.

    faults[method] — очередь сбоев: '429:<sec>', '500', '500_after_store', 'timeout_before',
    'timeout_after_store', 'bad_json', 'drop_after_store', 'malformed_ok', 'cant_delete', 'truncate'.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.messages: dict[int, dict] = {}
        self.files: dict[str, bytes] = {}
        self.faults: dict[str, list[str]] = {}
        self.requests: list[str] = []
        self._next = 100
        self.timeout_sleep = 0.7
        self.server = _QuietServer(("127.0.0.1", 0), _Handler)
        self.server.daemon_threads = True
        self.server.mock = self  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.02), daemon=True)

    @property
    def api_base(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def count(self, method: str) -> int:
        return self.requests.count(method)

    def _store(self, params, files):
        with self.lock:
            self._next += 1
            mid = self._next
            data = files["document"]
            file_id = f"FILEID{mid}"
            self.messages[mid] = {"chat_id": params["chat_id"], "caption": params.get("caption"), "data": data,
                                  "file_id": file_id}
            self.files[f"documents/file_{mid}"] = data
            return mid, file_id, len(data)

    def dispatch(self, h, method, params, files, fault):
        ok = lambda result: h._send(200, {"ok": True, "result": result})  # noqa: E731
        if fault and fault.startswith("429"):
            return h._send(429, {"ok": False, "error_code": 429, "description": "Too Many Requests: retry after 1",
                                 "parameters": {"retry_after": int(fault.split(":")[1])}})
        if fault == "500":
            return h._send(500, {"ok": False, "error_code": 500, "description": "Internal Server Error"})
        if fault == "timeout_before":
            time.sleep(self.timeout_sleep)
            return h._send(200, {"ok": True, "result": True})
        if fault == "bad_json":
            return h._send(200, None, raw=b"<html>oops</html>")
        if method == "sendDocument":
            if "document" not in files:
                return h._send(400, {"ok": False, "error_code": 400, "description": "Bad Request: no document"})
            if fault == "perm400":
                return h._send(400, {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"})
            mid, file_id, size = self._store(params, files)
            if fault == "500_after_store":
                return h._send(500, {"ok": False, "error_code": 500, "description": "Internal"})
            if fault == "timeout_after_store":
                time.sleep(self.timeout_sleep)
            if fault == "drop_after_store":
                h.close_connection = True
                h.connection.close()
                return
            if fault == "malformed_ok":
                return ok({"message_id": mid})
            if fault == "wrong_size":
                return ok({"message_id": mid, "document": {"file_id": file_id, "file_size": size + 1}})
            return ok({"message_id": mid, "document": {"file_id": file_id, "file_unique_id": f"U{mid}",
                                                       "file_size": size}})
        if method == "getFile":
            for mid, msg in self.messages.items():
                if msg["file_id"] == params.get("file_id"):
                    return ok({"file_id": msg["file_id"], "file_size": len(msg["data"]),
                               "file_path": f"documents/file_{mid}"})
            return h._send(400, {"ok": False, "error_code": 400,
                                 "description": "Bad Request: wrong file_id or the file is temporarily unavailable"})
        if method == "deleteMessage":
            if fault == "cant_delete":
                return h._send(400, {"ok": False, "error_code": 400,
                                     "description": "Bad Request: message can't be deleted"})
            with self.lock:
                if self.messages.pop(params["message_id"], None) is None:
                    return h._send(400, {"ok": False, "error_code": 400,
                                         "description": "Bad Request: message to delete not found"})
            return ok(True)
        if method == "getMe":
            return ok({"id": 777, "is_bot": True, "username": "polaris_test_bot"})
        if method == "getChat":
            return ok({"id": params["chat_id"], "type": "supergroup"})
        if method == "getChatMember":
            return ok({"status": "administrator", "can_delete_messages": True})
        return h._send(404, {"ok": False, "error_code": 404, "description": "Not Found"})
