from __future__ import annotations

from datetime import date as date_
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class BillingPeriod(str, Enum):
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"
    CUSTOM = "custom"


class PaymentStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


# Начальный набор категорий — подсказка для UI, не жёсткое ограничение.
# Пользователь может ввести любую свою категорию строкой.
DEFAULT_CATEGORIES = [
    "Subscriptions",
    "Infrastructure",
    "Software",
    "Services",
    "Entertainment",
    "Other",
]


class RecurringPaymentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    amount: float = Field(..., ge=0)
    currency: str = Field(default="EUR", min_length=1, max_length=8)
    billing_period: BillingPeriod = BillingPeriod.MONTHLY
    custom_interval_days: int | None = Field(default=None, ge=1)
    next_payment_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    start_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    category: str = "Other"
    status: PaymentStatus = PaymentStatus.ACTIVE

    @model_validator(mode="after")
    def _validate_custom_interval(self) -> "RecurringPaymentCreate":
        if self.billing_period == BillingPeriod.CUSTOM and not self.custom_interval_days:
            raise ValueError("custom_interval_days обязателен при billing_period = custom")
        if not self.start_date:
            self.start_date = self.next_payment_date
        return self


class RecurringPaymentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    amount: float | None = Field(default=None, ge=0)
    currency: str | None = None
    billing_period: BillingPeriod | None = None
    custom_interval_days: int | None = Field(default=None, ge=1)
    next_payment_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    start_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    category: str | None = None
    status: PaymentStatus | None = None


class RecurringPaymentResponse(BaseModel):
    id: str
    name: str
    description: str
    amount: float
    currency: str
    billing_period: BillingPeriod
    custom_interval_days: int | None = None
    next_payment_date: str
    start_date: str
    end_date: str | None = None
    category: str
    status: PaymentStatus
    days_until: int | None = None   # сколько дней до next_payment_date (может быть отрицательным)
    created_at: str
    updated_at: str

    @classmethod
    def from_db_row(cls, row: dict[str, Any]) -> "RecurringPaymentResponse":
        days_until = None
        npd = row.get("next_payment_date")
        if npd:
            try:
                delta = date_.fromisoformat(npd) - date_.today()
                days_until = delta.days
            except ValueError:
                days_until = None

        return cls(
            id=row["id"],
            name=row["name"],
            description=row.get("description", ""),
            amount=row.get("amount", 0),
            currency=row.get("currency", "EUR"),
            billing_period=row.get("billing_period", "monthly"),
            custom_interval_days=row.get("custom_interval_days"),
            next_payment_date=row.get("next_payment_date", ""),
            start_date=row.get("start_date", ""),
            end_date=row.get("end_date") or None,
            category=row.get("category", "Other"),
            status=row.get("status", "active"),
            days_until=days_until,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class FinanceSummaryCurrency(BaseModel):
    currency: str
    monthly_total: float
    yearly_total: float
    active_count: int


class FinanceSummary(BaseModel):
    active_count: int
    totals_by_currency: list[FinanceSummaryCurrency] = Field(default_factory=list)
    next_payment: RecurringPaymentResponse | None = None


class AttentionCandidate(BaseModel):
    """Сырые данные для Attention Engine. Finance НЕ считает приоритет —
    только предоставляет факты (id, срок, сумма, просрочен ли платёж)."""

    id: str
    name: str
    amount: float
    currency: str
    next_payment_date: str
    days_until: int
    overdue: bool
