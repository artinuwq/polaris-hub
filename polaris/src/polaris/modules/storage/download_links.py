"""Короткоживущие подписанные ссылки на скачивание объекта (аналог presigned URL).

Нужны клиентам, которые скачивают файл сами и не умеют слать заголовки авторизации — например,
Telegram.WebApp.downloadFile: Telegram получает обычный URL и сохраняет файл в загрузки.
Ссылка даёт доступ к ОДНОМУ объекту и действует TTL_SECONDS; подписывается HMAC-SHA256 от секрета Polaris.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

TTL_SECONDS = 300


def _key(secret: str) -> bytes:
    return hashlib.sha256(b"polaris-storage-download-v1:" + secret.encode("utf-8")).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _signature(secret: str, payload: str) -> str:
    return _b64(hmac.new(_key(secret), payload.encode("ascii"), hashlib.sha256).digest())


def sign(secret: str, object_id: str, ttl: int = TTL_SECONDS, now: float | None = None) -> str:
    expires = int((time.time() if now is None else now) + ttl)
    payload = _b64(json.dumps({"o": object_id, "e": expires}, separators=(",", ":")).encode("utf-8"))
    return f"{payload}.{_signature(secret, payload)}"


def verify(secret: str, token: str, now: float | None = None) -> str | None:
    """id объекта, если подпись верна и срок не истёк; иначе None (причина наружу не раскрывается)."""
    try:
        payload, signature = token.split(".", 1)
        if not hmac.compare_digest(signature, _signature(secret, payload)):
            return None
        data = json.loads(_unb64(payload))
        if int(data["e"]) < (time.time() if now is None else now):
            return None
        return str(data["o"])
    except Exception:  # noqa: BLE001 — любой мусор вместо токена = недействительная ссылка
        return None
