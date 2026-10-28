-- Athena API module tables
-- Migration 007: Create tables for athena module (focus, imprints, server_status, finance, email)

-- Focus/Attention state
CREATE TABLE IF NOT EXISTS focus_states (
    id              TEXT PRIMARY KEY,           -- UUID v4
    current_focus   TEXT,
    context         TEXT,
    since           TEXT NOT NULL,              -- ISO datetime
    previous        TEXT NOT NULL DEFAULT '[]', -- JSON array of strings
    is_active       INTEGER NOT NULL DEFAULT 0, -- 0 or 1
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_focus_states_active ON focus_states(is_active);

-- Imprints/Inbox entries
CREATE TABLE IF NOT EXISTS imprints (
    id              TEXT PRIMARY KEY,           -- UUID v4
    type            TEXT NOT NULL,              -- imprint|task_inbox|work_note
    content         TEXT NOT NULL,
    source          TEXT,                       -- optional source identifier
    tags            TEXT NOT NULL DEFAULT '[]', -- JSON array of strings
    created_at      TEXT NOT NULL,              -- ISO datetime
    updated_at      TEXT NOT NULL               -- ISO datetime
);

CREATE INDEX IF NOT EXISTS idx_imprints_type ON imprints(type);
CREATE INDEX IF NOT EXISTS idx_imprints_created ON imprints(created_at);

-- Server status (current snapshot)
CREATE TABLE IF NOT EXISTS server_status (
    server_id       TEXT PRIMARY KEY REFERENCES servers(id) ON DELETE CASCADE,
    online          INTEGER NOT NULL DEFAULT 0, -- 0 or 1
    last_check      TEXT,                       -- ISO datetime
    metrics         TEXT NOT NULL DEFAULT '{}', -- JSON metrics snapshot
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- Finance: Balances
CREATE TABLE IF NOT EXISTS balances (
    id              TEXT PRIMARY KEY,           -- UUID v4
    account         TEXT NOT NULL UNIQUE,
    currency        TEXT NOT NULL DEFAULT 'EUR',
    amount          INTEGER NOT NULL DEFAULT 0, -- in minor units (cents)
    as_of           TEXT NOT NULL,              -- ISO datetime
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_balances_account ON balances(account);

-- Finance: Transactions
CREATE TABLE IF NOT EXISTS transactions (
    id              TEXT PRIMARY KEY,           -- UUID v4
    date            TEXT NOT NULL,              -- ISO datetime
    account         TEXT NOT NULL,
    amount          INTEGER NOT NULL,           -- in minor units (cents), negative = expense
    currency        TEXT NOT NULL DEFAULT 'EUR',
    category        TEXT,                       -- optional category
    description     TEXT,                       -- optional description
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_transactions_account_date ON transactions(account, date);
CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(date);

-- Email: Messages
CREATE TABLE IF NOT EXISTS emails (
    id              TEXT PRIMARY KEY,           -- UUID v4 or message ID
    from_addr       TEXT NOT NULL,              -- sender email
    subject         TEXT NOT NULL,
    snippet         TEXT NOT NULL DEFAULT '',   -- preview text
    received_at     TEXT NOT NULL,              -- ISO datetime
    is_read         INTEGER NOT NULL DEFAULT 0, -- 0 or 1
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_emails_received ON emails(received_at);
CREATE INDEX IF NOT EXISTS idx_emails_read ON emails(is_read);