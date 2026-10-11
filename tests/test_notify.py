"""Task 4: drafts, guards and live sending. No real email is ever sent: live mode is
tested with an in-process fake sender and, over the real MCP protocol, with
tests/fake_gmail_mcp_server.py."""
import csv
import email
import json
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from enron_pipeline import db, dedupe, notify
from enron_pipeline.mcp_client import SendResult, interpret, load_server_config
from enron_pipeline.models import ParsedEmail

OVERRIDE = "pipeline.tester@gmail.com"
NOW = datetime(2026, 10, 11, 9, 30, tzinfo=timezone.utc)
FAKE_SERVER = Path(__file__).parent / "fake_gmail_mcp_server.py"


def parsed(n, when, subject="Schedule", sender="ken.lay@enron.com", body="Please review the schedule.",
           folder="inbox"):
    return ParsedEmail(message_id=f"<{n}.JavaMail.evans@thyme>", date=when, from_address=sender,
                       to_addresses=["jeff.skilling@enron.com"], subject=subject, body=body,
                       source_file=f"maildir/lay-k/{folder}/{n}.", has_attachment=False)


def t(day, hour=9):
    return datetime(2001, 3, day, hour, 0, tzinfo=timezone.utc)


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.delenv(notify.OVERRIDE_VAR, raising=False)  # never pick up the developer's real address
    path = tmp_path / "enron.db"
    conn = db.connect(path)
    db.create_schema(conn)
    cache = db.AddressCache(conn)
    conn.execute("BEGIN")
    emails = [
        # G1: folder copies (same timestamp)
        parsed(1, t(1), folder="inbox"), parsed(2, t(1), folder="all_documents"), parsed(3, t(1), folder="sent"),
        # G2: re-send a week later, by a different sender
        parsed(10, t(2), subject="Staffing", sender="liz.webb@enron.com", body="Any new employees next week?"),
        parsed(11, t(9), subject="Staffing", sender="liz.webb@enron.com", body="Any new employees next week?!"),
        # G3: original subject already starts with "Re:"; the latest copy is a tie at t(20) (highest id wins)
        parsed(20, t(15), subject="Re: Plan", sender="sally.beck@enron.com", body="Agreed, see you Monday."),
        parsed(21, t(20), subject="RE: Plan", sender="sally.beck@enron.com", body="Agreed, see you Monday."),
        parsed(22, t(20), subject="RE: Plan", sender="sally.beck@enron.com", body="Agreed, see you Monday."),
        # not a duplicate
        parsed(30, t(5), subject="Budget", body="Numbers attached."),
    ]
    for e in emails:
        db.insert_email(conn, e, cache)
    conn.execute("COMMIT")
    conn.close()
    dedupe.run(path, tmp_path / "dup.csv", None)
    return path


@pytest.fixture
def conn(database):
    c = db.connect(database)
    yield c
    c.close()


def mid(n):
    return f"<{n}.JavaMail.evans@thyme>"


def notified(conn):
    """message_ids with notification_sent = 1, in id order."""
    return [r[0] for r in conn.execute("SELECT message_id FROM emails WHERE notification_sent = 1 ORDER BY id")]


def group_by_target(groups, n):
    return next(g for g in groups if g.target["message_id"] == f"<{n}.JavaMail.evans@thyme>")


# --- groups and selection -------------------------------------------------------------

def test_groups_targets_and_categories(conn):
    groups = notify.load_groups(conn)
    assert len(groups) == 3
    g1, g2, g3 = group_by_target(groups, 3), group_by_target(groups, 11), group_by_target(groups, 22)
    assert g1.original["message_id"] == mid(1) and g1.category == dedupe.SAME_TIMESTAMP
    assert g2.category == dedupe.DIFFERENT_TIMESTAMPS
    assert g2.similarity == pytest.approx(dedupe.similarity(notify._candidate(g2.original),
                                                            notify._candidate(g2.target))[0])
    assert g3.target["message_id"] == mid(22)  # tie at the latest time (21 and 22): highest id
    assert g3.target["id"] > next(d["id"] for d in g3.duplicates if d["message_id"] == mid(21))


def test_select_live_most_recent_different_timestamp_groups(conn):
    groups = notify.load_groups(conn)
    selected, skipped = notify.select_live(groups, 5)
    assert [g.target["message_id"] for g in selected] == ["<22.JavaMail.evans@thyme>", "<11.JavaMail.evans@thyme>"]
    assert skipped == 0
    assert len(notify.select_live(groups, 1)[0]) == 1


