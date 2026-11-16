"""AWS Signature Version 4 для S3: заголовок Authorization и presigned URL.

Только stdlib (hmac/hashlib). Проверка подписи — hmac.compare_digest. Модель безопасности та же, что у AWS:
подпись привязана к методу, пути, параметрам, подписанным заголовкам, хешу тела, дате и области действия;
повторное использование ограничено окном допустимого рассинхрона часов (для presigned — X-Amz-Expires).
Защита от повторов внутри окна (nonce) не вводится — как и в самом S3.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from polaris.s3 import errors
from polaris.s3.credentials import CredentialProvider
from polaris.s3.util import aws_quote, decode_path, parse_http_date, parse_query

ALGORITHM = "AWS4-HMAC-SHA256"
SERVICE = "s3"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
UNSIGNED_PAYLOAD = "UNSIGNED-PAYLOAD"
MAX_PRESIGN_EXPIRES = 604800

_AUTH_RE = re.compile(
    r"^AWS4-HMAC-SHA256\s+Credential=(?P<cred>[^,\s]+)\s*,\s*SignedHeaders=(?P<headers>[^,\s]+)\s*,\s*"
    r"Signature=(?P<sig>[0-9a-fA-F]+)\s*$"
)


@dataclass(frozen=True)
class SigRequest:
    method: str
    raw_path: str  # путь как пришёл, с процентным кодированием и префиксом endpoint-а
    raw_query: str
    headers: list[tuple[str, str]]  # имена в нижнем регистре

    def header(self, name: str) -> str | None:
        values = [v for k, v in self.headers if k == name]
        return ",".join(v.strip() for v in values) if values else None


@dataclass(frozen=True)
class AuthResult:
    access_key_id: str
    region: str
    amz_date: str
    scope: str
    signing_key: bytes = field(repr=False)
    signature: str = field(repr=False)
    signed_headers: tuple[str, ...] = ()
    payload_hash: str = UNSIGNED_PAYLOAD
    presigned: bool = False
    principal: object = field(default=None, repr=False)  # Credential, чей ключ подписал запрос


# ─── примитивы ───

def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def signing_key(secret: str, date: str, region: str, service: str = SERVICE) -> bytes:
    k = _hmac(("AWS4" + secret).encode("utf-8"), date)
    for part in (region, service, "aws4_request"):
        k = _hmac(k, part)
    return k


def canonical_uri(raw_path: str) -> str:
    """S3: путь кодируется один раз, без нормализации сегментов."""
    path = decode_path(raw_path) or "/"
    return aws_quote(path, keep_slash=True)


def canonical_query(pairs: list[tuple[str, str]], *, drop: str | None = None) -> str:
    items = sorted((aws_quote(k), aws_quote(v)) for k, v in pairs if k != drop)
    return "&".join(f"{k}={v}" for k, v in items)


def canonical_headers(req: SigRequest, signed: tuple[str, ...]) -> str:
    out = []
    for name in signed:
        values = [re.sub(r"\s+", " ", v.strip()) for k, v in req.headers if k == name]
        if not values:
            raise errors.AccessDenied(f"подписанный заголовок {name} отсутствует в запросе")
        out.append(f"{name}:{','.join(values)}\n")
    return "".join(out)


def canonical_request(req: SigRequest, signed: tuple[str, ...], payload_hash: str, *, presigned: bool) -> str:
    query = canonical_query(parse_query(req.raw_query), drop="X-Amz-Signature" if presigned else None)
    return "\n".join([req.method, canonical_uri(req.raw_path), query, canonical_headers(req, signed),
                      ";".join(signed), payload_hash])


def string_to_sign(amz_date: str, scope: str, canonical: str) -> str:
    return "\n".join([ALGORITHM, amz_date, scope, hashlib.sha256(canonical.encode("utf-8")).hexdigest()])


def sign(key: bytes, to_sign: str) -> str:
    return hmac.new(key, to_sign.encode("utf-8"), hashlib.sha256).hexdigest()


def _parse_amz_date(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        raise errors.AccessDenied("некорректный формат даты запроса") from None


def _check_scope(cred_parts: list[str], region: str, *, query: bool) -> tuple[str, str, str]:
    err = errors.AuthorizationQueryParametersError if query else errors.AuthorizationHeaderMalformed
    if len(cred_parts) != 5 or cred_parts[4] != "aws4_request" or not re.fullmatch(r"\d{8}", cred_parts[1]):
        raise err("неверный формат Credential")
    key_id, date, cred_region, service, _ = cred_parts
    if service != SERVICE:
        raise err(f"неверный сервис '{service}', ожидается 's3'")
    if cred_region != region:
        raise err(f"регион '{cred_region}' неверен; ожидается '{region}'", extra={"Region": region})
    return key_id, date, f"{date}/{cred_region}/{service}/aws4_request"


def _check_time(now: datetime, t: datetime, skew: int) -> None:
    if abs((now - t).total_seconds()) > skew:
        raise errors.RequestTimeTooSkewed(
            extra={"RequestTime": t.strftime("%Y%m%dT%H%M%SZ"), "ServerTime": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "MaxAllowedSkewMilliseconds": str(skew * 1000)})


def has_body(req: SigRequest) -> bool:
    length = req.header("content-length")
    return bool(req.header("transfer-encoding")) or bool(length and length.strip() not in ("", "0"))


def verify(req: SigRequest, provider: CredentialProvider, *, region: str, now: datetime | None = None,
           skew_seconds: int = 900) -> AuthResult:
    """Проверить подпись запроса (заголовок Authorization или presigned query). Бросает S3Error."""
    now = now or datetime.now(timezone.utc)
    query = dict(parse_query(req.raw_query))
    if "X-Amz-Algorithm" in query or "X-Amz-Signature" in query:
        return _verify_presigned(req, provider, query, region, now, skew_seconds)
    auth = req.header("authorization")
    if not auth:
        raise errors.AccessDenied("Anonymous access is forbidden for this operation")
    return _verify_header(req, provider, auth, region, now, skew_seconds)


def _verify_header(req, provider, auth, region, now, skew) -> AuthResult:
    m = _AUTH_RE.match(auth.strip())
    if not m:
        if not auth.startswith("AWS4-HMAC-SHA256"):
            raise errors.AuthorizationHeaderMalformed("поддерживается только AWS4-HMAC-SHA256")
        raise errors.AuthorizationHeaderMalformed()
    key_id, date, scope = _check_scope(m["cred"].split("/"), region, query=False)
    signed = tuple(m["headers"].split(";"))
    if "host" not in signed:
        raise errors.AccessDenied("заголовок host должен быть подписан")
    amz_date = req.header("x-amz-date")
    if amz_date is None:
        http_date = req.header("date")
        parsed = parse_http_date(http_date) if http_date else None
        if parsed is None:
            raise errors.AccessDenied("в запросе нет заголовка x-amz-date или Date")
        amz_date = parsed.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    t = _parse_amz_date(amz_date)
    if amz_date[:8] != date:
        raise errors.AuthorizationHeaderMalformed("дата в Credential не совпадает с датой запроса")
    _check_time(now, t, skew)
    cred = provider.lookup(key_id)
    if cred is None:
        raise errors.InvalidAccessKeyId()
    payload_hash = req.header("x-amz-content-sha256")
    if payload_hash is None:
        if has_body(req):
            raise errors.InvalidRequest("Missing required header for this request: x-amz-content-sha256")
        payload_hash = EMPTY_SHA256
    key = signing_key(cred.secret_access_key, date, region)
    expected = sign(key, string_to_sign(amz_date, scope,
                                        canonical_request(req, signed, payload_hash, presigned=False)))
    if not hmac.compare_digest(expected, m["sig"].lower()):
        raise errors.SignatureDoesNotMatch()
    return AuthResult(key_id, region, amz_date, scope, key, expected, signed, payload_hash, principal=cred)


def _verify_presigned(req, provider, query, region, now, skew) -> AuthResult:
    err = errors.AuthorizationQueryParametersError
    if query.get("X-Amz-Algorithm") != ALGORITHM:
        raise err("X-Amz-Algorithm должен быть AWS4-HMAC-SHA256")
    for name in ("X-Amz-Credential", "X-Amz-Date", "X-Amz-Expires", "X-Amz-SignedHeaders", "X-Amz-Signature"):
        if not query.get(name):
            raise err(f"отсутствует параметр {name}")
    key_id, date, scope = _check_scope(query["X-Amz-Credential"].split("/"), region, query=True)
    amz_date = query["X-Amz-Date"]
    if not re.fullmatch(r"\d{8}T\d{6}Z", amz_date) or amz_date[:8] != date:
        raise err("X-Amz-Date неверен или не совпадает с датой в X-Amz-Credential")
    try:
        expires = int(query["X-Amz-Expires"])
    except ValueError:
        raise err("X-Amz-Expires должен быть числом") from None
    if not 1 <= expires <= MAX_PRESIGN_EXPIRES:
        raise err("X-Amz-Expires должен быть от 1 до 604800 секунд")
    signed = tuple(query["X-Amz-SignedHeaders"].split(";"))
    if "host" not in signed:
        raise errors.AccessDenied("заголовок host должен быть подписан")
    t = _parse_amz_date(amz_date)
    seconds = (now - t).total_seconds()
    if seconds < -skew:
        raise errors.AccessDenied("Request is not valid yet")
    if seconds > expires:
        raise errors.AccessDenied("Request has expired", extra={
            "X-Amz-Expires": str(expires), "ServerTime": now.strftime("%Y-%m-%dT%H:%M:%SZ")})
    cred = provider.lookup(key_id)
    if cred is None:
        raise errors.InvalidAccessKeyId()
    payload_hash = req.header("x-amz-content-sha256") or UNSIGNED_PAYLOAD
    key = signing_key(cred.secret_access_key, date, region)
    expected = sign(key, string_to_sign(amz_date, scope,
                                        canonical_request(req, signed, payload_hash, presigned=True)))
    if not hmac.compare_digest(expected, query["X-Amz-Signature"].lower()):
        raise errors.SignatureDoesNotMatch()
    return AuthResult(key_id, region, amz_date, scope, key, expected, signed, payload_hash, presigned=True,
                      principal=cred)


# ─── для клиентской стороны (тесты, скрипты): подпись запроса ───

def presign_url_query(secret: str, access_key_id: str, *, method: str, host: str, raw_path: str,
                      extra_query: list[tuple[str, str]], region: str, amz_date: str, expires: int,
                      signed_headers: tuple[str, ...] = ("host",)) -> str:
    """Построить query presigned URL (используется тестами; boto3 делает то же самое)."""
    date = amz_date[:8]
    scope = f"{date}/{region}/{SERVICE}/aws4_request"
    pairs = list(extra_query) + [
        ("X-Amz-Algorithm", ALGORITHM), ("X-Amz-Credential", f"{access_key_id}/{scope}"),
        ("X-Amz-Date", amz_date), ("X-Amz-Expires", str(expires)), ("X-Amz-SignedHeaders", ";".join(signed_headers)),
    ]
    raw_query = canonical_query(pairs)
    req = SigRequest(method, raw_path, raw_query, [("host", host)])
    cr = canonical_request(req, signed_headers, UNSIGNED_PAYLOAD, presigned=True)
    signature = sign(signing_key(secret, date, region), string_to_sign(amz_date, scope, cr))
    return f"{raw_query}&X-Amz-Signature={signature}"
