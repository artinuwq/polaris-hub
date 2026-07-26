"""Тесты проверки Telegram WebApp initData."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from polaris.integrations.telegram.auth import (
    authenticate_webapp,
    validate_init_data,
)
from polaris.shared.exceptions import AuthorizationError


def _sign(bot_token: str, fields: dict[str, str]) -> str:
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()


def _make_init_data(bot_token: str, user_id: int = 42, auth_date: int | None = None) -> str:
    user = json.dumps(
        {"id": user_id, "first_name": "Ada", "username": "ada"},
        separators=(",", ":"),
    )
    fields = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAE",
        "user": user,
    }
    fields_with_hash = dict(fields)
    # hash считается без самого hash
    digest = _sign(bot_token, fields)
    return urlencode({**fields_with_hash, "hash": digest})


def test_validate_init_data_ok():
    token = "123456:ABC"
    init_data = _make_init_data(token)
    data = validate_init_data(init_data, token)
    assert "user" in data


def test_validate_init_data_bad_hash():
    token = "123456:ABC"
    init_data = _make_init_data(token) + "dead"
    with pytest.raises(AuthorizationError):
        validate_init_data(init_data, token)


def test_authenticate_admin_only():
    token = "123456:ABC"
    init_data = _make_init_data(token, user_id=7)
    user = authenticate_webapp(init_data, token, admin_ids=[7], require_admin=True)
    assert user.id == 7
    assert user.username == "ada"


def test_authenticate_rejects_non_admin():
    token = "123456:ABC"
    init_data = _make_init_data(token, user_id=7)
    with pytest.raises(AuthorizationError):
        authenticate_webapp(init_data, token, admin_ids=[1], require_admin=True)


def test_authenticate_rejects_stale():
    token = "123456:ABC"
    init_data = _make_init_data(token, auth_date=int(time.time()) - 10_000)
    with pytest.raises(AuthorizationError):
        validate_init_data(init_data, token, max_age_seconds=60)
