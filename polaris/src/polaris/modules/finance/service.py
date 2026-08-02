from __future__ import annotations

import calendar as _calendar
from datetime import date

from polaris.modules.finance import repository as repo
from polaris.modules.finance.models import (
    DEFAULT_CATEGORIES,
    AttentionCandidate,
    FinanceSummary,
    FinanceSummaryCurrency,
    RecurringPaymentCreate,
    RecurringPaymentResponse,
    RecurringPaymentUpdate,
)

# Средняя длина месяца — используется только для нормализации периодов
# при подсчёте месячного/годового эквивалента (не хранится и не влияет
# на сами даты платежей).
_AVG_MONTH_DAYS = 30.4368


def _add_period(d: date, period: str, custom_days: int | None) -> date:
    """Прибавить один расчётный период к дате, корректно обрабатывая
    границы месяца (например 31 января -> 28/29 февраля, а не ошибку)."""
    if period == "weekly":
        from datetime import timedelta
        return d + timedelta(days=7)

    if period == "custom":
        from datetime import timedelta
        return d + timedelta(days=custom_days or 30)

    months_to_add = {"monthly": 1, "quarterly": 3, "yearly": 12}.get(period, 1)
    total_month_index = (d.month - 1) + months_to_add
    new_year = d.year + total_month_index // 12
    new_month = total_month_index % 12 + 1
    last_day_of_new_month = _calendar.monthrange(new_year, new_month)[1]
    new_day = min(d.day, last_day_of_new_month)
    return date(new_year, new_month, new_day)


def _monthly_equivalent(amount: float, period: str, custom_days: int | None) -> float:
    """Нормализовать сумму платежа к эквиваленту 'в месяц' для сводки."""
    if period == "weekly":
        return amount * (_AVG_MONTH_DAYS / 7)
    if period == "monthly":
        return amount
    if period == "quarterly":
        return amount / 3
    if period == "yearly":
        return amount / 12
    if period == "custom":
        days = custom_days or 30
        return amount * (_AVG_MONTH_DAYS / days)
    return amount


def _ensure_current(row: dict) -> dict:
    """Если next_payment_date уже в прошлом, продвинуть его вперёд до
    ближайшей будущей даты (без создания новой сущности). Если у платежа
    есть end_date и он больше не укладывается в срок — пометить expired.
    Изменения сразу сохраняются в БД."""
    if row.get("status") != "active":
        return row

    npd_str = row.get("next_payment_date") or ""
    try:
        npd = date.fromisoformat(npd_str)
    except ValueError:
        return row

    today = date.today()
    if npd >= today:
        return row

    end_date_str = row.get("end_date")
    end_date_val = None
    if end_date_str:
        try:
            end_date_val = date.fromisoformat(end_date_str)
        except ValueError:
            end_date_val = None

    status = row.get("status", "active")
    period = row.get("billing_period", "monthly")
    custom_days = row.get("custom_interval_days")

    guard = 0
    while npd < today and guard < 2000:
        guard += 1
        if end_date_val and npd >= end_date_val:
            status = "expired"
            break
        npd = _add_period(npd, period, custom_days)
        if end_date_val and npd > end_date_val:
            status = "expired"
            break

    updated = repo.update_payment(row["id"], {
        "next_payment_date": npd.isoformat(),
        "status": status,
    })
    return updated or row


def _dump(row: dict) -> RecurringPaymentResponse:
    return RecurringPaymentResponse.from_db_row(_ensure_current(row))


def ensure_all_current() -> None:
    """Продвинуть next_payment_date всех активных платежей, у которых дата
    уже в прошлом. Побочный эффект: сохраняет изменения в БД. Вызывается
    другими модулями (например Calendar) перед чтением, чтобы не хранить
    свою копию логики повторения платежей."""
    for row in repo.get_all_payments():
        _ensure_current(row)


