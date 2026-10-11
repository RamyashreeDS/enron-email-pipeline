"""Loader end to end on a small maildir built from the real-email fixtures."""
import json
import shutil
from pathlib import Path

import pytest

from enron_pipeline import db, load
from enron_pipeline.parser import parse_file

FIXTURES = Path(__file__).parent / "fixtures" / "enron"


@pytest.fixture
def maildir(tmp_path):
    """A maildir tree with every fixture under its original path."""
    root = tmp_path / "maildir"
    for f in FIXTURES.iterdir():
        rel = f.stem.replace("__", "/") + "."
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(f, dest)
    return root


def run(tmp_path, root, **kw):
    out = tmp_path / "out"
    return load.load(root, tmp_path / "enron.db", error_log_path=out / "error_log.txt",
                     warnings_path=out / "w.txt", stats_path=out / "s.json",
                     load_stats_path=out / "load.json", **kw)


def error_lines(tmp_path):
    return (tmp_path / "out" / "error_log.txt").read_text().splitlines()[1:]


def test_first_load(tmp_path, maildir):
    stats, ls = run(tmp_path, maildir)
    assert stats.found == 14 and stats.parsed == 11  # 3 fixtures are designed to fail parsing
    assert ls.inserted == 11 and ls.handled == 11
    assert ls.already_in_db == ls.duplicate_in_run == ls.db_errors == 0
    assert ls.database["emails"] == 11
    assert ls.checks == {"integrity_check": "ok", "foreign_key_violations": 0, "schema_version": 1}
    assert len(error_lines(tmp_path)) == 3  # parse failures only
    saved = json.loads((tmp_path / "out" / "load.json").read_text())
    assert saved["inserted"] == 11


def test_second_run_skips_everything_and_reports_a_count(tmp_path, maildir):
    run(tmp_path, maildir)
    conn = db.connect(tmp_path / "enron.db")
    conn.execute("UPDATE emails SET notification_sent = 1, notification_date = '2026-10-10 12:00:00' "
                 "WHERE id = 1")
    before = db.counts(conn)
    conn.close()

    stats, ls = run(tmp_path, maildir)
    assert ls.inserted == 0 and ls.already_in_db == 11 and ls.handled == stats.parsed
    assert ls.addresses_created == 0 and sum(ls.recipient_rows.values()) == 0
    assert ls.database == before
    # Skips are a count, not 11 log lines: the log holds only the 3 parse failures.
    assert len(error_lines(tmp_path)) == 3
    assert not any("DUPLICATE_MESSAGE_ID" in l for l in error_lines(tmp_path))
    # Task 4 state survives the rerun.
    conn = db.connect(tmp_path / "enron.db")
    assert conn.execute("SELECT notification_sent FROM emails WHERE id = 1").fetchone()[0] == 1


def test_duplicate_message_id_within_one_run_is_logged(tmp_path, maildir):
    src = maildir / "skilling-j" / "notes_inbox" / "36."
    copy = maildir / "skilling-j" / "notes_inbox" / "9999."
    shutil.copy(src, copy)
    stats, ls = run(tmp_path, maildir)
    assert ls.inserted == 11 and ls.duplicate_in_run == 1 and ls.handled == stats.parsed
    dup = [l for l in error_lines(tmp_path) if "\tDUPLICATE_MESSAGE_ID\t" in l]
    assert len(dup) == 1 and "maildir/skilling-j/notes_inbox/9999." in dup[0]
    assert "kept that copy" in dup[0]


def test_new_files_on_rerun_are_added(tmp_path, maildir):
    run(tmp_path, maildir, mailboxes=["skilling-j"])
    stats, ls = run(tmp_path, maildir)
    # 8 skilling-j fixtures, 2 of which fail parsing: 6 loaded first, the other 5 added now.
    assert ls.already_in_db == 6 and ls.inserted == 5


def test_round_trip_matches_parser(tmp_path, maildir):
    run(tmp_path, maildir)
    conn = db.connect(tmp_path / "enron.db")
    for f in sorted(maildir.rglob("*.")):
        source_file = f.relative_to(maildir.parent).as_posix()
        try:
            e = parse_file(f, source_file)
        except Exception:
            continue
        got = db.get_email(conn, e.message_id)
        assert got is not None, source_file
        for name in ("message_id", "from_address", "subject", "body", "source_file", "x_from", "x_to",
                     "x_cc", "x_bcc", "x_folder", "x_origin", "content_type", "forwarded_content",
                     "quoted_content", "headings", "to_addresses", "cc_addresses", "bcc_addresses"):
            assert got[name] == getattr(e, name), (source_file, name)
        assert got["date"] == db.format_date(e.date)
        assert got["has_attachment"] == int(e.has_attachment)
        assert got["date_is_sentinel"] == int(e.date_is_sentinel)


def test_db_error_on_one_email_does_not_stop_the_load(tmp_path, maildir, monkeypatch):
    real = db.insert_email

    def flaky(conn, email, cache):
        if email.source_file.endswith("notes_inbox/36."):
            raise RuntimeError("simulated failure")
        return real(conn, email, cache)

    monkeypatch.setattr(db, "insert_email", flaky)
    stats, ls = run(tmp_path, maildir)
    assert ls.db_errors == 1 and ls.inserted == 10 and ls.handled == stats.parsed
    assert any("\tDB_ERROR\tRuntimeError: simulated failure" in l for l in error_lines(tmp_path))


def test_small_batches_commit_correctly(tmp_path, maildir):
    _, ls = run(tmp_path, maildir, batch_size=2)
    assert ls.database["emails"] == 11 and ls.checks["integrity_check"] == "ok"
