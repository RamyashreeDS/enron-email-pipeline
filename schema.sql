-- Enron email database schema (SQLite).
--
-- Decisions are recorded in notes/db_decisions.md. In short:
--   * emails           one row per parsed email; from_address is a text column (T1)
--   * addresses        one row per distinct recipient address
--   * email_recipients linking table: which address received which email as to/cc/bcc
--   * dates are UTC text 'YYYY-MM-DD HH:MM:SS' (T4)
--   * duplicate_of holds the original email's message_id (T5)
--
-- Safe to run more than once: every statement uses IF NOT EXISTS.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS emails (
    id                 INTEGER PRIMARY KEY,
    -- Mandatory fields (spec 3.1)
    message_id         TEXT    NOT NULL UNIQUE,
    date               TEXT    NOT NULL
                       CHECK (date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]:[0-9][0-9]'),
    from_address       TEXT    NOT NULL CHECK (from_address <> ''),
    subject            TEXT    NOT NULL,          -- may be '' (empty Subject header)
    body               TEXT    NOT NULL,          -- may be '' (pure forward or quote)
    source_file        TEXT    NOT NULL UNIQUE,   -- e.g. maildir/lay-k/inbox/45.
    -- Optional fields (spec 3.2); to/cc/bcc lists live in email_recipients
    x_from             TEXT,
    x_to               TEXT,
    x_cc               TEXT,
    x_bcc              TEXT,
    x_folder           TEXT,
    x_origin           TEXT,
    content_type       TEXT,
    has_attachment     INTEGER NOT NULL DEFAULT 0 CHECK (has_attachment IN (0, 1)),
    forwarded_content  TEXT,
    quoted_content     TEXT,
    headings           TEXT,                      -- one heading per line (T6)
    -- Lotus Notes null date (1980-01-01 UTC); Task 3 must not pick it as the original (T7)
    date_is_sentinel   INTEGER NOT NULL DEFAULT 0 CHECK (date_is_sentinel IN (0, 1)),
    -- Duplicate detection (Task 3)
    is_duplicate       INTEGER NOT NULL DEFAULT 0 CHECK (is_duplicate IN (0, 1)),
    duplicate_of       TEXT REFERENCES emails (message_id),
    -- Notifications (Task 4)
    notification_sent  INTEGER NOT NULL DEFAULT 0 CHECK (notification_sent IN (0, 1)),
    notification_date  TEXT
                       CHECK (notification_date IS NULL OR notification_date GLOB
                              '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]:[0-9][0-9]'),

    CHECK ((is_duplicate = 0 AND duplicate_of IS NULL) OR (is_duplicate = 1 AND duplicate_of IS NOT NULL)),
    CHECK (duplicate_of IS NULL OR duplicate_of <> message_id),
    CHECK (notification_sent = 0 OR notification_date IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS addresses (
    id       INTEGER PRIMARY KEY,
    address  TEXT NOT NULL UNIQUE CHECK (address <> '' AND address = lower(address))
);

CREATE TABLE IF NOT EXISTS email_recipients (
    email_id        INTEGER NOT NULL REFERENCES emails (id) ON DELETE CASCADE,
    address_id      INTEGER NOT NULL REFERENCES addresses (id),
    recipient_type  TEXT    NOT NULL CHECK (recipient_type IN ('to', 'cc', 'bcc')),
    position        INTEGER NOT NULL CHECK (position >= 0),  -- order within the original list
    PRIMARY KEY (email_id, recipient_type, address_id),
    UNIQUE (email_id, recipient_type, position)
);

-- Indexes required by the spec (item 9)
CREATE INDEX IF NOT EXISTS idx_emails_date         ON emails (date);
CREATE INDEX IF NOT EXISTS idx_emails_from_address ON emails (from_address);
CREATE INDEX IF NOT EXISTS idx_emails_subject      ON emails (subject);

-- "All emails received by address X"; lookups by email use the primary key.
CREATE INDEX IF NOT EXISTS idx_email_recipients_address ON email_recipients (address_id);

PRAGMA user_version = 1;
