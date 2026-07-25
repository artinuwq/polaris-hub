from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from polaris.infra.settings import Settings
from polaris.shared.exceptions import AuthorizationError, PolarisError
from polaris.update.manager import UpdateManager

settings = Settings.from_env()
app = FastAPI(title="Polaris API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = Path(__file__).resolve().parents[3] / "frontend"


def _authorize(token: str | None) -> None:
    expected = settings.update_api_token
    if not expected:
        if settings.debug:
            return
        raise HTTPException(status_code=503, detail="UPDATE_API_TOKEN не задан")
    if token != expected:
        raise AuthorizationError("Неверный токен обновления")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/update/status")
def update_status(x_polaris_token: str | None = Header(default=None)):
    try:
        _authorize(x_polaris_token)
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
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PolarisError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/update")
def run_update(x_polaris_token: str | None = Header(default=None)):
    try:
        _authorize(x_polaris_token)
        result = UpdateManager(settings).apply()
        return {
            "success": result.success,
            "message": result.message,
            "data": {
                "previous_sha": result.previous_sha,
                "current_sha": result.current_sha,
                "restarted": result.restarted,
            },
        }
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PolarisError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


if FRONTEND_DIR.exists():
    assets_dir = FRONTEND_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/")
    def mini_app():
        return FileResponse(FRONTEND_DIR / "index.html")