def test_select_live_puts_sentinel_targets_last(tmp_path, conn):
    # Real case (tori.wells "Update from cousins in Missouri"): the original is real-dated and a
    # sentinel copy (1980-01-01) is the latest; it must not count as the "most recent" group.
    conn.execute("BEGIN")
    cache = db.AddressCache(conn)
    db.insert_email(conn, parsed(40, t(28), subject="Cousins", sender="tori.wells@enron.com", body="Update from Missouri."), cache)
    s = parsed(41, datetime(1980, 1, 1, tzinfo=timezone.utc), subject="Cousins", sender="tori.wells@enron.com",
               body="Update from Missouri.")
    s.date_is_sentinel = True
    db.insert_email(conn, s, cache)
    conn.execute("COMMIT")
    dedupe.run(conn.execute("PRAGMA database_list").fetchone()[2], tmp_path / "d.csv", None)
    groups = notify.load_groups(conn)
    g = group_by_target(groups, 41)
    assert g.original["message_id"] == mid(40) and g.category == dedupe.DIFFERENT_TIMESTAMPS
    selected, _ = notify.select_live(groups, 5)
    assert [x.target["message_id"] for x in selected] == [mid(22), mid(11), mid(41)]


def test_select_live_skips_group_if_any_member_notified(conn):
    # N7d: notifying an EARLIER member (not the target) still makes the group "notified".
    conn.execute("UPDATE emails SET notification_sent = 1, notification_date = '2026-10-01 00:00:00' "
                 "WHERE message_id = ?", (mid(21),))
    selected, skipped = notify.select_live(notify.load_groups(conn), 5)
    assert [g.target["message_id"] for g in selected] == [mid(11)] and skipped == 1


# --- template -------------------------------------------------------------------------

def test_body_follows_the_template_exactly(conn):
    g = group_by_target(notify.load_groups(conn), 11)
    assert notify.notice_body(g).splitlines() == [
        "This is an automated notification from the Email Deduplication System.",
        "",
        "Your email has been identified as a potential duplicate:",
        "",
        "Your Email (Flagged):",
        "Message-ID: <11.JavaMail.evans@thyme>",
        "Date Sent: 2001-03-09 09:00:00 UTC",
        "Subject: Staffing",
        "",
        "Original Email on Record:",
        "Message-ID: <10.JavaMail.evans@thyme>",
        "Date Sent: 2001-03-02 09:00:00 UTC",
        f"Similarity Score: {g.similarity:.1f}%",
        "",
        "If this was NOT a duplicate and you intended to send this email,",
        "please reply with CONFIRM to restore it to active status.",
        "No action is required if this is indeed a duplicate.",
    ]


def test_subject_is_literal(conn):
    groups = notify.load_groups(conn)
    assert notify.notice_subject(group_by_target(groups, 11)) == "[Duplicate Notice] Re: Staffing"
    assert notify.notice_subject(group_by_target(groups, 22)) == "[Duplicate Notice] Re: Re: Plan"  # N3 A


def test_live_body_starts_with_test_delivery_line(conn):
    g = group_by_target(notify.load_groups(conn), 11)
    body = notify.live_body(g)
    assert body.splitlines()[0] == "[Test delivery: intended for liz.webb@enron.com]"
    assert body.endswith(notify.notice_body(g))
    assert "[Test delivery" not in notify.notice_body(g)  # drafts stay exactly as the template


def test_draft_headers(conn):
    g = group_by_target(notify.load_groups(conn), 11)
    msg = email.message_from_bytes(notify.build_draft(g, NOW).as_bytes())
    assert msg["To"] == "liz.webb@enron.com"  # the original sender, never the override
    assert msg["Subject"] == "[Duplicate Notice] Re: Staffing"
    assert msg["Date"] == "Sun, 11 Oct 2026 09:30:00 +0000"
    assert msg["References"] == "<11.JavaMail.evans@thyme>"
    assert msg["In-Reply-To"] is None and msg["From"] is None  # N4 (i): only the template's headers
    assert msg.get_content_type() == "text/plain"
    assert msg.get_payload(decode=True).decode() == notify.notice_body(g).replace("\n", "\r\n")


def test_draft_filename_is_safe_and_unique(conn):
    names = [notify.draft_filename(g) for g in notify.load_groups(conn)]
    assert names[0] == "3.JavaMail.evans@thyme.eml" and len(set(names)) == len(names)
    assert notify.draft_filename(notify.NoticeGroup({}, [], {"message_id": "<a/b\\c d>"}, 0, "")) == "a_b_c_d.eml"


# --- dry run --------------------------------------------------------------------------

