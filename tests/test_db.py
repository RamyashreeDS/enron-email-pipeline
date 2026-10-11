import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from enron_pipeline import db
from enron_pipeline.models import ParsedEmail


def make_email(n=1, **overrides):
    e = ParsedEmail(
        message_id=f"<{n}.JavaMail.evans@thyme>",
        date=datetime(2000, 12, 4, 17, 58, tzinfo=timezone.utc),
        from_address="ken.lay@enron.com",
        to_addresses=["jeff.skilling@enron.com", "louise.kitchen@enron.com"],
        subject="Re: Q4 plan",
        body="See below.",
        source_file=f"maildir/lay-k/inbox/{n}.",
        cc_addresses=["sherri.sera@enron.com"],
        bcc_addresses=["sherri.sera@enron.com"],
        x_from="Kenneth Lay",
        x_folder="\\Lay, Kenneth\\Inbox",
        content_type="text/plain",
        has_attachment=True,
        headings="AGENDA\nNext Steps",
    )
    return replace(e, **overrides)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    db.create_schema(c)
    yield c
    c.close()


def insert(conn, email, cache=None):
    cache = cache or db.AddressCache(conn)
    conn.execute("BEGIN")
    try:
        result = db.insert_email(conn, email, cache)
    except Exception:
        conn.execute("COMMIT")
        raise
    conn.execute("COMMIT")
    return result


def test_schema_tables_indexes_and_version(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'index')")}
    assert {"emails", "addresses", "email_recipients"} <= names
    assert {"idx_emails_date", "idx_emails_from_address", "idx_emails_subject",
            "idx_email_recipients_address"} <= names
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_schema_can_be_applied_twice(conn):
    db.create_schema(conn)  # IF NOT EXISTS everywhere


def test_spec_columns_present(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(emails)")}
    assert {"is_duplicate", "duplicate_of", "notification_sent", "notification_date",
            "date_is_sentinel"} <= cols
    # T7: no extras beyond the spec and date_is_sentinel
    assert not cols & {"mailbox", "split_marker", "leaked_headers_stripped", "to_addresses"}


def test_insert_and_round_trip(conn):
    e = make_email(to_addresses=["b@enron.com", "a@enron.com", "c@enron.com"])  # order matters
    assert insert(conn, e) is True
    got = db.get_email(conn, e.message_id)
    assert got["date"] == "2000-12-04 17:58:00"
    assert got["to_addresses"] == ["b@enron.com", "a@enron.com", "c@enron.com"]
    assert got["cc_addresses"] == ["sherri.sera@enron.com"]
    assert got["bcc_addresses"] == ["sherri.sera@enron.com"]
    assert got["has_attachment"] == 1 and got["date_is_sentinel"] == 0
    assert got["is_duplicate"] == 0 and got["duplicate_of"] is None
    assert got["notification_sent"] == 0 and got["notification_date"] is None
    assert got["headings"] == "AGENDA\nNext Steps"


def test_dates_are_stored_in_utc(conn):
    pst = timezone(timedelta(hours=-8))
    insert(conn, make_email(date=datetime(2000, 12, 31, 20, 30, tzinfo=pst)))
    assert conn.execute("SELECT date FROM emails").fetchone()[0] == "2001-01-01 04:30:00"


def test_naive_date_is_refused():
    with pytest.raises(ValueError):
        db.format_date(datetime(2000, 1, 1))


def test_duplicate_message_id_is_skipped_and_original_kept(conn):
    assert insert(conn, make_email(1, subject="first")) is True
    again = make_email(1, subject="second", source_file="maildir/lay-k/inbox/999.")
    assert insert(conn, again) is False
    rows = conn.execute("SELECT subject, source_file FROM emails").fetchall()
    assert [tuple(r) for r in rows] == [("first", "maildir/lay-k/inbox/1.")]
    assert db.counts(conn)["recipient_rows"] == 4  # the skipped copy added no recipients


def test_unique_message_id_is_enforced_by_the_database(conn):
    insert(conn, make_email(1))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO emails (message_id, date, from_address, subject, body, source_file) "
                     "VALUES (?, '2001-01-01 00:00:00', 'a@b.com', '', '', 'x')", ("<1.JavaMail.evans@thyme>",))


