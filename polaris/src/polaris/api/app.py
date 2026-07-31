from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from polaris.infra.database import run_migration
from polaris.infra.settings import Settings
from polaris.integrations.telegram.auth import TelegramWebAppUser, authenticate_webapp
from polaris.modules.calendar.api import router as calendar_router
from polaris.modules.events.api import router as events_router
from polaris.modules.todo.api import router as tasks_router
from polaris.shared.exceptions import AuthorizationError, PolarisError
from polaris.update.manager import UpdateManager

settings = Settings.from_env()
MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "database" / "migrations"
FRONTEND_DIR = Path(__file__).resolve().parents[3] / "frontend"


def _run_migrations() -> None:
    """Run pending database migrations on startup."""
    if MIGRATIONS_DIR.exists():
        for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
            run_migration(sql_file.read_text())


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: run migrations on startup."""
    _run_migrations()
    yield


app = FastAPI(title="Polaris API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class TgAuthBody(BaseModel):
    initData: str = Field(default="", description="Telegram.WebApp.initData")


def _user_from_init_data(init_data: str) -> TelegramWebAppUser:
    return authenticate_webapp(
        init_data,
        settings.telegram_bot_token,
        admin_ids=settings.telegram_admin_ids,
        require_admin=True,
        max_age_seconds=settings.telegram_init_data_max_age,
    )


# ─── Auth dependency ───


def _require_admin(
    x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"),
    x_polaris_token: str | None = Header(default=None, alias="X-Polaris-Token"),
) -> TelegramWebAppUser | None:
    """Доступ: валидный Telegram initData админа ИЛИ UPDATE_API_TOKEN."""
    if x_telegram_init_data:
        try:
            return _user_from_init_data(x_telegram_init_data)
        except AuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    expected = settings.update_api_token
    if expected and x_polaris_token == expected:
        return None

    if settings.debug and not expected and not settings.telegram_bot_token:
        return None

    raise HTTPException(
        status_code=401,
        detail="Откройте Mini App из Telegram или передайте X-Polaris-Token",
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/tg/auth")
def tg_auth(body: TgAuthBody):
    """Как в lumica: принять initData в JSON и проверить подпись."""
    try:
        user = _user_from_init_data(body.initData)
        return _me_payload(user)
    except AuthorizationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@app.get("/api/me")
def me(user: TelegramWebAppUser | None = Depends(_require_admin)):
    return _me_payload(user)


@app.get("/api/update/status")
def update_status(_user: TelegramWebAppUser | None = Depends(_require_admin)):
    try:
        status = UpdateManager(settings).check()
        return {
            "success": True,
            "message": status.message,
            "data": {
                "branch": status.branch,
                "local_sha": status.local_sha,
                "remote_sha": status.remote_sha,
                "up_to_date": status.up_to_date,
                "dirty": status.dirty,
            },
        }
    except PolarisError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/update")
def run_update(_user: TelegramWebAppUser | None = Depends(_require_admin)):
    try:
        result = UpdateManager(settings).apply()
        return {
            "success": True,
            "message": result.message,
            "data": {
                "previous_sha": result.previous_sha,
                "current_sha": result.current_sha,
                "restarted": result.restarted,
            },
        }
    except PolarisError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/restart")
def restart_service(_user: TelegramWebAppUser | None = Depends(_require_admin)):
    try:
        restarted, message = UpdateManager(settings).restart_service()
        return {
            "success": True,
            "message": message,
            "data": {
                "restarted": restarted,
            },
        }
    except PolarisError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ─── Include routers ───
app.include_router(tasks_router)
app.include_router(calendar_router)
app.include_router(events_router)


# ─── Helpers ───


def _me_payload(user: TelegramWebAppUser | None) -> dict:
    if user is None:
        return {
            "success": True,
            "ok": True,
            "message": "Авторизован по API-токену",
            "data": {"auth": "token", "is_admin": True},
        }
    return {
        "success": True,
        "ok": True,
        "message": f"Привет, {user.display_name}",
        "data": {
            "auth": "telegram",
            "is_admin": True,
            "id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "display_name": user.display_name,
        },
        "user": {
            "id": user.id,
            "username": user.username,
            "first_name": user.first_name,
        },
    }


# ─── Static files & SPA fallback ───

if FRONTEND_DIR.exists():
    assets_dir = FRONTEND_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/")
    def mini_app():
        return FileResponse(FRONTEND_DIR / "index.html")