def test_dry_run_zip_and_samples(tmp_path, conn):
    samples = tmp_path / "replies"
    samples.mkdir()
    (samples / "stale.eml").write_text("old")
    (samples / "README.txt").write_text("keep me")
    stats = notify.dry_run(conn, tmp_path / "replies.zip", samples, sample_count=2, now=NOW)
    assert stats["groups"] == stats["drafts_written"] == 3 and stats["draft_errors"] == []
    with zipfile.ZipFile(tmp_path / "replies.zip") as zf:
        names = sorted(zf.namelist())
        assert names == sorted(f"replies/{n}" for n in
                               ["3.JavaMail.evans@thyme.eml", "11.JavaMail.evans@thyme.eml", "22.JavaMail.evans@thyme.eml"])
        assert b"References: <22.JavaMail.evans@thyme>" in zf.read("replies/22.JavaMail.evans@thyme.eml")
    left = sorted(p.name for p in samples.iterdir())
    assert "stale.eml" not in left and "README.txt" in left
    assert len([n for n in left if n.endswith(".eml")]) == 2


def test_dry_run_never_sends_and_never_touches_notification_columns(tmp_path, conn):
    notify.dry_run(conn, tmp_path / "r.zip", tmp_path / "r", now=NOW)
    assert conn.execute("SELECT sum(notification_sent) FROM emails").fetchone()[0] == 0
    assert not (tmp_path / "send_log.csv").exists()


# --- guards ---------------------------------------------------------------------------

