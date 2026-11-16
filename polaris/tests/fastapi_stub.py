"""Минимальная имитация FastAPI для окружений без fastapi/pydantic: позволяет импортировать роутеры Polaris и
вызывать их обработчики с разрешением Depends/Header/Query/Body так же, как это делает FastAPI.
Если настоящий fastapi установлен — ничего не подменяется (install() возвращает False)."""
from __future__ import annotations

import inspect
import re
import sys
import types
from typing import Any


class HTTPException(Exception):
    def __init__(self, status_code: int, detail: Any = None, headers: dict | None = None) -> None:
        super().__init__(str(detail))
        self.status_code, self.detail, self.headers = status_code, detail, headers


class _Param:
    def __init__(self, kind: str, default: Any = None, alias: str | None = None, ge=None, le=None) -> None:
        self.kind, self.default, self.alias, self.ge, self.le = kind, default, alias, ge, le


class _Depends:
    def __init__(self, dependency) -> None:
        self.dependency = dependency


def Header(default=None, alias=None, **_):  # noqa: N802
    return _Param("header", default, alias)


def Query(default=None, ge=None, le=None, **_):  # noqa: N802
    return _Param("query", default, None, ge, le)


def Body(default=None, **_):  # noqa: N802
    return _Param("body", default)


def Depends(dependency):  # noqa: N802
    return _Depends(dependency)


class Response:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}


class StreamingResponse:
    def __init__(self, content, media_type=None, headers=None, status_code=200) -> None:
        self.body_iterator, self.media_type, self.headers, self.status_code = content, media_type, headers or {}, status_code


class Request:
    """Заглушка starlette.Request: заголовки и асинхронный поток тела."""

    def __init__(self, headers: dict | None = None, raw: bytes = b"", chunk: int = 64 * 1024) -> None:
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self._raw, self._chunk = raw, chunk

    async def stream(self):
        for pos in range(0, len(self._raw), self._chunk):
            yield self._raw[pos:pos + self._chunk]


async def run_in_threadpool(fn, *args, **kwargs):
    return fn(*args, **kwargs)


class APIRouter:
    def __init__(self, prefix: str = "", tags=None, **_) -> None:
        self.prefix, self.routes = prefix, []

    def _register(self, method: str, path: str):
        def decorator(fn):
            self.routes.append((method, self.prefix + path, fn))
            return fn
        return decorator

    def get(self, path, **_): return self._register("GET", path)
    def post(self, path, **_): return self._register("POST", path)
    def put(self, path, **_): return self._register("PUT", path)
    def patch(self, path, **_): return self._register("PATCH", path)
    def delete(self, path, **_): return self._register("DELETE", path)


def _stub_modules():
    module = types.ModuleType("fastapi")
    for name in ("HTTPException", "Header", "Query", "Body", "Depends", "Response", "APIRouter", "Request"):
        setattr(module, name, globals()[name])
    responses = types.ModuleType("fastapi.responses")
    responses.StreamingResponse = StreamingResponse
    concurrency = types.ModuleType("fastapi.concurrency")
    concurrency.run_in_threadpool = run_in_threadpool
    module.responses, module.concurrency = responses, concurrency
    return module, responses, concurrency


def install() -> bool:
    """Подменить fastapi заглушкой, только если настоящего нет."""
    try:
        import fastapi  # noqa: F401
        return False
    except ImportError:
        pass
    sys.modules["fastapi"], sys.modules["fastapi.responses"], sys.modules["fastapi.concurrency"] = _stub_modules()
    return True


_loaded = None


def load_stubbed():
    """(polaris.shared.auth, polaris.modules.storage.api), импортированные ПОВЕРХ заглушки — даже если настоящий
    FastAPI установлен. Исходные модули в sys.modules восстанавливаются, остальной код не затрагивается."""
    global _loaded
    if _loaded is not None:
        return _loaded
    import importlib

    names = ("fastapi", "fastapi.responses", "fastapi.concurrency", "polaris.shared.auth", "polaris.modules.storage.api")
    saved = {n: sys.modules.get(n) for n in names}
    sys.modules["fastapi"], sys.modules["fastapi.responses"], sys.modules["fastapi.concurrency"] = _stub_modules()
    for n in names[3:]:
        sys.modules.pop(n, None)
    try:
        auth = importlib.import_module("polaris.shared.auth")
        api = importlib.import_module("polaris.modules.storage.api")
    finally:
        for n, module in saved.items():
            if module is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = module
    _loaded = (auth, api)
    return _loaded


def _resolve(fn, ctx: dict, depth: int = 0):
    kwargs = {}
    for name, p in inspect.signature(fn).parameters.items():
        d = p.default
        if isinstance(d, _Depends):
            kwargs[name] = _resolve(d.dependency, ctx, depth + 1)
        elif isinstance(d, _Param):
            if d.kind == "header":
                lowered = {k.lower(): v for k, v in ctx["headers"].items()}
                kwargs[name] = lowered.get((d.alias or name).lower(), d.default)
            elif d.kind == "query":
                value = ctx["query"].get(name, d.default)
                if isinstance(value, str) and str(p.annotation) == "int":
                    value = int(value)
                if isinstance(value, int) and ((d.ge is not None and value < d.ge) or (d.le is not None and value > d.le)):
                    raise HTTPException(422, f"{name} вне диапазона")
                kwargs[name] = value
            else:
                kwargs[name] = ctx["body"] if ctx["body"] is not None else d.default
        elif name in ctx["path"]:
            kwargs[name] = ctx["path"][name]
        elif name == "response":
            kwargs[name] = ctx["response"]
        elif name == "request":
            kwargs[name] = Request(ctx["headers"], ctx["raw"])
    return fn(**kwargs)


def call(router, method: str, path: str, *, headers: dict | None = None, query: dict | None = None, body: Any = None,
         raw: bytes = b""):
    """Вызвать маршрут как FastAPI. Возвращает (status, payload, response_headers)."""
    for route_method, pattern, fn in router.routes:
        if route_method != method:
            continue
        match = re.fullmatch(re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern), path)
        if match:
            response = Response()
            ctx = {"headers": headers or {}, "query": query or {}, "body": body, "path": match.groupdict(),
                   "response": response, "raw": raw}
            try:
                result = _resolve(fn, ctx)
                if inspect.iscoroutine(result):  # async-обработчики (например, загрузка файла)
                    import asyncio
                    result = asyncio.run(result)
                return 200, result, response.headers
            except HTTPException as exc:
                return exc.status_code, exc.detail, response.headers
    raise LookupError(f"нет маршрута {method} {path}")
