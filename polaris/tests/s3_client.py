"""Тестовый S3-клиент: подпись SigV4 (заголовки, presigned, aws-chunked) на примитивах polaris.s3.sigv4.

Примитивы проверены официальными тест-векторами AWS (tests/test_s3_sigv4.py). Для независимой проверки
используются curl --aws-sigv4 и (если установлен) boto3."""
from __future__ import annotations

import hashlib
import zlib
import base64
from datetime import datetime, timezone
from urllib.parse import urlsplit

import requests

from polaris.s3 import sigv4
from polaris.s3.util import aws_quote

AK, SK, REGION = "POLARISTESTKEY", "polaris/test+secret/KEY0123456789", "us-east-1"


class S3Client:
    def __init__(self, base_url: str, prefix: str = "/s3", access_key=AK, secret=SK, region=REGION) -> None:
        self.base, self.prefix, self.ak, self.sk, self.region = base_url, prefix, access_key, secret, region
        self.host = urlsplit(base_url).netloc
        self.session = requests.Session()

    def url(self, bucket: str = "", key: str | None = None, query: str = "") -> tuple[str, str]:
        path = self.prefix + ("/" + bucket if bucket else "/")
        if key is not None:
            path += "/" + aws_quote(key, keep_slash=True)
        return path, query

    def sign_headers(self, method, path, query, headers, payload_hash, *, amz_date=None, secret=None, ak=None):
        amz_date = amz_date or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        h = {"host": self.host, "x-amz-date": amz_date, "x-amz-content-sha256": payload_hash}
        h.update({k.lower(): v for k, v in headers.items()})
        signed = tuple(sorted(h))
        req = sigv4.SigRequest(method, path, query, list(h.items()))
        date = amz_date[:8]
        scope = f"{date}/{self.region}/s3/aws4_request"
        key = sigv4.signing_key(secret or self.sk, date, self.region)
        sig = sigv4.sign(key, sigv4.string_to_sign(amz_date, scope, sigv4.canonical_request(
            req, signed, payload_hash, presigned=False)))
        h["authorization"] = (f"AWS4-HMAC-SHA256 Credential={ak or self.ak}/{scope}, "
                              f"SignedHeaders={';'.join(signed)}, Signature={sig}")
        return h, sig, key, scope, amz_date

    def request(self, method, bucket="", key=None, query="", headers=None, body=b"", *, payload_hash=None,
                sign=True, stream_body=None, **kw):
        path, query = self.url(bucket, key, query)
        headers = dict(headers or {})
        if payload_hash is None:
            payload_hash = hashlib.sha256(body).hexdigest()
        if sign:
            h, *_ = self.sign_headers(method, path, query, headers, payload_hash, **kw)
        else:
            h = headers
        h.pop("host", None)
        url = self.base + path + (("?" + query) if query else "")
        return self.session.request(method, url, headers=h, data=stream_body if stream_body is not None else body,
                                    timeout=30)

    # ─── удобные обёртки ───
    def put(self, bucket, key, body=b"", headers=None, **kw):
        return self.request("PUT", bucket, key, headers=headers, body=body, **kw)

    def get(self, bucket, key, headers=None, query="", **kw):
        return self.request("GET", bucket, key, query=query, headers=headers, **kw)

    def presign(self, method, bucket, key, expires=300, extra_query=(), amz_date=None, secret=None) -> str:
        path, _ = self.url(bucket, key)
        amz_date = amz_date or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        query = sigv4.presign_url_query(secret or self.sk, self.ak, method=method, host=self.host, raw_path=path,
                                        extra_query=list(extra_query), region=self.region, amz_date=amz_date,
                                        expires=expires)
        return f"{self.base}{path}?{query}"

    # ─── aws-chunked ───
    def put_chunked(self, bucket, key, data: bytes, *, mode="STREAMING-AWS4-HMAC-SHA256-PAYLOAD", piece=64 * 1024,
                    trailer_crc32: str | None = None, headers=None, tamper=False):
        path, query = self.url(bucket, key)
        extra = {"content-encoding": "aws-chunked", "x-amz-decoded-content-length": str(len(data))}
        trailer_name = "x-amz-checksum-crc32"
        crc = trailer_crc32 or base64.b64encode(zlib.crc32(data).to_bytes(4, "big")).decode()
        if mode.endswith("TRAILER"):
            extra["x-amz-trailer"] = trailer_name
        extra.update({k.lower(): v for k, v in (headers or {}).items()})
        seed_holder: dict = {}

        def build(seed_sig, key_, scope, amz_date):
            prev, out = seed_sig, []
            signed = mode.startswith("STREAMING-AWS4")
            chunks = [data[i:i + piece] for i in range(0, len(data), piece)] + [b""]
            for chunk in chunks:
                if signed:
                    sts = "\n".join(["AWS4-HMAC-SHA256-PAYLOAD", amz_date, scope, prev, sigv4.EMPTY_SHA256,
                                     hashlib.sha256(chunk).hexdigest()])
                    prev = sigv4.sign(key_, sts)
                    ext = f";chunk-signature={prev}"
                else:
                    ext = ""
                shown = (b"X" + chunk[1:]) if (tamper and chunk and chunk is chunks[0]) else chunk
                out.append(f"{len(chunk):x}{ext}\r\n".encode() + shown + (b"\r\n" if chunk else b""))
            if mode.endswith("TRAILER"):
                line = f"{trailer_name}:{crc}"
                tail = line + "\r\n"
                if signed:
                    tsig = sigv4.sign(key_, "\n".join(["AWS4-HMAC-SHA256-TRAILER", amz_date, scope, prev,
                                                       hashlib.sha256((line + "\n").encode()).hexdigest()]))
                    tail += f"x-amz-trailer-signature:{tsig}\r\n"
                out.append(tail.encode())
            out.append(b"\r\n")
            return b"".join(out)

        # две итерации: длина тела (content-length) входит в подпись, а тело зависит от seed-подписи
        length = "0"
        for _ in range(3):
            h, sig, key_, scope, amz_date = self.sign_headers("PUT", path, query, {**extra, "content-length": length},
                                                              mode, amz_date=seed_holder.get("date"))
            seed_holder["date"] = amz_date
            body = build(sig, key_, scope, amz_date)
            if str(len(body)) == length:
                break
            length = str(len(body))
        h.pop("host", None)
        h["content-length"] = str(len(body))
        url = self.base + path
        return self.session.put(url, headers=h, data=body, timeout=30)