def test_read_override(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    assert notify.read_override(env) is None
    env.write_text("# comment\nOTHER=1\nNOTIFY_RECIPIENT_OVERRIDE = 'ignored-wrong-format'\n")
    assert notify.read_override(env) is None  # KEY=value only, no spaces around '='
    env.write_text(f'NOTIFY_RECIPIENT_OVERRIDE="{OVERRIDE.upper()}"\n')
    assert notify.read_override(env) == OVERRIDE
    monkeypatch.setenv(notify.OVERRIDE_VAR, "from.env.var@gmail.com")
    assert notify.read_override(env) == "from.env.var@gmail.com"  # the environment wins


@pytest.mark.parametrize("address, reason", [
    (None, "not set"),
    ("", "not set"),
    ("not-an-address", "not a valid"),
    ("someone@enron.com", "Enron address"),
    ("x@ect.enron.com", "Enron address"),
    ("jeff.skilling@enron.com", "Enron address"),
    ("liz.webb@enron.com", "Enron address"),
])
def test_override_refused(conn, address, reason):
    with pytest.raises(notify.LiveModeRefused, match=reason):
        notify.check_override(conn, address)


def test_override_in_dataset_is_refused(tmp_path, conn):
    # A non-Enron address that appears in the data (e.g. an outside correspondent).
    conn.execute("INSERT INTO addresses (address) VALUES ('outside.person@calpine.com')")
    with pytest.raises(notify.LiveModeRefused, match="appears in the Enron dataset"):
        notify.check_override(conn, "outside.person@calpine.com")
    assert notify.check_override(conn, OVERRIDE) == OVERRIDE


@pytest.mark.parametrize("n, ok", [(0, False), (1, True), (5, True), (10, True), (11, False), (1000, False)])
def test_max_sends_ceiling(n, ok):
    if ok:
        assert notify.check_max_sends(n) == n
    else:
        with pytest.raises(notify.LiveModeRefused):
            notify.check_max_sends(n)


# --- live mode with an in-process fake sender ------------------------------------------

class FakeSender:
    """Async context manager with the same send() as GmailMcpSender."""

    def __init__(self, outcomes=None):
        self.calls = []
        self.outcomes = list(outcomes or [])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def send(self, to, subject, body, in_reply_to=None):
        self.calls.append({"to": to, "subject": subject, "body": body, "in_reply_to": in_reply_to})
        if self.outcomes:
            return self.outcomes.pop(0)
        return SendResult("sent", gmail_message_id=f"gm{len(self.calls)}")


def write_env(tmp_path, address=OVERRIDE):
    (tmp_path / ".env").write_text(f"NOTIFY_RECIPIENT_OVERRIDE={address}\n")
    return tmp_path / ".env"


def read_log(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def test_live_run_sends_logs_and_marks(tmp_path, conn):
    fake = FakeSender()
    stats = notify.live_run(conn, 5, tmp_path / "send_log.csv", write_env(tmp_path), sender_factory=lambda: fake)
    assert stats["sent"] == 2 and stats["failed"] == 0
    assert [c["to"] for c in fake.calls] == [[OVERRIDE], [OVERRIDE]]  # only ever the override
    assert fake.calls[0]["in_reply_to"] == "<22.JavaMail.evans@thyme>"
    rows = read_log(tmp_path / "send_log.csv")
    assert list(rows[0].keys()) == list(notify.SEND_LOG_COLUMNS)
    assert rows[0]["recipient"] == OVERRIDE and rows[0]["intended_recipient"] == "sally.beck@enron.com"
    assert rows[0]["status"] == "sent" and rows[0]["gmail_message_id"] == "gm1" and rows[0]["error"] == ""
    assert notified(conn) == [mid(11), mid(22)]
    assert all(r[0] for r in conn.execute("SELECT notification_date FROM emails WHERE notification_sent = 1"))


def test_masked_log_hides_the_override(tmp_path, conn):
    full, masked = tmp_path / "data" / "send_log.csv", tmp_path / "output" / "send_log.csv"
    fake = FakeSender([SendResult("failed", error=f"550 mailbox {OVERRIDE.upper()} unavailable"), SendResult("sent", gmail_message_id="g2")])
    notify.live_run(conn, 5, full, write_env(tmp_path), sender_factory=lambda: fake, masked_log_path=masked)
    assert OVERRIDE in full.read_text()
    text = masked.read_text()
    assert OVERRIDE not in text.lower() and "p***@gmail.com" in text
    rows = read_log(masked)
    assert list(rows[0].keys()) == list(notify.SEND_LOG_COLUMNS) and len(rows) == 2
    assert rows[0]["error"] == "550 mailbox p***@gmail.com unavailable"
    assert rows[1]["intended_recipient"] == "liz.webb@enron.com"  # dataset addresses stay readable
    assert [r["status"] for r in rows] == [r["status"] for r in read_log(full)]


@pytest.mark.parametrize("address, masked", [("ramya@gmail.com", "r***@gmail.com"), ("x@y.org", "x***@y.org"),
                                             ("broken", "***")])
def test_mask_address(address, masked):
    assert notify.mask_address(address) == masked


def test_rerun_never_sends_the_same_notice_twice(tmp_path, conn):
    env = write_env(tmp_path)
    notify.live_run(conn, 5, tmp_path / "log.csv", env, sender_factory=lambda: FakeSender())
    fake = FakeSender()
    stats = notify.live_run(conn, 5, tmp_path / "log.csv", env, sender_factory=lambda: fake)
    assert fake.calls == [] and stats["selected"] == 0 and stats["skipped_already_notified"] == 2
    assert len(read_log(tmp_path / "log.csv")) == 2  # the log is appended to, never rewritten


def test_failed_send_is_logged_and_not_marked(tmp_path, conn):
    fake = FakeSender([SendResult("failed", error="quota exceeded"), SendResult("sent", gmail_message_id="ok2")])
    stats = notify.live_run(conn, 5, tmp_path / "log.csv", write_env(tmp_path), sender_factory=lambda: fake)
    assert stats["failed"] == 1 and stats["sent"] == 1
    rows = read_log(tmp_path / "log.csv")
    assert [r["status"] for r in rows] == ["failed", "sent"] and rows[0]["error"] == "quota exceeded"
    assert notified(conn) == [mid(11)]
    # The failed group is retried on the next run.
    fake2 = FakeSender()
    notify.live_run(conn, 5, tmp_path / "log.csv", write_env(tmp_path), sender_factory=lambda: fake2)
    assert [c["in_reply_to"] for c in fake2.calls] == ["<22.JavaMail.evans@thyme>"]


def test_unconfirmed_result_stops_the_run(tmp_path, conn):
    fake = FakeSender([SendResult("unconfirmed", error="no message id")])
    stats = notify.live_run(conn, 5, tmp_path / "log.csv", write_env(tmp_path), sender_factory=lambda: fake)
    assert len(fake.calls) == 1 and stats["unconfirmed"] == 1
    assert conn.execute("SELECT sum(notification_sent) FROM emails").fetchone()[0] == 0


def test_cap_limits_sends(tmp_path, conn):
    fake = FakeSender()
    notify.live_run(conn, 1, tmp_path / "log.csv", write_env(tmp_path), sender_factory=lambda: fake)
    assert len(fake.calls) == 1


def test_guards_run_before_anything_is_sent(tmp_path, conn):
    fake = FakeSender()
    with pytest.raises(notify.LiveModeRefused):
        notify.live_run(conn, 5, tmp_path / "log.csv", tmp_path / "missing.env", sender_factory=lambda: fake)
    with pytest.raises(notify.LiveModeRefused):
        notify.live_run(conn, 5, tmp_path / "log.csv", write_env(tmp_path, "ken.lay@enron.com"),
                        sender_factory=lambda: fake)
    with pytest.raises(notify.LiveModeRefused):
        notify.live_run(conn, 11, tmp_path / "log.csv", write_env(tmp_path), sender_factory=lambda: fake)
    assert fake.calls == [] and not (tmp_path / "log.csv").exists()


def test_sender_that_cannot_start_logs_failures_without_crashing(tmp_path, conn):
    class Broken(FakeSender):
        async def __aenter__(self):
            raise FileNotFoundError("npx not found")

    stats = notify.live_run(conn, 5, tmp_path / "log.csv", write_env(tmp_path), sender_factory=lambda: Broken())
    assert stats["failed"] == 2 and stats["sent"] == 0
    assert all("MCP server error: FileNotFoundError" in r["error"] for r in read_log(tmp_path / "log.csv"))
    assert conn.execute("SELECT sum(notification_sent) FROM emails").fetchone()[0] == 0


# --- over the real MCP protocol, against the fake Gmail server ---------------------------

def fake_config(tmp_path, fail=None):
    env = {"FAKE_GMAIL_LOG": str(tmp_path / "fake_gmail.log")}
    if fail:
        env["FAKE_GMAIL_FAIL"] = fail
    cfg = tmp_path / "mcp_config.json"
    cfg.write_text(json.dumps({"mcpServers": {"gmail": {"command": sys.executable,
                                                        "args": [str(FAKE_SERVER)], "env": env}}}))
    return cfg


def test_live_run_over_mcp_protocol(tmp_path, conn):
    stats = notify.live_run(conn, 5, tmp_path / "log.csv", write_env(tmp_path), mcp_config=fake_config(tmp_path),
                            server_log=tmp_path / "server.log")
    assert stats["sent"] == 2
    calls = [json.loads(l) for l in (tmp_path / "fake_gmail.log").read_text().splitlines()]
    assert [c["to"] for c in calls] == [[OVERRIDE], [OVERRIDE]]
    assert calls[0]["subject"] == "[Duplicate Notice] Re: Re: Plan"
    assert calls[0]["inReplyTo"] == "<22.JavaMail.evans@thyme>" and calls[0]["mimeType"] == "text/plain"
    assert calls[0]["body"].startswith("[Test delivery: intended for sally.beck@enron.com]")
    assert [r["gmail_message_id"] for r in read_log(tmp_path / "log.csv")] == ["fake0001", "fake0002"]


def test_tool_error_over_mcp_protocol(tmp_path, conn):
    stats = notify.live_run(conn, 5, tmp_path / "log.csv", write_env(tmp_path),
                            mcp_config=fake_config(tmp_path, fail="Staffing"), server_log=tmp_path / "server.log")
    assert stats["sent"] == 1 and stats["failed"] == 1
    failed = next(r for r in read_log(tmp_path / "log.csv") if r["status"] == "failed")
    assert "simulated Gmail API error" in failed["error"]
    assert notified(conn) == [mid(22)]


def test_server_that_does_not_exist(tmp_path, conn):
    cfg = tmp_path / "mcp_config.json"
    cfg.write_text(json.dumps({"mcpServers": {"gmail": {"command": str(tmp_path / "no-such-binary"), "args": []}}}))
    stats = notify.live_run(conn, 5, tmp_path / "log.csv", write_env(tmp_path), mcp_config=cfg,
                            server_log=tmp_path / "server.log")
    assert stats["failed"] == 2 and conn.execute("SELECT sum(notification_sent) FROM emails").fetchone()[0] == 0


# --- MCP client helpers -----------------------------------------------------------------

class _Text:
    def __init__(self, text):
        self.text = text


class _Result:
    def __init__(self, text, is_error=False):
        self.content, self.is_error = [_Text(text)], is_error


@pytest.mark.parametrize("text, is_error, status, gid", [
    ("Email sent successfully with ID: 1a1298c7c0b1b4da", False, "sent", "1a1298c7c0b1b4da"),
    ("Error: invalid_grant (token expired)", True, "failed", ""),
    ("Something else happened", False, "unconfirmed", ""),
])
def test_interpret(text, is_error, status, gid):
    r = interpret(_Result(text, is_error))
    assert r.status == status and r.gmail_message_id == gid


def test_load_server_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", "/home/tester")
    default = load_server_config(tmp_path / "missing.json")
    assert default["command"] == "npx" and default["args"] == ["-y", "@artymclabin/gmail-mcp@1.2.3"]
    assert default["env"]["GMAIL_CREDENTIALS_PATH"] == "/home/tester/.gmail-mcp/credentials.json"
    example = Path(__file__).parents[1] / "mcp_config.json.example"
    cfg = load_server_config(example)
    assert cfg == default  # the committed example describes exactly the default server