def list_payments(
    status: str | None = None,
    category: str | None = None,
    search: str | None = None,
    sort: str = "next_payment_date",
) -> list[RecurringPaymentResponse]:
    rows = [_ensure_current(r) for r in repo.get_all_payments()]

    if status:
        rows = [r for r in rows if r.get("status") == status]
    if category:
        rows = [r for r in rows if r.get("category") == category]
    if search:
        needle = search.strip().lower()
        rows = [
            r for r in rows
            if needle in (r.get("name", "").lower())
            or needle in (r.get("description", "").lower())
        ]

    sort_keys = {
        "next_payment_date": lambda r: r.get("next_payment_date") or "9999-99-99",
        "name": lambda r: (r.get("name") or "").lower(),
        "amount": lambda r: r.get("amount") or 0,
    }
    rows.sort(key=sort_keys.get(sort, sort_keys["next_payment_date"]))

    return [RecurringPaymentResponse.from_db_row(r) for r in rows]


def get_payment(payment_id: str) -> RecurringPaymentResponse | None:
    row = repo.get_payment_by_id(payment_id)
    if not row:
        return None
    return _dump(row)


def create_payment(data: RecurringPaymentCreate) -> RecurringPaymentResponse:
    payload = data.model_dump(mode="json")
    row = repo.create_payment(payload)
    return RecurringPaymentResponse.from_db_row(row)


def update_payment(payment_id: str, data: RecurringPaymentUpdate) -> RecurringPaymentResponse | None:
    payload = data.model_dump(mode="json", exclude_none=True)
    row = repo.update_payment(payment_id, payload)
    if not row:
        return None
    return _dump(row)


def delete_payment(payment_id: str) -> bool:
    return repo.delete_payment(payment_id)


def get_upcoming(limit: int = 5) -> list[RecurringPaymentResponse]:
    return list_payments(status="active")[:limit]


def get_categories() -> list[str]:
    used = {r.get("category") for r in repo.get_all_payments() if r.get("category")}
    ordered = list(DEFAULT_CATEGORIES)
    for cat in sorted(used):
        if cat not in ordered:
            ordered.append(cat)
    return ordered


def get_summary() -> FinanceSummary:
    rows = [_ensure_current(r) for r in repo.get_all_payments()]
    active_rows = [r for r in rows if r.get("status") == "active"]

    by_currency: dict[str, dict[str, float]] = {}
    for r in active_rows:
        currency = r.get("currency", "EUR")
        monthly = _monthly_equivalent(
            r.get("amount", 0), r.get("billing_period", "monthly"), r.get("custom_interval_days")
        )
        bucket = by_currency.setdefault(currency, {"monthly": 0.0, "count": 0})
        bucket["monthly"] += monthly
        bucket["count"] += 1

    totals = [
        FinanceSummaryCurrency(
            currency=cur,
            monthly_total=round(vals["monthly"], 2),
            yearly_total=round(vals["monthly"] * 12, 2),
            active_count=int(vals["count"]),
        )
        for cur, vals in sorted(by_currency.items())
    ]

    next_payment = None
    if active_rows:
        soonest = min(active_rows, key=lambda r: r.get("next_payment_date") or "9999-99-99")
        next_payment = RecurringPaymentResponse.from_db_row(soonest)

    return FinanceSummary(
        active_count=len(active_rows),
        totals_by_currency=totals,
        next_payment=next_payment,
    )


def get_attention_candidates() -> list[AttentionCandidate]:
    """Сырые данные для будущего Attention Engine. Finance НЕ решает,
    что важно — только предоставляет факты по каждому активному платежу."""
    rows = [_ensure_current(r) for r in repo.get_all_payments()]
    today = date.today()
    candidates: list[AttentionCandidate] = []

    for r in rows:
        if r.get("status") != "active":
            continue
        npd_str = r.get("next_payment_date") or ""
        try:
            npd = date.fromisoformat(npd_str)
        except ValueError:
            continue
        days_until = (npd - today).days
        candidates.append(AttentionCandidate(
            id=r["id"],
            name=r.get("name", ""),
            amount=r.get("amount", 0),
            currency=r.get("currency", "EUR"),
            next_payment_date=npd_str,
            days_until=days_until,
            overdue=days_until < 0,
        ))

    candidates.sort(key=lambda c: c.days_until)
    return candidates
