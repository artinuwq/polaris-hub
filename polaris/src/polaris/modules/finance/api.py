from __future__ import annotations

"""API роутер модуля Finance (регулярные платежи и подписки).

Эндпоинты:
  GET    /api/finance/payments                 — список платежей (?status=&category=&search=&sort=)
  POST   /api/finance/payments                 — создать платёж
  GET    /api/finance/payments/{id}             — получить платёж
  PATCH  /api/finance/payments/{id}             — обновить платёж
  DELETE /api/finance/payments/{id}             — удалить платёж
  GET    /api/finance/upcoming                  — ближайшие активные платежи
  GET    /api/finance/summary                   — сводка (кол-во, суммы по валютам, ближайший платёж)
  GET    /api/finance/categories                — список категорий (дефолтные + используемые)
  GET    /api/finance/attention-candidates       — сырые данные для Attention Engine
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from polaris.integrations.telegram.auth import TelegramWebAppUser
from polaris.shared.auth import require_admin
from polaris.modules.finance.models import RecurringPaymentCreate, RecurringPaymentUpdate
from polaris.modules.finance import service

router = APIRouter(prefix="/api/finance", tags=["finance"])


@router.get("/payments")
def list_payments(
    status: str | None = Query(default=None),
    category: str | None = Query(default=None),
    search: str | None = Query(default=None),
    sort: str = Query(default="next_payment_date"),
    _user: TelegramWebAppUser | None = Depends(require_admin),
):
    payments = service.list_payments(status=status, category=category, search=search, sort=sort)
    return {
        "success": True,
        "data": {"payments": [p.model_dump() for p in payments]},
    }


@router.post("/payments")
def create_payment(body: RecurringPaymentCreate, _user: TelegramWebAppUser | None = Depends(require_admin)):
    try:
        payment = service.create_payment(body)
        return {"success": True, "data": payment.model_dump()}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/upcoming")
def upcoming_payments(
    limit: int = Query(default=5, ge=1, le=50),
    _user: TelegramWebAppUser | None = Depends(require_admin),
):
    payments = service.get_upcoming(limit=limit)
    return {"success": True, "data": {"payments": [p.model_dump() for p in payments]}}


@router.get("/summary")
def finance_summary(_user: TelegramWebAppUser | None = Depends(require_admin)):
    summary = service.get_summary()
    return {"success": True, "data": summary.model_dump()}


@router.get("/categories")
def finance_categories(_user: TelegramWebAppUser | None = Depends(require_admin)):
    return {"success": True, "data": {"categories": service.get_categories()}}


@router.get("/attention-candidates")
def attention_candidates(_user: TelegramWebAppUser | None = Depends(require_admin)):
    candidates = service.get_attention_candidates()
    return {"success": True, "data": {"candidates": [c.model_dump() for c in candidates]}}


@router.get("/payments/{payment_id}")
def get_payment(payment_id: str, _user: TelegramWebAppUser | None = Depends(require_admin)):
    payment = service.get_payment(payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    return {"success": True, "data": payment.model_dump()}


@router.patch("/payments/{payment_id}")
def update_payment(
    payment_id: str,
    body: RecurringPaymentUpdate,
    _user: TelegramWebAppUser | None = Depends(require_admin),
):
    payment = service.update_payment(payment_id, body)
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    return {"success": True, "data": payment.model_dump()}


@router.delete("/payments/{payment_id}")
def delete_payment(payment_id: str, _user: TelegramWebAppUser | None = Depends(require_admin)):
    deleted = service.delete_payment(payment_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Payment not found")
    return {"success": True, "message": "Payment deleted"}
