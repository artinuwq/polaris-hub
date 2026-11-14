"""Минимальный HTTP/1.1-сервер для ASGI-приложений (только для тестов, где нет uvicorn).

Поддерживает Content-Length и chunked-тела со стримингом, Expect: 100-continue, HEAD. Соединение
закрывается после каждого ответа. Нужен, чтобы гонять S3-приложение через настоящие сокеты."""
from __future__ import annotations

import asyncio
import threading
from urllib.parse import unquote


class AsgiTestServer:
    def __init__(self, app) -> None:
        self.app = app
        self.loop = asyncio.new_event_loop()
        self.port = 0
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> "AsgiTestServer":
        self._thread.start()
        self._ready.wait(10)
        return self

    def stop(self) -> None:
        if getattr(self, "_stopped", False):
            return
        self._stopped = True

        async def shutdown():
            self.server.close()
            await self.server.wait_closed()
        asyncio.run_coroutine_threadsafe(shutdown(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(5)

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)

        async def boot():
            self.server = await asyncio.start_server(self._client, "127.0.0.1", 0)
            self.port = self.server.sockets[0].getsockname()[1]
            self._ready.set()
        self.loop.run_until_complete(boot())
        self.loop.run_forever()

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
            writer.close()
            return
        lines = head.decode("latin-1").split("\r\n")
        method, target, _ = lines[0].split(" ", 2)
        headers = []
        for line in lines[1:]:
            if line:
                k, _, v = line.partition(":")
                headers.append((k.strip().lower().encode("latin-1"), v.strip().encode("latin-1")))
        hmap = {k.decode(): v.decode("latin-1") for k, v in headers}
        raw_path, _, query = target.partition("?")
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
                 "scheme": "http", "path": unquote(raw_path), "raw_path": raw_path.encode("latin-1"),
                 "query_string": query.encode("latin-1"), "headers": headers,
                 "server": ("127.0.0.1", self.port), "client": ("127.0.0.1", 0)}
        state = {"continue_sent": False, "done": False, "started": False, "gone": False}
        body_iter = self._body(reader, hmap)

        async def receive():
            if state["gone"]:
                return {"type": "http.disconnect"}
            if hmap.get("expect", "").lower() == "100-continue" and not state["continue_sent"]:
                state["continue_sent"] = True
                writer.write(b"HTTP/1.1 100 Continue\r\n\r\n")
                await writer.drain()
            try:
                piece = await body_iter.__anext__()
            except StopAsyncIteration:
                if state["done"]:
                    await asyncio.sleep(3600)  # после конца тела приложение ждёт disconnect
                state["done"] = True
                return {"type": "http.request", "body": b"", "more_body": False}
            except (asyncio.IncompleteReadError, ConnectionError):
                state["gone"] = True
                return {"type": "http.disconnect"}
            return {"type": "http.request", "body": piece, "more_body": True}

        async def send(message):
            if message["type"] == "http.response.start":
                state["started"] = True
                state["status"] = message["status"]
                state["resp_headers"] = message["headers"]
                state["chunked"] = not any(k == b"content-length" for k, _ in message["headers"])
                out = [f"HTTP/1.1 {message['status']} X\r\n".encode()]
                out += [k + b": " + v + b"\r\n" for k, v in message["headers"]]
                if state["chunked"] and method != "HEAD":
                    out.append(b"transfer-encoding: chunked\r\n")
                out.append(b"connection: close\r\n\r\n")
                writer.write(b"".join(out))
            else:
                body = message.get("body", b"")
                if method != "HEAD" and body:
                    if state["chunked"]:
                        writer.write(f"{len(body):x}\r\n".encode() + body + b"\r\n")
                    else:
                        writer.write(body)
                if not message.get("more_body") and state["chunked"] and method != "HEAD":
                    writer.write(b"0\r\n\r\n")
                await writer.drain()
                if not message.get("more_body"):
                    state["finished"] = True

        try:
            await self.app(scope, receive, send)
        except Exception:  # noqa: BLE001 — как uvicorn: оборвать соединение
            pass
        finally:
            try:
                await writer.drain()
            except ConnectionError:
                pass
            writer.close()

    @staticmethod
    async def _body(reader, hmap):
        if hmap.get("transfer-encoding", "").lower() == "chunked":
            while True:
                size = int((await reader.readuntil(b"\r\n")).strip().split(b";")[0], 16)
                if size == 0:
                    await reader.readuntil(b"\r\n")
                    return
                yield await reader.readexactly(size)
                await reader.readexactly(2)
        else:
            remaining = int(hmap.get("content-length", "0") or 0)
            while remaining > 0:
                piece = await reader.read(min(65536, remaining))
                if not piece:
                    raise asyncio.IncompleteReadError(b"", remaining)
                remaining -= len(piece)
                yield piece
