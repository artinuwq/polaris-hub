"""API управления Polaris Storage (только для админа: Telegram initData или X-Polaris-Token).

  GET    /api/storage/status                        — компоненты, статистика, состояние S3
  GET    /api/storage/config                        — настройки (секреты замаскированы)
  PUT    /api/storage/config                        — {changes, confirm}: без confirm — предпросмотр, с confirm — запись в .env
  POST   /api/storage/test                          — тесты хранилища (запись/чтение/SHA-256/удаление/полный S3-цикл)
  GET    /api/storage/buckets · POST /api/storage/buckets
  GET    /api/storage/buckets/{bucket} · DELETE /api/storage/buckets/{bucket}   (только пустой)
  GET    /api/storage/buckets/{bucket}/objects      — ?prefix=&after=&limit=
  GET    /api/storage/objects/{id} · DELETE /api/storage/objects/{id} · GET /api/storage/objects/{id}/download
  POST   /api/storage/objects/{id}/download-link    — подписанная ссылка на скачивание (для Telegram.WebApp.downloadFile)
  GET    /api/storage/download/{token}              — единственный маршрут без admin-заголовков: доступ даёт токен ссылки
  GET    /api/storage/buckets/{bucket}/browse       — ?prefix=&cursor=&limit=: подпапки и файлы «папки» (файловый менеджер)
  POST   /api/storage/buckets/{bucket}/folders      — {prefix}: создать папку · DELETE ...?prefix=: удалить папку со всем содержимым
  PUT    /api/storage/buckets/{bucket}/upload       — ?key=: тело запроса — байты файла (перезаписывает существующий ключ)
  POST   /api/storage/buckets/{bucket}/multipart    — {key, content_type}: начать загрузку, которую режет браузер (размер части и параллелизм — в ответе)
  PUT    /api/storage/multipart/{id}/parts/{n}      — байты части → {etag} · POST .../complete — {parts:[{part_number, etag}]}: собрать объект
  GET    /api/storage/credentials · POST /api/storage/credentials
  POST   /api/storage/credentials/{id}/revoke · POST .../rotate · PATCH .../{id} · DELETE .../{id}
  GET    /api/storage/diagnostics · POST /api/storage/diagnostics/run
  GET    /api/storage/multipart · POST /api/storage/multipart/{upload_id}/abort

S3-ключи (Authorization: AWS4-HMAC-SHA256 …) этим API не принимаются: он проверяет только require_admin.
"""

from __future__ import annotations

import logging
import tempfile
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from polaris.infra.settings import Settings
from polaris.integrations.telegram.auth import TelegramWebAppUser
from polaris.modules.storage.admin import AdminError, StorageAdmin
from polaris.shared.auth import require_admin
from polaris.storage.errors import StorageError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/storage", tags=["storage"])
_admin: StorageAdmin | None = None
_NO_STORE = {"Cache-Control": "no-store"}
_PART_LIMIT = 64 * 1024 * 1024  # часть multipart целиком копится в памяти: потолок на запрос


def get_admin() -> StorageAdmin:
    global _admin
    if _admin is None:
        _admin = StorageAdmin(Settings.from_env())
    return _admin


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except AdminError as exc:
        detail = {"message": exc.detail, "errors": exc.errors} if exc.errors else exc.detail
        raise HTTPException(status_code=exc.status, detail=detail) from None
    except StorageError as exc:
        logger.warning("Storage API: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Storage недоступен или вернул ошибку") from None


def _ok(data=None) -> dict:
    return {"success": True, "data": data if data is not None else {}}


def _secret_response(response: Response, data: dict) -> dict:
    for key, value in _NO_STORE.items():  # секрет показывается один раз и не кешируется
        response.headers[key] = value
    return _ok(data)


@router.get("/status")
def storage_status(_user: TelegramWebAppUser | None = Depends(require_admin)):
    return _ok(_run(get_admin().status))


@router.get("/config")
def storage_config(_user: TelegramWebAppUser | None = Depends(require_admin)):
    return _ok(_run(get_admin().config_get))


@router.put("/config")
def storage_config_update(body: dict | None = Body(default=None),
                          _user: TelegramWebAppUser | None = Depends(require_admin)):
    body = body or {}
    return _ok(_run(get_admin().config_update, body.get("changes"), body.get("confirm") is True))


