"""Окружение UI-тестов: реальные статические файлы Mini App + реальные маршруты /api/storage/* (через fastapi_stub
или настоящий FastAPI) + реальное S3-приложение на тестовом ASGI-сервере; браузер — Playwright/Chromium."""
from __future__ import annotations

import asyncio
import json
import mimetypes
import os
from pathlib import Path

from tests import fastapi_stub

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
TOKEN = "ui-test-token"


def playwright_available() -> tuple[bool, str]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False, "playwright не установлен"
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers" if Path("/opt/pw-browsers").exists() else "")
    if not os.environ["PLAYWRIGHT_BROWSERS_PATH"]:
        os.environ.pop("PLAYWRIGHT_BROWSERS_PATH")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
    except Exception as exc:  # noqa: BLE001
        return False, f"Chromium недоступен: {type(exc).__name__}"
    return True, ""


class UiApp:
    """ASGI-приложение: статика Mini App, /api/me, /api/storage/* (реальный роутер), /s3 (реальное S3-приложение)."""

    def __init__(self, router, s3_app) -> None:
        self.router, self.s3_app = router, s3_app
        self.restart_calls = 0
        self.storage_requests: list[tuple[str, str]] = []
        self.index = (FRONTEND / "index.html").read_text(encoding="utf-8").replace("{{ VERSION }}", "ui-test")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return
        path = scope["path"]
        if path == "/s3" or path.startswith("/s3/"):
            return await self.s3_app(scope, receive, send)
        method = scope["method"]
        headers = {k.decode().lower(): v.decode("latin-1") for k, v in scope["headers"]}
        if method == "GET" and path == "/":
            return await self._respond(send, 200, self.index.encode(), "text/html; charset=utf-8")
        if method == "GET" and path.startswith("/assets/"):
            file = (FRONTEND / path.lstrip("/")).resolve()
            if FRONTEND.resolve() in file.parents and file.is_file():
                return await self._respond(send, 200, file.read_bytes(), mimetypes.guess_type(file.name)[0] or "application/octet-stream")
            return await self._json(send, 404, {"detail": "not found"})
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        if path == "/api/me":
            if headers.get("x-polaris-token") != TOKEN:
                return await self._json(send, 401, {"detail": "нужен токен"})
            return await self._json(send, 200, {"success": True, "data": {"auth": "browser"}})
        if path == "/api/restart" and method == "POST":
            if headers.get("x-polaris-token") != TOKEN:
                return await self._json(send, 401, {"detail": "нужен токен"})
            self.restart_calls += 1
            return await self._json(send, 200, {"success": True})
        if path.startswith("/api/storage/"):
            self.storage_requests.append((method, path))
            query = {}
            for part in scope.get("query_string", b"").decode().split("&"):
                if part:
                    from urllib.parse import unquote_plus
                    k, _, v = part.partition("=")
                    query[unquote_plus(k)] = unquote_plus(v)
            payload = json.loads(body) if body else None
            loop = asyncio.get_running_loop()
            status, result, resp_headers = await loop.run_in_executor(
                None, lambda: fastapi_stub.call(self.router, method, path, headers=headers, query=query, body=payload))
            if hasattr(result, "body_iterator"):
                data = await loop.run_in_executor(None, lambda: b"".join(result.body_iterator))
                extra = {k: v for k, v in result.headers.items() if k.lower() != "content-length"}
                return await self._respond(send, 200, data, result.media_type, extra)
            if status == 200:
                return await self._json(send, 200, result, resp_headers)
            return await self._json(send, status, {"detail": result}, resp_headers)
        return await self._json(send, 404, {"detail": "not found"})

    async def _json(self, send, status, payload, headers=None):
        await self._respond(send, status, json.dumps(payload, ensure_ascii=False, default=str).encode(), "application/json", headers)

    async def _respond(self, send, status, body: bytes, content_type: str, headers=None):
        raw = [(b"content-type", content_type.encode()), (b"content-length", str(len(body)).encode())]
        raw += [(k.lower().encode(), v.encode("utf-8", "replace")) for k, v in (headers or {}).items()]
        await send({"type": "http.response.start", "status": status, "headers": raw})
        await send({"type": "http.response.body", "body": body})
