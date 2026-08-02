-- Finance module: RecurringPayment
-- Migration 003: единая сущность для регулярных платежей и подписок.
-- Finance не хранит расходы/доходы/бюджеты — только регулярные обязательства.

CREATE TABLE IF NOT EXISTS recurring_payments (
    id                    TEXT PRIMARY KEY,          -- UUID v4
    name                  TEXT NOT NULL,
    description           TEXT NOT NULL DEFAULT '',
    amount                REAL NOT NULL DEFAULT 0,
    currency              TEXT NOT NULL DEFAULT 'EUR',
    billing_period        TEXT NOT NULL DEFAULT 'monthly',  -- weekly|monthly|quarterly|yearly|custom
    custom_interval_days  INTEGER,                    -- только для billing_period = custom
    next_payment_date     TEXT NOT NULL,              -- YYYY-MM-DD
    start_date            TEXT NOT NULL,              -- YYYY-MM-DD
    end_date              TEXT,                       -- YYYY-MM-DD, optional
    category              TEXT NOT NULL DEFAULT 'Other',
    status                TEXT NOT NULL DEFAULT 'active',   -- active|paused|cancelled|expired
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_recurring_payments_next_date ON recurring_payments(next_payment_date);
CREATE INDEX IF NOT EXISTS idx_recurring_payments_status ON recurring_payments(status);
