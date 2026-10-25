-- Mail: сбор почты по IMAP (mail.ru / Google), хранение писем и пересылка в Telegram.
-- Migration 006. Все таблицы идемпотентны (CREATE IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS mail_accounts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    provider        TEXT NOT NULL,                       -- mailru | gmail
    email           TEXT NOT NULL COLLATE NOCASE UNIQUE,
    label           TEXT NOT NULL DEFAULT '',
    password        TEXT NOT NULL,                       -- пароль приложения; наружу через API не отдаётся
    enabled         INTEGER NOT NULL DEFAULT 1,
    last_status     TEXT NOT NULL DEFAULT 'pending',     -- pending | ok | error
    last_error      TEXT NOT NULL DEFAULT '',
    last_checked_at TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- Позиция чтения по ящику: до какого UID письма уже обработаны.
CREATE TABLE IF NOT EXISTS mail_account_state (
    account_id  INTEGER PRIMARY KEY REFERENCES mail_accounts(id) ON DELETE CASCADE,
    uidvalidity TEXT NOT NULL,
    last_uid    INTEGER NOT NULL
);

-- Куда слать уведомления: личка, группа или тема группы.
CREATE TABLE IF NOT EXISTS mail_destinations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    label       TEXT NOT NULL DEFAULT '',
    raw         TEXT NOT NULL,                           -- как ввёл пользователь
    chat_id     TEXT NOT NULL,                           -- как уходит в Telegram: 123, -100..., @name
    thread_id   INTEGER,                                 -- message_thread_id темы, если есть
    kind        TEXT NOT NULL,                           -- user | group | topic | channel
    enabled     INTEGER NOT NULL DEFAULT 1,
    last_status TEXT NOT NULL DEFAULT 'pending',         -- pending | ok | error
    last_error  TEXT NOT NULL DEFAULT '',
    last_sent_at TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mail_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  INTEGER NOT NULL REFERENCES mail_accounts(id) ON DELETE CASCADE,
    uidvalidity TEXT NOT NULL,
    uid         INTEGER NOT NULL,
    subject     TEXT NOT NULL DEFAULT '',
    sender      TEXT NOT NULL DEFAULT '',
    recipient   TEXT NOT NULL DEFAULT '',
    date_text   TEXT NOT NULL DEFAULT '',
    received_at TEXT NOT NULL,                           -- когда письмо попало в Polaris (UTC)
    body        TEXT NOT NULL DEFAULT '',
    attachments TEXT NOT NULL DEFAULT '[]',              -- JSON-список имён файлов
    UNIQUE(account_id, uidvalidity, uid)
);
CREATE INDEX IF NOT EXISTS idx_mail_messages_account ON mail_messages(account_id, id DESC);

-- Очередь доставки: одно письмо × одно назначение.
CREATE TABLE IF NOT EXISTS mail_deliveries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id      INTEGER NOT NULL REFERENCES mail_messages(id) ON DELETE CASCADE,
    destination_id  INTEGER NOT NULL REFERENCES mail_destinations(id) ON DELETE CASCADE,
    status          TEXT NOT NULL DEFAULT 'pending',     -- pending | sent | failed
    attempts        INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    last_error      TEXT NOT NULL DEFAULT '',
    sent_at         TEXT,
    UNIQUE(message_id, destination_id)
);
CREATE INDEX IF NOT EXISTS idx_mail_deliveries_due ON mail_deliveries(status, next_attempt_at);