@router.post("/test")
def storage_test(body: dict | None = Body(default=None), _user: TelegramWebAppUser | None = Depends(require_admin)):
    body = body or {}
    return _ok(_run(get_admin().run_tests, body.get("steps"), body.get("use_saved", True) is not False))


@router.get("/buckets")
def list_buckets(_user: TelegramWebAppUser | None = Depends(require_admin)):
    return _ok(_run(get_admin().buckets_list))


@router.post("/buckets")
def create_bucket(body: dict | None = Body(default=None), _user: TelegramWebAppUser | None = Depends(require_admin)):
    return _ok(_run(get_admin().bucket_create, (body or {}).get("name")))


@router.get("/buckets/{bucket}")
def get_bucket(bucket: str, _user: TelegramWebAppUser | None = Depends(require_admin)):
    return _ok(_run(get_admin().bucket_get, bucket))


@router.get("/buckets/{bucket}/objects")
def list_bucket_objects(bucket: str, prefix: str = Query(default=""), after: str = Query(default=""),
                        limit: int = Query(default=100, ge=1, le=500),
                        _user: TelegramWebAppUser | None = Depends(require_admin)):
    return _ok(_run(get_admin().bucket_objects, bucket, prefix, after, limit))


@router.get("/buckets/{bucket}/browse")
def browse_bucket(bucket: str, prefix: str = Query(default=""), cursor: str = Query(default=""),
                  limit: int = Query(default=200, ge=1, le=500),
                  _user: TelegramWebAppUser | None = Depends(require_admin)):
    return _ok(_run(get_admin().bucket_browse, bucket, prefix, cursor, limit))


@router.post("/buckets/{bucket}/folders")
def create_folder(bucket: str, body: dict | None = Body(default=None),
                  _user: TelegramWebAppUser | None = Depends(require_admin)):
    return _ok(_run(get_admin().folder_create, bucket, (body or {}).get("prefix")))


@router.delete("/buckets/{bucket}/folders")
def delete_folder(bucket: str, prefix: str = Query(default=""),
                  _user: TelegramWebAppUser | None = Depends(require_admin)):
    return _ok(_run(get_admin().folder_delete, bucket, prefix))


@router.post("/buckets/{bucket}/multipart")
def start_multipart(bucket: str, body: dict | None = Body(default=None),
                    _user: TelegramWebAppUser | None = Depends(require_admin)):
    body = body or {}
    return _ok(_run(get_admin().multipart_start, bucket, body.get("key"), body.get("content_type")))


