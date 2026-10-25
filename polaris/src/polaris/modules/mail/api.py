"""API роутер модуля Mail (только для админа).

  GET    /api/mail/overview                    — ящики, получатели, провайдеры (одним запросом)
  POST   /api/mail/accounts                    — добавить ящик (вход проверяется сразу)
  PATCH  /api/mail/accounts/{id}               — название / новый пароль / включить-выключить
  DELETE /api/mail/accounts/{id}               — удалить ящик вместе с его письмами
  POST   /api/mail/destinations                — добавить получателя (id, группа, тема, ссылка t.me/c/…)
  PATCH  /api/mail/destinations/{id}           — название / включить-выключить
  DELETE /api/mail/destinations/{id}
  POST   /api/mail/destinations/{id}/test      — отправить тестовое сообщение
  GET    /api/mail/messages                    — письма (?account_id=&limit=&before_id=)

Пароли ящиков наружу не отдаются.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from polaris.infra.settings import Settings
from polaris.integrations.telegram.auth import TelegramWebAppUser
from polaris.modules.mail import service
from polaris.modules.mail.models import AccountCreate, AccountUpdate, DestinationCreate, DestinationUpdate
from polaris.shared.auth import require_admin

router = APIRouter(prefix="/api/mail", tags=["mail"])


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except service.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except service.MailError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/overview")
def mail_overview(_user: TelegramWebAppUser | None = Depends(require_admin)):
    return {"success": True, "data": service.overview()}


@router.post("/accounts")
def add_account(body: AccountCreate, _user: TelegramWebAppUser | None = Depends(require_admin)):
    account = _run(service.add_account, body.provider, body.email, body.password, body.label)
    return {"success": True, "data": account}


@router.patch("/accounts/{account_id}")
def update_account(account_id: int, body: AccountUpdate, _user: TelegramWebAppUser | None = Depends(require_admin)):
    account = _run(service.change_account, account_id, body.label, body.password, body.enabled)
    return {"success": True, "data": account}


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int, _user: TelegramWebAppUser | None = Depends(require_admin)):
    _run(service.remove_account, account_id)
    return {"success": True}


@router.post("/destinations")
def add_destination(body: DestinationCreate, _user: TelegramWebAppUser | None = Depends(require_admin)):
    destination = _run(service.add_destination, body.target, body.label)
    return {"success": True, "data": destination}


@router.patch("/destinations/{destination_id}")
def update_destination(
    destination_id: int, body: DestinationUpdate, _user: TelegramWebAppUser | None = Depends(require_admin)
):
    destination = _run(service.change_destination, destination_id, body.label, body.enabled)
    return {"success": True, "data": destination}


@router.delete("/destinations/{destination_id}")
def delete_destination(destination_id: int, _user: TelegramWebAppUser | None = Depends(require_admin)):
    _run(service.remove_destination, destination_id)
    return {"success": True}


@router.post("/destinations/{destination_id}/test")
def test_destination(destination_id: int, _user: TelegramWebAppUser | None = Depends(require_admin)):
    token = Settings.from_env().telegram_bot_token
    _run(service.test_destination, destination_id, token)
    return {"success": True}


@router.get("/messages")
def list_messages(
    account_id: int | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=100),
    before_id: int | None = Query(default=None),
    _user: TelegramWebAppUser | None = Depends(require_admin),
):
    return {"success": True, "data": service.list_messages(account_id, limit, before_id)}
