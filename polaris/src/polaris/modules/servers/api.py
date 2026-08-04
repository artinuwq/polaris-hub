from __future__ import annotations

"""API роутеры модуля Servers.

Два независимых поверхности:

  * Admin-facing (/api/v1/servers/...)  — управление из Hub UI, авторизация
    как везде в проекте (Telegram initData / X-Polaris-Token, require_admin).

  * Agent-facing (/api/v1/agent/...)    — протокол Polaris Agent. register
    не требует токена в заголовке (сам токен передаётся в теле и одноразовый);
    heartbeat/metrics/events требуют постоянных agent credentials, выданных
    при регистрации: заголовки X-Agent-Id + Authorization: Bearer <agent_token>.
"""

from fastapi import APIRouter, Depends, Header, HTTPException

from polaris.integrations.telegram.auth import TelegramWebAppUser
from polaris.shared.auth import require_admin
from polaris.modules.servers.models import (
    AgentEventRequest,
    AgentHeartbeatRequest,
    AgentMetricsRequest,
    AgentRegisterRequest,
    ServerCreate,
    ServerUpdate,
)
from polaris.modules.servers import service

admin_router = APIRouter(prefix="/api/v1/servers", tags=["servers"])
agent_router = APIRouter(prefix="/api/v1/agent", tags=["agent"])


# ─────────────────────────── Agent auth dependency ───────────────────────────

def require_agent(
    x_agent_id: str | None = Header(default=None, alias="X-Agent-Id"),
    authorization: str | None = Header(default=None),
) -> dict:
    if not x_agent_id or not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Нужны заголовки X-Agent-Id и Authorization: Bearer <token>")

    agent_token = authorization.split(" ", 1)[1].strip()
    server = service.authenticate_agent(x_agent_id, agent_token)
    if not server:
        raise HTTPException(status_code=401, detail="Неверные agent credentials")
    return server


# ─────────────────────────── Admin: серверы ───────────────────────────

@admin_router.get("")
def list_servers(_user: TelegramWebAppUser | None = Depends(require_admin)):
    servers = service.list_servers()
    return {"success": True, "data": {"servers": [s.model_dump() for s in servers]}}


@admin_router.post("")
def create_server(body: ServerCreate, _user: TelegramWebAppUser | None = Depends(require_admin)):
    server, token = service.create_server(name=body.name, address=body.address)
    return {
        "success": True,
        "data": {"server": server.model_dump(), "registration_token": token.model_dump()},
    }


@admin_router.get("/attention-candidates")
def attention_candidates(_user: TelegramWebAppUser | None = Depends(require_admin)):
    return {"success": True, "data": {"candidates": service.get_attention_candidates()}}


@admin_router.get("/{server_id}")
def get_server(server_id: str, _user: TelegramWebAppUser | None = Depends(require_admin)):
    server = service.get_server(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return {"success": True, "data": server.model_dump()}


@admin_router.patch("/{server_id}")
def update_server(server_id: str, body: ServerUpdate, _user: TelegramWebAppUser | None = Depends(require_admin)):
    server = service.update_server(server_id, body.model_dump(exclude_none=True))
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return {"success": True, "data": server.model_dump()}


@admin_router.delete("/{server_id}")
def delete_server(server_id: str, _user: TelegramWebAppUser | None = Depends(require_admin)):
    deleted = service.delete_server(server_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Server not found")
    return {"success": True, "message": "Server deleted"}


@admin_router.post("/{server_id}/registration-token")
def new_registration_token(server_id: str, _user: TelegramWebAppUser | None = Depends(require_admin)):
    server = service.get_server(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    token = service.generate_registration_token(server_id)
    return {"success": True, "data": token.model_dump()}


@admin_router.get("/{server_id}/events")
def server_events(server_id: str, limit: int = 30, _user: TelegramWebAppUser | None = Depends(require_admin)):
    events = service.get_events(server_id, limit=limit)
    return {"success": True, "data": {"events": [e.model_dump() for e in events]}}


@admin_router.get("/{server_id}/metrics-history")
def metrics_history(server_id: str, limit: int = 60, _user: TelegramWebAppUser | None = Depends(require_admin)):
    return {"success": True, "data": {"points": service.get_metrics_history(server_id, limit=limit)}}


# ─────────────────────────── Agent protocol ───────────────────────────

@agent_router.post("/register")
def agent_register(body: AgentRegisterRequest):
    try:
        result = service.register_agent(body)
    except service.RegistrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "data": result.model_dump()}


@agent_router.post("/heartbeat")
def agent_heartbeat(body: AgentHeartbeatRequest, server: dict = Depends(require_agent)):
    service.record_heartbeat(server["id"], agent_version=body.agent_version)
    return {"success": True}


@agent_router.post("/metrics")
def agent_metrics(body: AgentMetricsRequest, server: dict = Depends(require_agent)):
    service.ingest_metrics(server["id"], body)
    return {"success": True}


@agent_router.post("/events")
def agent_events(body: AgentEventRequest, server: dict = Depends(require_agent)):
    event = service.ingest_event(server["id"], body)
    return {"success": True, "data": event.model_dump() if event else None}
