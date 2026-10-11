"""SQLite storage for parsed emails (Task 2). Decisions: notes/db_decisions.md."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import ParsedEmail

# schema.sql lives at the repository root (a deliverable); the code runs that same file.
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema.sql"

DATE_FORMAT = "%Y-%m-%d %H:%M:%S"  # T4: UTC, e.g. '2000-12-04 17:58:00'
RECIPIENT_FIELDS = (("to", "to_addresses"), ("cc", "cc_addresses"), ("bcc", "bcc_addresses"))

EMAIL_COLUMNS = (
    "message_id", "date", "from_address", "subject", "body", "source_file",
    "x_from", "x_to", "x_cc", "x_bcc", "x_folder", "x_origin", "content_type",
    "has_attachment", "forwarded_content", "quoted_content", "headings", "date_is_sentinel",
)
_INSERT_EMAIL = (
    f"INSERT INTO emails ({', '.join(EMAIL_COLUMNS)}) "
    f"VALUES ({', '.join('?' for _ in EMAIL_COLUMNS)}) "
    "ON CONFLICT (message_id) DO NOTHING"
)


def format_date(dt: datetime) -> str:
    """Aware datetime -> 'YYYY-MM-DD HH:MM:SS' in UTC."""
    if dt.tzinfo is None:
        raise ValueError("date must be timezone-aware")
    return dt.astimezone(timezone.utc).strftime(DATE_FORMAT)


def connect(path: str | Path) -> sqlite3.Connection:
    """Open the database with foreign keys on and explicit transaction control."""
    conn = sqlite3.connect(str(path), isolation_level=None)  # we issue BEGIN/COMMIT ourselves
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_schema(conn: sqlite3.Connection, schema_path: str | Path = SCHEMA_PATH) -> None:
    conn.executescript(Path(schema_path).read_text(encoding="utf-8"))
    conn.execute("PRAGMA foreign_keys = ON")  # executescript can reset connection state


class AddressCache:
    """address -> addresses.id, so each distinct address costs one lookup per run."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._ids: dict[str, int] = {}
        self.created = 0

    def get_or_create(self, address: str, new_in_email: list[str]) -> int:
        cached = self._ids.get(address)
        if cached is not None:
            return cached
        cur = self.conn.execute(
            "INSERT INTO addresses (address) VALUES (?) ON CONFLICT (address) DO NOTHING", (address,))
        if cur.rowcount:
            self.created += 1
            new_in_email.append(address)
            address_id = cur.lastrowid
        else:
            address_id = self.conn.execute("SELECT id FROM addresses WHERE address = ?", (address,)).fetchone()[0]
        self._ids[address] = address_id
        return address_id

    def forget(self, addresses: list[str]) -> None:
        """Drop entries whose rows were rolled back."""
        for a in addresses:
            self._ids.pop(a, None)
        self.created -= len(addresses)


def insert_email(conn: sqlite3.Connection, email: ParsedEmail, cache: AddressCache) -> bool:
    """Insert one email and its recipients atomically (a savepoint inside the caller's
    transaction). Returns False if the message_id is already in the database (T3: the
    existing row is kept). Raises sqlite3.Error on any other problem, after undoing this
    email's partial writes."""
    values = (
        email.message_id, format_date(email.date), email.from_address, email.subject, email.body,
        email.source_file, email.x_from, email.x_to, email.x_cc, email.x_bcc, email.x_folder,
        email.x_origin, email.content_type, int(bool(email.has_attachment)), email.forwarded_content,
        email.quoted_content, email.headings, int(email.date_is_sentinel),
    )
    new_addresses: list[str] = []
    conn.execute("SAVEPOINT email")
    try:
        cur = conn.execute(_INSERT_EMAIL, values)
        if cur.rowcount == 0:
            conn.execute("RELEASE email")
            return False
        email_id = cur.lastrowid
        rows = []
        for kind, attr in RECIPIENT_FIELDS:
            for position, address in enumerate(getattr(email, attr)):
                rows.append((email_id, cache.get_or_create(address, new_addresses), kind, position))
        conn.executemany(
            "INSERT INTO email_recipients (email_id, address_id, recipient_type, position) VALUES (?, ?, ?, ?)",
            rows)
    except BaseException:
        conn.execute("ROLLBACK TO email")
        conn.execute("RELEASE email")
        cache.forget(new_addresses)
        raise
    conn.execute("RELEASE email")
    return True


def message_id_exists(conn: sqlite3.Connection, message_id: str) -> bool:
    return conn.execute("SELECT 1 FROM emails WHERE message_id = ?", (message_id,)).fetchone() is not None


def get_email(conn: sqlite3.Connection, message_id: str) -> dict | None:
    """Rebuild one email as a dict, with to/cc/bcc lists in their original order."""
    row = conn.execute("SELECT * FROM emails WHERE message_id = ?", (message_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    for kind, attr in RECIPIENT_FIELDS:
        d[attr] = [r[0] for r in conn.execute(
            "SELECT a.address FROM email_recipients r JOIN addresses a ON a.id = r.address_id "
            "WHERE r.email_id = ? AND r.recipient_type = ? ORDER BY r.position", (d["id"], kind))]
    return d


def counts(conn: sqlite3.Connection) -> dict:
    c = {
        "emails": conn.execute("SELECT count(*) FROM emails").fetchone()[0],
        "addresses": conn.execute("SELECT count(*) FROM addresses").fetchone()[0],
        "recipient_rows": conn.execute("SELECT count(*) FROM email_recipients").fetchone()[0],
    }
    for kind, n in conn.execute("SELECT recipient_type, count(*) FROM email_recipients GROUP BY 1"):
        c[f"recipient_rows_{kind}"] = n
    return c


def check_database(conn: sqlite3.Connection) -> dict:
    """SQLite's own consistency checks. A healthy database gives integrity 'ok' and no
    foreign-key violations."""
    integrity = [r[0] for r in conn.execute("PRAGMA integrity_check")]
    fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    return {
        "integrity_check": "ok" if integrity == ["ok"] else integrity[:10],
        "foreign_key_violations": len(fk_violations),
        "schema_version": conn.execute("PRAGMA user_version").fetchone()[0],
    }
