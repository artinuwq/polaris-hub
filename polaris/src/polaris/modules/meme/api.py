"""API Polaris Meme (личная библиотека изображений).

  GET    /api/meme                  — ?q=&tag=&limit=&offset=: поиск по названию, тегам и описанию (пагинация offset/next_offset)
  POST   /api/meme                  — ?title=&tags=a,b&description=&filename=: тело запроса — байты изображения → созданная запись
  GET    /api/meme/{id}             — запись
  PATCH  /api/meme/{id}             — {title?, tags?, description?}
  DELETE /api/meme/{id}             — удалить запись и файл из Polaris Storage
  GET    /api/meme/settings         — настройки (шестерёнка): чат кэша, состояние inline-кэша
  PUT    /api/meme/settings         — {cache_chat_id}: сохранить чат кэша ('' — сбросить)
  POST   /api/meme/settings/test    — {cache_chat_id?}: проверить, что бот видит чат и может писать
  POST   /api/meme/settings/sync    — ?limit=&skip=: получить file_id для записей без него (порциями)
  GET    /api/meme/media/{token}    — оригинал изображения по подписанной ссылке (без заголовков авторизации:
                                      <img> и серверы Telegram их не шлют); ссылка на ОДНУ запись, действует час

Всё, кроме /media/{token}, только для админа (Telegram initData или X-Polaris-Token).
Id объектов и чанков Storage в ответах и ссылках не появляются.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from polaris.integrations.telegram.auth import TelegramWebAppUser
from polaris.modules.meme import media
from polaris.modules.meme.service import MemeError, get_meme_service
from polaris.shared.auth import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/meme", tags=["meme"])


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except MemeError as exc:
        detail = {"message": exc.detail, **exc.extra} if exc.extra else exc.detail
        raise HTTPException(status_code=exc.status, detail=detail) from None


def _ok(data=None) -> dict:
    return {"success": True, "data": data if data is not None else {}}


@router.get("")
def list_memes(q: str = Query(default=""), tag: str = Query(default=""),
               limit: int = Query(default=24, ge=1, le=100), offset: int = Query(default=0, ge=0),
               _user: TelegramWebAppUser | None = Depends(require_admin)):
    return _ok(_run(get_meme_service().listing, q, tag, limit, offset))


@router.post("")
async def create_meme(request: Request, title: str = Query(default=""), tags: str = Query(default=""),
                      description: str = Query(default=""), filename: str = Query(default=""),
                      _user: TelegramWebAppUser | None = Depends(require_admin)):
    """Тело — байты файла (не multipart). Размер ограничиваем на лету, до чтения всего тела."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > media.MAX_BYTES:
        raise HTTPException(status_code=413, detail="Файл больше 10 МиБ — это предел Telegram для изображений")
    data = bytearray()
    async for piece in request.stream():
        data += piece
        if len(data) > media.MAX_BYTES:
            raise HTTPException(status_code=413, detail="Файл больше 10 МиБ — это предел Telegram для изображений")
    created = await run_in_threadpool(_run, get_meme_service().create, bytes(data), filename=filename,
                                      title=title, tags=tags, description=description)
    return _ok(created)


@router.get("/media/{token}")
def meme_media(token: str):
    """Без авторизации: доступ даёт подписанный токен. Отдаётся целый оригинал с настоящим Content-Type."""
    row, data = _run(get_meme_service().read_by_token, token)
    headers = {
        "Content-Length": str(len(data)),
        "Cache-Control": "private, max-age=3600",
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": "inline",
    }
    return StreamingResponse(iter([data]), media_type=row["mime_type"], headers=headers)


# маршруты /settings/* объявлены раньше /{meme_id}: иначе «settings» приняли бы за id записи
@router.get("/settings")
def get_settings(_user: TelegramWebAppUser | None = Depends(require_admin)):
    return _ok(_run(get_meme_service().settings_view))


@router.put("/settings")
def update_settings(body: dict | None = Body(default=None), _user: TelegramWebAppUser | None = Depends(require_admin)):
    return _ok(_run(get_meme_service().settings_update, body))


@router.post("/settings/test")
def test_settings(body: dict | None = Body(default=None), _user: TelegramWebAppUser | None = Depends(require_admin)):
    return _ok(_run(get_meme_service().settings_test, body))


@router.post("/settings/sync")
async def sync_settings(limit: int = Query(default=10, ge=1, le=30), skip: str = Query(default=""),
                        _user: TelegramWebAppUser | None = Depends(require_admin)):
    skipped = tuple(s for s in skip.split(",") if s)[:200]
    return _ok(await run_in_threadpool(_run, get_meme_service().sync_pending, limit, skipped))


@router.get("/{meme_id}")
def get_meme(meme_id: str, _user: TelegramWebAppUser | None = Depends(require_admin)):
    return _ok(_run(get_meme_service().get, meme_id))


@router.patch("/{meme_id}")
def update_meme(meme_id: str, body: dict | None = Body(default=None),
                _user: TelegramWebAppUser | None = Depends(require_admin)):
    return _ok(_run(get_meme_service().update, meme_id, body))


@router.delete("/{meme_id}")
def delete_meme(meme_id: str, _user: TelegramWebAppUser | None = Depends(require_admin)):
    _run(get_meme_service().delete, meme_id)
    return _ok({"deleted": meme_id})
