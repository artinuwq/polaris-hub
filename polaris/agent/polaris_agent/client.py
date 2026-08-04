from __future__ import annotations

import logging
import time
from typing import Any

import requests

from polaris_agent import __version__
from polaris_agent.config import AgentConfig

log = logging.getLogger("polaris_agent.client")


class HubUnavailable(Exception):
    """Hub временно недоступен — вызывающий код должен продолжить работу
    и попробовать снова на следующем цикле (Agent не должен падать)."""


class RegistrationFailed(Exception):
    pass


class HubClient:
    def __init__(self, config: AgentConfig, timeout: float = 10.0):
        self.config = config
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["User-Agent"] = f"polaris-agent/{__version__}"

    def _auth_headers(self) -> dict[str, str]:
        return {
            "X-Agent-Id": self.config.agent_id,
            "Authorization": f"Bearer {self.config.agent_token}",
        }

    def register(self, token: str, hostname: str, os_name: str, kernel: str,
                 architecture: str, address: str = "") -> dict[str, Any]:
        url = f"{self.config.hub_url}/api/v1/agent/register"
        payload = {
            "token": token,
            "hostname": hostname,
            "os": os_name,
            "kernel": kernel,
            "architecture": architecture,
            "agent_version": __version__,
            "address": address,
        }
        try:
            resp = self.session.post(url, json=payload, timeout=self.timeout)
        except requests.RequestException as exc:
            raise RegistrationFailed(f"Не удалось подключиться к Hub: {exc}") from exc

        if resp.status_code != 200:
            detail = _safe_detail(resp)
            raise RegistrationFailed(f"Hub отклонил регистрацию ({resp.status_code}): {detail}")

        body = resp.json()
        if not body.get("success"):
            raise RegistrationFailed(body.get("message", "Неизвестная ошибка регистрации"))
        return body["data"]

    def heartbeat(self, status: str = "online") -> None:
        self._post_authed("/api/v1/agent/heartbeat", {"agent_version": __version__, "status": status})

    def metrics(self, payload: dict[str, Any]) -> None:
        self._post_authed("/api/v1/agent/metrics", payload)

    def event(self, event_type: str, severity: str, payload: dict[str, Any]) -> None:
        self._post_authed("/api/v1/agent/events", {"type": event_type, "severity": severity, "payload": payload})

    def _post_authed(self, path: str, payload: dict[str, Any]) -> None:
        url = f"{self.config.hub_url}{path}"
        try:
            resp = self.session.post(url, json=payload, headers=self._auth_headers(), timeout=self.timeout)
        except requests.RequestException as exc:
            raise HubUnavailable(str(exc)) from exc

        if resp.status_code == 401:
            raise RegistrationFailed("Hub больше не признаёт credentials агента (401) — нужна повторная регистрация")
        if resp.status_code >= 500:
            raise HubUnavailable(f"Hub вернул {resp.status_code}")
        if resp.status_code >= 400:
            log.warning("Hub отклонил запрос %s: %s", path, _safe_detail(resp))


def _safe_detail(resp: requests.Response) -> str:
    try:
        return str(resp.json().get("detail", resp.text[:200]))
    except Exception:
        return resp.text[:200]


def with_backoff(attempt: int, base: float = 2.0, cap: float = 300.0) -> float:
    """Экспоненциальный backoff с потолком — для длительной недоступности Hub."""
    return min(cap, base * (2 ** attempt))


def sleep_with_backoff(attempt: int) -> None:
    time.sleep(with_backoff(attempt))
