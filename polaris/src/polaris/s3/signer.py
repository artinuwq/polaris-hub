"""Подпись S3-запросов SigV4 на стороне клиента (самотест Polaris, тесты, скрипты)."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from polaris.s3 import sigv4


def sign_headers(secret: str, access_key_id: str, region: str, method: str, raw_path: str, raw_query: str,
                 headers: dict[str, str], body: bytes | None = None, *, payload_hash: str | None = None,
                 amz_date: str | None = None) -> dict[str, str]:
    """Вернуть заголовки запроса с x-amz-date, x-amz-content-sha256 и Authorization. host обязателен в headers."""
    payload_hash = payload_hash or hashlib.sha256(body or b"").hexdigest()
    amz_date = amz_date or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = {k.lower(): v for k, v in headers.items()}
    out["x-amz-date"], out["x-amz-content-sha256"] = amz_date, payload_hash
    signed = tuple(sorted(out))
    date = amz_date[:8]
    scope = f"{date}/{region}/s3/aws4_request"
    req = sigv4.SigRequest(method, raw_path, raw_query, list(out.items()))
    signature = sigv4.sign(sigv4.signing_key(secret, date, region), sigv4.string_to_sign(
        amz_date, scope, sigv4.canonical_request(req, signed, payload_hash, presigned=False)))
    out["authorization"] = (f"AWS4-HMAC-SHA256 Credential={access_key_id}/{scope}, "
                            f"SignedHeaders={';'.join(signed)}, Signature={signature}")
    return out