@router.put("/multipart/{upload_id}/parts/{part_number}")
async def upload_part(upload_id: str, part_number: int, request: Request,
                      _user: TelegramWebAppUser | None = Depends(require_admin)):
    """Одна часть (режет браузер): тело запроса — байты части. Часть целиком в памяти, но не больше _PART_LIMIT."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > _PART_LIMIT:
        raise HTTPException(status_code=413, detail="Часть больше допустимого размера")
    data = bytearray()
    async for piece in request.stream():
        data += piece
        if len(data) > _PART_LIMIT:
            raise HTTPException(status_code=413, detail="Часть больше допустимого размера")
    if not data:
        raise HTTPException(status_code=422, detail="Пустая часть")
    return _ok(await run_in_threadpool(_run, get_admin().multipart_part, upload_id, int(part_number), data))


@router.post("/multipart/{upload_id}/complete")
def complete_multipart(upload_id: str, body: dict | None = Body(default=None),
                       _user: TelegramWebAppUser | None = Depends(require_admin)):
    return _ok(_run(get_admin().multipart_finish, upload_id, (body or {}).get("parts")))


@router.put("/buckets/{bucket}/upload")
async def upload_object(bucket: str, request: Request, key: str = Query(default=""),
                        _user: TelegramWebAppUser | None = Depends(require_admin)):
    """Загрузка одного файла: тело запроса — сами байты (не multipart), полный ключ — в ?key=."""
    limit_bytes = int(get_admin().settings.s3_max_request_size_mib) * 1024 * 1024
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit_bytes:
        raise HTTPException(status_code=413, detail="Файл больше допустимого размера запроса")
    mime = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if mime in ("", "application/octet-stream"):
        mime = None
    spool = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)  # noqa: SIM115 — закрывается в finally
    try:
        size = 0
        async for piece in request.stream():
            size += len(piece)
            if size > limit_bytes:
                raise HTTPException(status_code=413, detail="Файл больше допустимого размера запроса")
            spool.write(piece)
        spool.seek(0)
        return _ok(await run_in_threadpool(_run, get_admin().object_upload, bucket, key, spool, mime))
    finally:
        spool.close()


@router.delete("/buckets/{bucket}")
def delete_bucket(bucket: str, _user: TelegramWebAppUser | None = Depends(require_admin)):
    return _ok(_run(get_admin().bucket_delete, bucket))


@router.get("/objects/{object_id}")
def get_object(object_id: str, _user: TelegramWebAppUser | None = Depends(require_admin)):
    return _ok(_run(get_admin().object_get, object_id))


@router.delete("/objects/{object_id}")
def delete_object(object_id: str, _user: TelegramWebAppUser | None = Depends(require_admin)):
    return _ok(_run(get_admin().object_delete, object_id))


def _file_response(info: dict, stream) -> StreamingResponse:
    filename = info["key"].rsplit("/", 1)[-1] or "object"
    ascii_name = "".join(c if 32 <= ord(c) < 127 and c not in '"\\' else "_" for c in filename)
    headers = {"Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}",
               "Content-Length": str(info["size"]), **_NO_STORE}
    return StreamingResponse(stream, media_type=info["content_type"] or "application/octet-stream", headers=headers)


@router.get("/objects/{object_id}/download")
def download_object(object_id: str, _user: TelegramWebAppUser | None = Depends(require_admin)):
    return _file_response(*_run(get_admin().object_download, object_id))


@router.post("/objects/{object_id}/download-link")
def download_object_link(object_id: str, response: Response,
                         _user: TelegramWebAppUser | None = Depends(require_admin)):
    return _secret_response(response, _run(get_admin().download_link, object_id))


@router.get("/download/{token}")
def download_by_link(token: str):
    """Без заголовков авторизации: доступ даёт подписанный токен из download-link (один объект, ~5 минут)."""
    return _file_response(*_run(get_admin().download_by_token, token))


@router.get("/credentials")
def list_credentials(_user: TelegramWebAppUser | None = Depends(require_admin)):
    return _ok(_run(get_admin().credentials_list))


@router.post("/credentials")
def create_credential(response: Response, body: dict | None = Body(default=None),
                      _user: TelegramWebAppUser | None = Depends(require_admin)):
    return _secret_response(response, _run(get_admin().credential_create, body or {}))


@router.post("/credentials/{credential_id}/revoke")
def revoke_credential(credential_id: str, _user: TelegramWebAppUser | None = Depends(require_admin)):
    return _ok(_run(get_admin().credential_revoke, credential_id))


@router.post("/credentials/{credential_id}/rotate")
def rotate_credential(credential_id: str, response: Response, _user: TelegramWebAppUser | None = Depends(require_admin)):
    return _secret_response(response, _run(get_admin().credential_rotate, credential_id))


@router.patch("/credentials/{credential_id}")
def update_credential(credential_id: str, body: dict | None = Body(default=None),
                      _user: TelegramWebAppUser | None = Depends(require_admin)):
    return _ok(_run(get_admin().credential_update, credential_id, body or {}))


@router.delete("/credentials/{credential_id}")
def delete_credential(credential_id: str, _user: TelegramWebAppUser | None = Depends(require_admin)):
    return _ok(_run(get_admin().credential_delete, credential_id))


@router.get("/diagnostics")
def diagnostics(_user: TelegramWebAppUser | None = Depends(require_admin)):
    return _ok(_run(get_admin().diagnostics))


@router.post("/diagnostics/run")
def run_diagnostics(body: dict | None = Body(default=None), _user: TelegramWebAppUser | None = Depends(require_admin)):
    return _ok({"checks": _run(get_admin().run_checks, (body or {}).get("checks"))})


@router.get("/multipart")
def list_multipart(_user: TelegramWebAppUser | None = Depends(require_admin)):
    return _ok(_run(get_admin().multipart_list))


@router.post("/multipart/{upload_id}/abort")
def abort_multipart(upload_id: str, _user: TelegramWebAppUser | None = Depends(require_admin)):
    return _ok(_run(get_admin().multipart_abort, upload_id))
