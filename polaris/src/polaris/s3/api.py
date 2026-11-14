"""S3 API: ASGI-приложение (HTTP-маршруты, аутентификация, потоковый ввод-вывод).

Чистый ASGI без зависимостей от FastAPI: монтируется в Polaris как подприложение. Блокирующая работа
(SQLite, Telegram) выполняется в выделенном пуле потоков, поэтому цикл событий основного Polaris не блокируется.
Тело запроса читается порциями с обратным давлением (следующая порция запрашивается только после записи
предыдущей), тело ответа отдаётся по чанкам. Секреты, query-строки и заголовки подписи не логируются.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable
from urllib.parse import quote

from polaris.s3 import errors, sigv4, util, xmlio
from polaris.s3.chunked import STREAMING_MODES, AwsChunkedDecoder
from polaris.s3.config import S3Config
from polaris.s3.credentials import CredentialProvider
from polaris.s3.service import BodyIntegrity, S3Service, decode_token, encode_token, map_exception

logger = logging.getLogger(__name__)

FLUSH_SIZE = 1024 * 1024
SEND_SLICE = 256 * 1024
_BUCKET_SUBRESOURCES = {
    "acl", "cors", "policy", "tagging", "versioning", "lifecycle", "website", "logging", "notification",
    "replication", "encryption", "accelerate", "requestPayment", "object-lock", "versions", "analytics", "metrics",
    "inventory", "ownershipControls", "publicAccessBlock", "intelligent-tiering", "policyStatus", "accessPoint",
}
_OBJECT_SUBRESOURCES = {"acl", "tagging", "attributes", "legal-hold", "retention", "torrent", "restore", "select"}
_RESPONSE_OVERRIDES = {
    "response-content-type": "Content-Type", "response-content-language": "Content-Language",
    "response-expires": "Expires", "response-cache-control": "Cache-Control",
    "response-content-disposition": "Content-Disposition", "response-content-encoding": "Content-Encoding",
}
_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")


class _ClientGone(Exception):
    pass


@dataclass
class _Req:
    method: str
    bucket: str
    key: str | None
    query: dict[str, str]
    pairs: list[tuple[str, str]]
    headers: dict[str, str]
    auth: sigv4.AuthResult
    receive: Callable
    send: Callable
    request_id: str
    scheme: str
    host: str
    resource: str
    status: int = 0

    def has(self, name: str) -> bool:
        return name in self.query


class _Body:
    def __init__(self, receive: Callable, timeout: float, limit: int) -> None:
        self._receive, self._timeout, self._limit = receive, timeout, limit
        self.received = 0

    async def chunks(self) -> AsyncIterator[bytes]:
        while True:
            try:
                message = await asyncio.wait_for(self._receive(), self._timeout)
            except asyncio.TimeoutError:
                raise errors.RequestTimeout() from None
            if message["type"] == "http.disconnect":
                raise _ClientGone()
            data = message.get("body", b"")
            if data:
                self.received += len(data)
                if self.received > self._limit:
                    raise errors.EntityTooLarge()
                yield data
            if not message.get("more_body"):
                return

    async def read_all(self) -> bytes:
        return b"".join([c async for c in self.chunks()])


class S3App:
    def __init__(self, config: S3Config, provider: CredentialProvider, service: S3Service, *,
                 max_workers: int = 12, now: Callable[[], datetime] | None = None) -> None:
        self.config = config
        self.provider = provider
        self.service = service
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="s3-worker")

    # ─── ASGI ───

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            return
        request_id = uuid.uuid4().hex[:16].upper()
        req: _Req | None = None
        try:
            req = self._parse(scope, receive, send, request_id)
            await self._dispatch(req)
        except _ClientGone:
            return
        except Exception as exc:  # noqa: BLE001
            err = map_exception(exc)
            status = err.status
            if req is not None:
                req.status = status
            await self._send_error(send, scope, err, request_id, getattr(req, "resource", scope.get("path", "")))
        finally:
            if req is not None:
                self._log(req)

    def _log(self, req: _Req) -> None:
        level = logging.WARNING if req.status >= 500 else logging.INFO if req.status >= 400 else logging.DEBUG
        logger.log(level, "S3 %s %s -> %s [%s]", req.method, req.resource, req.status or 200, req.request_id)

    async def run(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self._pool, fn, *args)

    def _parse(self, scope, receive, send, request_id: str) -> _Req:
        prefix = self.config.path_prefix
        raw = scope.get("raw_path")
        if not raw:  # сервер не передал raw_path: восстановить из path (с учётом root_path при монтировании)
            path = scope["path"]
            if not (path == prefix or path.startswith(prefix + "/")):
                path = scope.get("root_path", "") + path
            raw = quote(path).encode()
        raw_path = raw.decode("latin-1").split("?", 1)[0]
        if raw_path != prefix and not raw_path.startswith(prefix + "/"):
            raise errors.S3Error("NoSuchBucket", "The specified path does not exist.", 404)
        raw_query = scope.get("query_string", b"").decode("latin-1")
        headers = [(k.decode("latin-1").lower(), v.decode("latin-1")) for k, v in scope["headers"]]
        method = scope["method"].upper()
        sig_req = sigv4.SigRequest(method, raw_path, raw_query, headers)
        merged: dict[str, str] = {}
        for k, v in headers:
            merged[k] = f"{merged[k]},{v}" if k in merged else v
        auth = sigv4.verify(sig_req, self.provider, region=self.config.region, now=self._now(),
                            skew_seconds=self.config.clock_skew_seconds)
        path = util.decode_path(raw_path[len(prefix):])
        bucket, key = util.split_bucket_key(path)
        pairs = util.parse_query(raw_query)
        return _Req(method, bucket, key, {k: v for k, v in reversed(pairs)}, pairs, merged, auth, receive, send,
                    request_id, scope.get("scheme", "http"), merged.get("host", ""), path or "/")

    # ─── ответы ───

    @staticmethod
    def _encode_headers(headers: dict[str, str]) -> list[tuple[bytes, bytes]]:
        out = []
        for k, v in headers.items():
            try:
                value = v.encode("latin-1")
            except UnicodeEncodeError:
                value = v.encode("utf-8")
            out.append((k.lower().encode("latin-1"), value))
        return out

    def _common(self, request_id: str) -> dict[str, str]:
        return {"x-amz-request-id": request_id, "x-amz-id-2": request_id, "server": "PolarisS3"}

    async def _send_error(self, send, scope, err: errors.S3Error, request_id: str, resource: str) -> None:
        head = scope["method"].upper() == "HEAD"
        body = b"" if head else xmlio.error_xml(err, resource, request_id)
        headers = {**self._common(request_id), **err.headers, "content-type": "application/xml",
                   "content-length": str(len(body))}
        await send({"type": "http.response.start", "status": err.status, "headers": self._encode_headers(headers)})
        await send({"type": "http.response.body", "body": body})

    async def _respond(self, r: _Req, status: int, headers: dict[str, str] | None = None, body: bytes = b"", *,
                       xml: bool = False) -> None:
        r.status = status
        merged = {**self._common(r.request_id), **(headers or {})}
        if xml:
            merged["content-type"] = "application/xml"
        if "Content-Length" not in merged and "content-length" not in merged:
            merged["content-length"] = str(len(body))
        await r.send({"type": "http.response.start", "status": status, "headers": self._encode_headers(merged)})
        await r.send({"type": "http.response.body", "body": b"" if r.method == "HEAD" else body})

    # ─── маршрутизация ───

    async def _dispatch(self, r: _Req) -> None:
        m = r.method
        if not r.bucket:
            if m == "GET":
                return await self._list_buckets(r)
            raise errors.MethodNotAllowed()
        if r.key is None:
            return await self._bucket_op(r)
        return await self._object_op(r)

    async def _bucket_op(self, r: _Req) -> None:
        m, q = r.method, r.query
        sub = _BUCKET_SUBRESOURCES & q.keys()
        if m == "PUT":
            if sub:
                raise errors.NotImplementedS3(f"операция {sorted(sub)[0]} не поддерживается")
            return await self._create_bucket(r)
        if m == "HEAD":
            await self.run(self.service.head_bucket, r.bucket)
            return await self._respond(r, 200, {"x-amz-bucket-region": self.config.region})
        if m == "DELETE":
            if sub:
                raise errors.NotImplementedS3(f"операция {sorted(sub)[0]} не поддерживается")
            await self.run(self.service.delete_bucket, r.bucket)
            return await self._respond(r, 204)
        if m == "GET":
            if sub:
                raise errors.NotImplementedS3(f"операция {sorted(sub)[0]} не поддерживается")
            if r.has("location"):
                await self.run(self.service.head_bucket, r.bucket)
                return await self._respond(r, 200, body=xmlio.location_xml(self.config.region), xml=True)
            if r.has("uploads"):
                return await self._list_uploads(r)
            if q.get("list-type") == "2":
                return await self._list_objects(r, v2=True)
            if "list-type" in q:
                raise errors.InvalidArgument("поддерживается list-type=2")
            return await self._list_objects(r, v2=False)
        if m == "POST" and r.has("delete"):
            return await self._delete_objects(r)
        raise errors.MethodNotAllowed()

    async def _object_op(self, r: _Req) -> None:
        m, q = r.method, r.query
        sub = _OBJECT_SUBRESOURCES & q.keys()
        if sub:
            raise errors.NotImplementedS3(f"операция {sorted(sub)[0]} не поддерживается")
        if m == "PUT":
            if "x-amz-copy-source" in r.headers:
                raise errors.NotImplementedS3("CopyObject не поддерживается")
            if r.has("uploadId") or r.has("partNumber"):
                return await self._upload_part(r)
            return await self._put_object(r)
        if m in ("GET", "HEAD"):
            if m == "GET" and r.has("uploadId"):
                return await self._list_parts(r)
            return await self._get_object(r)
        if m == "DELETE":
            if r.has("uploadId"):
                await self.run(self.service.abort_multipart, r.bucket, r.key, q["uploadId"])
            else:
                await self.run(self.service.delete_object, r.bucket, r.key)
            return await self._respond(r, 204)
        if m == "POST":
            if r.has("uploads"):
                return await self._create_multipart(r)
            if r.has("uploadId"):
                return await self._complete_multipart(r)
        raise errors.MethodNotAllowed()

    # ─── buckets ───

    def _body(self, r: _Req, limit: int | None = None) -> _Body:
        return _Body(r.receive, self.config.body_timeout, limit if limit is not None else self.config.max_request_size)

    async def _list_buckets(self, r: _Req) -> None:
        buckets = await self.run(self.service.list_buckets)
        body = xmlio.list_buckets_xml("polaris", [(b.name, b.created_at) for b in buckets])
        await self._respond(r, 200, body=body, xml=True)

    async def _create_bucket(self, r: _Req) -> None:
        acl = r.headers.get("x-amz-acl")
        if acl is not None and acl != "private":
            raise errors.NotImplementedS3("ACL не поддерживаются: все bucket-ы приватны")
        data = await self._body(r, self.config.max_xml_body).read_all()
        location = xmlio.parse_create_bucket(data)
        await self.run(self.service.create_bucket, r.bucket, location)
        await self._respond(r, 200, {"Location": "/" + r.bucket})

    async def _delete_objects(self, r: _Req) -> None:
        data = await self._body(r, self.config.max_xml_body).read_all()
        integrity = BodyIntegrity(r.headers)
        integrity.update(data)
        integrity.verify()
        quiet, keys = xmlio.parse_delete_objects(data)
        for key in keys:
            util.validate_key(key)
        deleted, failed = await self.run(self.service.delete_objects, r.bucket, keys)
        await self._respond(r, 200, body=xmlio.delete_result_xml(deleted, failed, quiet), xml=True)

    # ─── листинги ───

    @staticmethod
    def _max_keys(value: str | None, name: str = "max-keys") -> int:
        if value is None:
            return 1000
        if not re.fullmatch(r"\d+", value):
            raise errors.InvalidArgument(f"{name} должен быть неотрицательным целым")
        return min(int(value), 1000)

    async def _list_objects(self, r: _Req, *, v2: bool) -> None:
        q = r.query
        encoding = q.get("encoding-type")
        if encoding not in (None, "url"):
            raise errors.InvalidArgument("encoding-type: поддерживается только url")
        prefix, delimiter = q.get("prefix", ""), q.get("delimiter", "")
        max_keys = self._max_keys(q.get("max-keys"))
        token = q.get("continuation-token", "") if v2 else ""
        start_after = q.get("start-after", "") if v2 else q.get("marker", "")
        if v2 and "continuation-token" in q and not token:
            raise errors.InvalidArgument("The continuation token provided is incorrect")
        if token:
            kind, value = decode_token(token)
            from_key = value + "\0" if kind == "K" else (self.service_successor(value) or "\U0010ffff\0")
        elif start_after:
            from_key = start_after + "\0"
            idx = start_after.find(delimiter, len(prefix)) if delimiter and start_after.startswith(prefix) else -1
            if idx >= 0 and idx == len(start_after) - len(delimiter):  # маркер — общий префикс: пропустить его целиком
                from_key = self.service_successor(start_after) or from_key
        else:
            from_key = ""
        res = await self.run(lambda: self.service.list_objects(
            r.bucket, prefix=prefix, delimiter=delimiter, max_keys=max_keys, from_key=from_key))
        contents = [(o.object_key, o.completed_at or o.created_at, o.etag or "", o.size_bytes) for o in res.contents]
        if v2:
            xml = xmlio.list_objects_v2_xml(xmlio.ListObjectsResult(
                r.bucket, prefix, delimiter, max_keys, encoding == "url", res.truncated, contents,
                res.common_prefixes, token, encode_token(*res.next_marker) if res.next_marker else "", start_after))
        else:
            xml = self._list_v1_xml(r, res, contents, prefix, delimiter, max_keys, encoding == "url", start_after)
        await self._respond(r, 200, body=xml, xml=True)

    @staticmethod
    def service_successor(prefix: str) -> str | None:
        from polaris.storage.repository import key_successor

        return key_successor(prefix)

    @staticmethod
    def _list_v1_xml(r, res, contents, prefix, delimiter, max_keys, enc_url, marker) -> bytes:
        import xml.etree.ElementTree as ET

        enc = util.url_encode_for_listing if enc_url else (lambda v: v)
        root = ET.Element("ListBucketResult", xmlns=xmlio.NS)
        for tag, value in (("Name", r.bucket), ("Prefix", enc(prefix)), ("Marker", enc(marker))):
            xmlio._el(root, tag, value)
        if delimiter:
            xmlio._el(root, "Delimiter", enc(delimiter))
        if res.truncated and delimiter and res.next_marker:
            xmlio._el(root, "NextMarker", enc(res.next_marker[1]))
        xmlio._el(root, "MaxKeys", max_keys)
        if enc_url:
            xmlio._el(root, "EncodingType", "url")
        xmlio._el(root, "IsTruncated", "true" if res.truncated else "false")
        for key, modified, etag, size in contents:
            c = xmlio._el(root, "Contents")
            xmlio._el(c, "Key", enc(key))
            xmlio._el(c, "LastModified", util.iso8601(modified))
            xmlio._el(c, "ETag", util.quote_etag(etag))
            xmlio._el(c, "Size", size)
            xmlio._el(c, "StorageClass", "STANDARD")
        for p in res.common_prefixes:
            xmlio._el(xmlio._el(root, "CommonPrefixes"), "Prefix", enc(p))
        return xmlio._dump(root)

    # ─── чтение объектов ───

    async def _get_object(self, r: _Req) -> None:
        overrides = {_RESPONSE_OVERRIDES[k]: v for k, v in r.query.items() if k in _RESPONSE_OVERRIDES}
        plan = await self.run(self.service.prepare_get, r.bucket, r.key, r.headers, overrides)
        if r.method == "HEAD" or plan.status == 304 or plan.length == 0:
            headers = dict(plan.headers)
            if plan.status == 304:
                return await self._respond(r, 304, headers)
            return await self._respond(r, plan.status, headers)
        stream = await self.run(self.service.open_stream, plan)
        try:
            first = await self.run(next, stream, None)  # ошибка backend до отправки заголовков -> обычный S3-ответ
            r.status = plan.status
            await r.send({"type": "http.response.start", "status": plan.status,
                          "headers": self._encode_headers({**self._common(r.request_id), **plan.headers})})
            piece = first
            while piece is not None:
                for pos in range(0, len(piece), SEND_SLICE):
                    await r.send({"type": "http.response.body", "body": piece[pos:pos + SEND_SLICE], "more_body": True})
                piece = await self.run(next, stream, None)
            await r.send({"type": "http.response.body", "body": b"", "more_body": False})
        finally:
            close = getattr(stream, "close", None)
            if close is not None:
                await self.run(close)

    # ─── запись объектов ───

    def _payload_mode(self, r: _Req) -> tuple[str | None, str | None]:
        """(expected_sha256, streaming_mode) по x-amz-content-sha256."""
        payload = r.auth.payload_hash
        if payload == sigv4.UNSIGNED_PAYLOAD:
            return None, None
        if payload in STREAMING_MODES:
            return None, payload
        if payload.startswith("STREAMING-"):
            raise errors.NotImplementedS3("этот вариант потоковой подписи не поддерживается")
        if _HEX64.match(payload):
            return payload.lower(), None
        raise errors.InvalidArgument("x-amz-content-sha256 должен быть UNSIGNED-PAYLOAD, STREAMING-* или SHA-256")

    async def _pump(self, r: _Req, session, expected_sha_mode: str | None) -> tuple[BodyIntegrity, int]:
        """Прочитать тело в сеанс загрузки. Возвращает (integrity, число байт содержимого)."""
        h = r.headers
        integrity = BodyIntegrity(h)
        decoder = None
        declared: int | None = None
        if expected_sha_mode:
            decoded = h.get("x-amz-decoded-content-length", "")
            if not decoded.isdigit():
                raise errors.MissingContentLength("отсутствует x-amz-decoded-content-length")
            declared = int(decoded)
            if declared > self.config.max_request_size:
                raise errors.EntityTooLarge()
            decoder = AwsChunkedDecoder(expected_sha_mode, r.auth if STREAMING_MODES[expected_sha_mode][0] else None)
        elif (cl := h.get("content-length", "")).isdigit():
            declared = int(cl)
        body = self._body(r)
        buf = bytearray()
        async for piece in body.chunks():
            for part in (decoder.decode(piece) if decoder else (piece,)):
                integrity.update(part)
                buf += part
                if len(buf) >= FLUSH_SIZE:
                    data = bytes(buf)
                    buf.clear()
                    await self.run(session.write, data)
        if decoder:
            decoder.finish()
        if buf:
            await self.run(session.write, bytes(buf))
        if declared is not None and integrity.size != declared:
            raise errors.IncompleteBody()
        integrity.verify(decoder.trailers if decoder else None)
        return integrity, integrity.size

    def _abort_later(self, session) -> None:
        try:
            self._pool.submit(session.abort)  # не ждём: должно сработать и при отмене задачи
        except RuntimeError:  # пул уже остановлен (завершение работы) — откатываем синхронно
            session.abort()

    async def _check_declared_size(self, r: _Req) -> None:
        for name in ("content-length", "x-amz-decoded-content-length"):
            value = r.headers.get(name, "")
            if value.isdigit() and int(value) > self.config.max_request_size:
                raise errors.EntityTooLarge()

    @staticmethod
    def _reject_conditional_write(r: _Req) -> None:
        """If-Match/If-None-Match на запись: атомарно обеспечить не можем — честная ошибка вместо тихой перезаписи."""
        if "if-none-match" in r.headers or "if-match" in r.headers:
            raise errors.NotImplementedS3("условная запись (If-Match / If-None-Match) не поддерживается")

    async def _put_object(self, r: _Req) -> None:
        self._reject_conditional_write(r)
        await self._check_declared_size(r)
        expected_sha, mode = self._payload_mode(r)
        BodyIntegrity(r.headers)  # ранняя проверка заголовков контрольных сумм
        session = await self.run(lambda: self.service.begin_put(r.bucket, r.key, r.headers, expected_sha256=expected_sha))
        try:
            await self._pump(r, session, mode)
            obj = await self.run(session.complete)
        except BaseException:
            self._abort_later(session)
            raise
        await self._respond(r, 200, {"ETag": util.quote_etag(obj.etag)})

    async def _upload_part(self, r: _Req) -> None:
        number, upload_id = r.query.get("partNumber", ""), r.query.get("uploadId", "")
        if not (r.has("partNumber") and r.has("uploadId")):
            raise errors.InvalidArgument("для UploadPart нужны uploadId и partNumber")
        if not re.fullmatch(r"\d+", number) or not 1 <= int(number) <= 10000:
            raise errors.InvalidArgument("partNumber должен быть от 1 до 10000")
        await self._check_declared_size(r)
        expected_sha, mode = self._payload_mode(r)
        BodyIntegrity(r.headers)
        session = await self.run(lambda: self.service.begin_part(
            r.bucket, r.key, upload_id, int(number), expected_sha256=expected_sha))
        try:
            await self._pump(r, session, mode)
            obj = await self.run(session.complete)
        except BaseException:
            self._abort_later(session)
            raise
        await self._respond(r, 200, {"ETag": util.quote_etag(obj.etag)})

    # ─── multipart ───

    async def _create_multipart(self, r: _Req) -> None:
        await self._body(r, self.config.max_xml_body).read_all()
        upload = await self.run(self.service.create_multipart, r.bucket, r.key, r.headers)
        await self._respond(r, 200, body=xmlio.initiate_multipart_xml(r.bucket, r.key, upload.id), xml=True)

    async def _complete_multipart(self, r: _Req) -> None:
        self._reject_conditional_write(r)
        data = await self._body(r, self.config.max_xml_body).read_all()
        parts = xmlio.parse_complete_multipart(data)
        obj = await self.run(self.service.complete_multipart, r.bucket, r.key, r.query["uploadId"], parts)
        location = f"{r.scheme}://{r.host}{self.config.path_prefix}/{r.bucket}/{util.aws_quote(r.key, keep_slash=True)}"
        await self._respond(r, 200, body=xmlio.complete_multipart_xml(location, r.bucket, r.key, obj.etag or ""), xml=True)

    async def _list_parts(self, r: _Req) -> None:
        q = r.query
        marker = q.get("part-number-marker", "0")
        if not re.fullmatch(r"\d+", marker):
            raise errors.InvalidArgument("part-number-marker должен быть числом")
        max_parts = self._max_keys(q.get("max-parts"), "max-parts")
        parts, truncated = await self.run(self.service.list_parts, r.bucket, r.key, q["uploadId"], int(marker),
                                          max_parts)
        body = xmlio.list_parts_xml(r.bucket, r.key, q["uploadId"],
                                    [(p.part_number, p.last_modified, p.etag, p.size_bytes) for p in parts],
                                    marker=int(marker), max_parts=max_parts, truncated=truncated)
        await self._respond(r, 200, body=body, xml=True)

    async def _list_uploads(self, r: _Req) -> None:
        q = r.query
        encoding = q.get("encoding-type")
        if encoding not in (None, "url"):
            raise errors.InvalidArgument("encoding-type: поддерживается только url")
        max_uploads = self._max_keys(q.get("max-uploads"), "max-uploads")
        key_marker, upload_id_marker = q.get("key-marker", ""), q.get("upload-id-marker", "")
        uploads, truncated = await self.run(lambda: self.service.list_uploads(
            r.bucket, prefix=q.get("prefix", ""), key_marker=key_marker, upload_id_marker=upload_id_marker,
            max_uploads=max_uploads))
        body = xmlio.list_uploads_xml(r.bucket, [(u.object_key, u.id, u.created_at) for u in uploads],
                                      prefix=q.get("prefix", ""), key_marker=key_marker,
                                      upload_id_marker=upload_id_marker, max_uploads=max_uploads,
                                      truncated=truncated, encoding_url=encoding == "url")
        await self._respond(r, 200, body=body, xml=True)

    def close(self) -> None:
        self._pool.shutdown(wait=False)