def test_same_source_file_with_new_message_id_is_an_error(conn):
    insert(conn, make_email(1))
    with pytest.raises(sqlite3.IntegrityError):
        insert(conn, make_email(2, source_file="maildir/lay-k/inbox/1."))


def test_addresses_are_shared_across_emails(conn):
    cache = db.AddressCache(conn)
    for n in range(1, 4):
        insert(conn, make_email(n), cache)
    c = db.counts(conn)
    assert c["emails"] == 3 and c["addresses"] == 3  # jeff, louise, sherri
    assert c["recipient_rows"] == 12 and cache.created == 3


def test_same_address_in_to_and_cc_is_allowed(conn):
    insert(conn, make_email(to_addresses=["x@enron.com"], cc_addresses=["x@enron.com"], bcc_addresses=[]))
    assert db.counts(conn)["recipient_rows"] == 2


def test_failed_email_is_rolled_back_completely(conn):
    cache = db.AddressCache(conn)
    bad = make_email(1, to_addresses=["NEW@Enron.com"])  # uppercase violates the address CHECK
    with pytest.raises(sqlite3.IntegrityError):
        insert(conn, bad, cache)
    assert db.counts(conn) == {"emails": 0, "addresses": 0, "recipient_rows": 0}
    assert cache.created == 0
    # The cache must not keep ids of rolled-back rows.
    assert insert(conn, make_email(2), cache) is True
    assert db.check_database(conn)["foreign_key_violations"] == 0


@pytest.mark.parametrize("sql", [
    "UPDATE emails SET is_duplicate = 1",                         # duplicate_of missing
    "UPDATE emails SET duplicate_of = message_id, is_duplicate = 1",  # duplicate of itself
    "UPDATE emails SET duplicate_of = '<2.JavaMail.evans@thyme>'",   # duplicate_of without the flag
    "UPDATE emails SET notification_sent = 1",                    # notification_date missing
    "UPDATE emails SET has_attachment = 2",
    "UPDATE emails SET date = '12/04/2000'",                      # wrong date format
    "UPDATE emails SET notification_sent = 1, notification_date = '2026-10-10T12:00:00Z'",
])
def test_check_constraints(conn, sql):
    insert(conn, make_email(1))
    insert(conn, make_email(2))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql + " WHERE id = 1")


def test_valid_duplicate_and_notification_updates(conn):
    insert(conn, make_email(1))
    insert(conn, make_email(2))
    conn.execute("UPDATE emails SET is_duplicate = 1, duplicate_of = '<1.JavaMail.evans@thyme>', "
                 "notification_sent = 1, notification_date = '2026-10-10 12:00:00' WHERE id = 2")
    assert db.check_database(conn)["foreign_key_violations"] == 0


def test_duplicate_of_must_reference_an_existing_message_id(conn):
    insert(conn, make_email(1))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE emails SET is_duplicate = 1, duplicate_of = '<nope@x>' WHERE id = 1")


def test_deleting_an_email_deletes_its_recipients(conn):
    insert(conn, make_email(1))
    conn.execute("DELETE FROM emails WHERE id = 1")
    assert db.counts(conn)["recipient_rows"] == 0


def test_indexes_are_used(conn):
    insert(conn, make_email(1))
    def plan(sql):
        return " ".join(r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + sql))
    assert "idx_emails_date" in plan("SELECT * FROM emails WHERE date >= '2001-12-01' AND date < '2001-12-08'")
    assert "idx_emails_from_address" in plan("SELECT * FROM emails WHERE from_address = 'a@b.com'")
    assert "idx_emails_subject" in plan("SELECT * FROM emails WHERE subject = 'x'")


def test_check_database_on_healthy_db(conn):
    insert(conn, make_email(1))
    assert db.check_database(conn) == {"integrity_check": "ok", "foreign_key_violations": 0,
                                       "schema_version": 1}
