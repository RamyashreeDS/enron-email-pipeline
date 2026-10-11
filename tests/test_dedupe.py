import csv
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from enron_pipeline import db, dedupe
from enron_pipeline.dedupe import Candidate, group_bucket, normalize_subject, similarity
from enron_pipeline.models import ParsedEmail


# --- pure rules -------------------------------------------------------------------

@pytest.mark.parametrize("subject, expected", [
    ("Meeting Friday", "Meeting Friday"),
    ("RE: Meeting Friday", "Meeting Friday"),
    ("Re: FW: Fwd: Meeting Friday", "Meeting Friday"),
    ("re:Meeting Friday", "Meeting Friday"),
    ("RE[2]: Meeting Friday", "Meeting Friday"),
    ("FWD :  Meeting Friday", "Meeting Friday"),
    ("", ""),
    ("Fwd:", ""),
    ("Meeting Friday re: budget", "Meeting Friday re: budget"),  # only leading prefixes
    ("Reminder: lunch", "Reminder: lunch"),                      # 'Re' must be the whole word
])
def test_normalize_subject(subject, expected):
    assert normalize_subject(subject) == expected


def test_subject_match_is_exact_apart_from_prefixes():
    # X7 B: case is kept, so these are different subjects.
    assert normalize_subject("RE: Q4 Plan") != normalize_subject("Q4 plan")


def test_collapse_whitespace():
    assert dedupe.collapse_whitespace("Let's  meet\n\tat 3pm.\n") == "Let's meet at 3pm."
    assert dedupe.collapse_whitespace(None) == ""


def cand(i, date="2001-01-01 09:00:00", body="Let's meet at 3pm.", fwd="", sentinel=False,
         subject="Meeting Friday", sender="ken@enron.com", quoted=""):
    return Candidate(i, f"<{i}@x>", sender, subject, date, sentinel,
                     dedupe.collapse_whitespace(body), dedupe.collapse_whitespace(fwd),
                     dedupe.collapse_whitespace(quoted))


def test_spec_example_e1_e2_e3():
    e1 = cand(1, "2001-01-01 09:00:00", "Let's meet at 3pm.")
    e2 = cand(2, "2001-01-01 11:00:00", "Let's meet at 3 pm.", subject="RE: Meeting Friday")
    e3 = cand(3, "2001-01-02 08:00:00", "Budget numbers attached.")
    groups, _ = group_bucket([e3, e2, e1])
    assert len(groups) == 1
    g = groups[0]
    assert g.original.id == 1 and [m.email.id for m in g.duplicates] == [2]
    assert g.duplicates[0].score == pytest.approx(97.3, abs=0.1)
    assert g.category == dedupe.DIFFERENT_TIMESTAMPS


def test_threshold_is_inclusive_and_applied_before_rounding():
    a, b = cand(1), cand(2)
    score, _ = similarity(a, b)
    assert score == 100.0
    groups, _ = group_bucket([cand(1, body="a" * 90 + "b" * 10), cand(2, body="a" * 100)],
                             threshold=90.0)
    assert len(groups) == 1 and groups[0].duplicates[0].score >= 90.0
    groups, _ = group_bucket([cand(1, body="a" * 89 + "b" * 11), cand(2, body="a" * 100)],
                             threshold=94.3)
    assert groups == []


def test_x2_ties_broken_by_lowest_id():
    copies = [cand(7), cand(3), cand(5)]  # identical timestamp and text: folder copies
    (g,), _ = group_bucket(copies)
    assert g.original.id == 3
    assert [m.email.id for m in g.duplicates] == [5, 7]
    assert g.notification_target.id == 7
    assert g.category == dedupe.SAME_TIMESTAMP


def test_x2_tie_at_the_latest_time_targets_highest_id():
    (g,), _ = group_bucket([cand(1, "2001-01-01 09:00:00"), cand(4, "2001-01-02 09:00:00"),
                            cand(2, "2001-01-02 09:00:00")])
    assert g.original.id == 1 and g.notification_target.id == 4


def test_x3_empty_bodies_compare_forwarded_content():
    fwd_a = "---- Forwarded by Daren ---- Sitara release notes for April: deal 1234 moved."
    fwd_b = "---- Forwarded by Daren ---- Changes in Global due to consent: new counterparty list."
    groups, _ = group_bucket([cand(1, body="", fwd=fwd_a), cand(2, body="", fwd=fwd_b)])
    assert groups == []  # different forwards are not duplicates
    (g,), _ = group_bucket([cand(1, body="", fwd=fwd_a), cand(2, body="", fwd=fwd_a)])
    assert g.used_rule(dedupe.FORWARDED) and g.duplicates[0].score == 100.0


def test_x3_only_one_empty_body_compares_bodies():
    score, compared = similarity(cand(1, body="", fwd="same"), cand(2, body="hello", fwd="same"))
    assert score == 0.0 and compared == dedupe.BODY


def test_x3_one_empty_forwarded_part_compares_forwarded():
    score, compared = similarity(cand(1, body="", fwd="", quoted="q"), cand(2, body="", fwd="text", quoted="q"))
    assert score == 0.0 and compared == dedupe.FORWARDED


def test_x3_extension_compares_quoted_when_bodies_and_forwards_are_empty():
    # Real case: j..farmer replied "Remove" (no own text) to two different senders.
    q1 = "-----Original Message----- From: \"Iona Alphonso\" <ialphonso@garygreene.com> Sent: Tuesday"
    q2 = "-----Original Message----- From: KibbyBears <KibbyBears@temd.net> Sent: Friday, November 2"
    score, compared = similarity(cand(1, body="", quoted=q1), cand(2, body="", quoted=q2))
    assert compared == dedupe.QUOTED and score < 90
    groups, _ = group_bucket([cand(1, "2001-10-03 12:12:32", body="", quoted=q1),
                              cand(2, "2001-11-02 19:11:53", body="", quoted=q2)])
    assert groups == []
    # Folder copies with the same quoted text still match.
    (g,), _ = group_bucket([cand(3, body="", quoted=q1), cand(4, body="", quoted=q1)])
    assert g.used_rule(dedupe.QUOTED) and g.duplicates[0].score == 100.0


def test_x3_completely_empty_emails_still_match():
    # Nothing at all to compare on either side: "" vs "" is 100%.
    score, compared = similarity(cand(1, body=""), cand(2, body=""))
    assert score == 100.0 and compared == dedupe.QUOTED


def test_x4_sentinel_is_never_the_original():
    sentinel = cand(1, "1980-01-01 00:00:00", sentinel=True)
    real = cand(2, "2001-05-01 10:00:00")
    (g,), _ = group_bucket([sentinel, real])
    assert g.original.id == 2 and g.duplicates[0].email.id == 1


def test_x4_sentinel_only_group_uses_id_tie_break():
    (g,), _ = group_bucket([cand(9, "1980-01-01 00:00:00", sentinel=True),
                            cand(4, "1980-01-01 00:00:00", sentinel=True)])
    assert g.original.id == 4 and g.category == dedupe.SAME_TIMESTAMP


def test_x5_compare_with_original_not_chain():
    # A~B and B~C pass 90%, but A~C doesn't: C must not join A's group.
    a = cand(1, "2001-01-01 01:00:00", body="x" * 100)
    b = cand(2, "2001-01-01 02:00:00", body="x" * 95 + "y" * 5)
    c = cand(3, "2001-01-01 03:00:00", body="x" * 88 + "y" * 12)
    assert similarity(a, b)[0] >= 90 and similarity(b, c)[0] >= 90 and similarity(a, c)[0] < 90
    groups, _ = group_bucket([a, b, c])
    assert len(groups) == 1 and [m.email.id for m in groups[0].duplicates] == [2]


def test_buckets_need_same_sender_and_subject():
    emails = [cand(1), cand(2, sender="other@enron.com"), cand(3, subject="RE: Meeting Friday"),
              cand(4, subject="meeting friday")]
    groups, info = dedupe.find_groups(emails)
    assert len(groups) == 1
    assert sorted([groups[0].original.id] + [m.email.id for m in groups[0].duplicates]) == [1, 3]
    assert info["candidate_buckets"] == 1


# --- against a real (temporary) database --------------------------------------------

def parsed(n, minutes=0, body="Please review the attached schedule for next week.",
           subject="Schedule", sender="ken.lay@enron.com", fwd=None, sentinel=False, folder="inbox"):
    when = (datetime(1980, 1, 1, tzinfo=timezone.utc) if sentinel
            else datetime(2001, 3, 1, 9, 0, tzinfo=timezone.utc) + timedelta(minutes=minutes))
    return ParsedEmail(message_id=f"<{n}.JavaMail.evans@thyme>", date=when, from_address=sender,
                       to_addresses=["jeff.skilling@enron.com"], subject=subject, body=body,
                       source_file=f"maildir/lay-k/{folder}/{n}.", forwarded_content=fwd,
                       has_attachment=False, date_is_sentinel=sentinel)


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "enron.db"
    conn = db.connect(path)
    db.create_schema(conn)
    cache = db.AddressCache(conn)
    conn.execute("BEGIN")
    emails = [
        parsed(1, folder="inbox"), parsed(2, folder="all_documents"), parsed(3, folder="discussion_threads"),
        parsed(4, minutes=60 * 24 * 7, subject="RE: Schedule"),          # re-send a week later
        parsed(5, body="Totally different text about the budget.", subject="Schedule"),
        parsed(6, body="", fwd="---- Forwarded: gas nominations for May", subject="FW: Noms"),
        parsed(7, minutes=5, body="", fwd="---- Forwarded: gas nominations for May", subject="Noms"),
        parsed(8, body="", fwd="---- Forwarded: completely unrelated travel itinerary", subject="Noms"),
    ]
    for e in emails:
        db.insert_email(conn, e, cache)
    conn.execute("COMMIT")
    conn.close()
    return path


def run(tmp_path, database):
    return dedupe.run(database, tmp_path / "duplicates_report.csv", tmp_path / "stats.json")


def test_run_flags_database_and_writes_report(tmp_path, database):
    groups, stats = run(tmp_path, database)
    assert stats["groups"] == 2 and stats["emails_flagged"] == 4
    assert stats["by_category"]["different_timestamps"]["groups"] == 2
    conn = db.connect(database)
    flags = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT id, is_duplicate, duplicate_of FROM emails")}
    orig = "<1.JavaMail.evans@thyme>"
    assert flags[1] == (0, None)
    assert flags[2] == flags[3] == flags[4] == (1, orig)
    assert flags[5] == (0, None)
    assert flags[6] == (0, None) and flags[7] == (1, "<6.JavaMail.evans@thyme>")  # empty-body rule
    assert flags[8] == (0, None)  # empty body, different forward
    assert db.check_database(conn)["foreign_key_violations"] == 0

    with open(tmp_path / "duplicates_report.csv", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert list(rows[0].keys()) == list(dedupe.REPORT_COLUMNS)
    assert len(rows) == 4 == stats["report_rows"] == stats["database_flagged"]
    r4 = next(r for r in rows if r["duplicate_message_id"] == "<4.JavaMail.evans@thyme>")
    assert r4["subject"] == "RE: Schedule"          # the duplicate's own subject (X10)
    assert r4["similarity_score"] == "100.0"
    assert r4["duplicate_date"] == "2001-03-08 09:00:00" and r4["original_date"] == "2001-03-01 09:00:00"
    assert stats["rules"]["groups_using_forwarded_content_rule"] == 1


def test_rerun_is_identical_and_keeps_notification_columns(tmp_path, database):
    run(tmp_path, database)
    conn = db.connect(database)
    conn.execute("UPDATE emails SET notification_sent = 1, notification_date = '2026-10-11 10:00:00' WHERE id = 4")
    first = [tuple(r) for r in conn.execute("SELECT id, is_duplicate, duplicate_of FROM emails ORDER BY id")]
    conn.close()
    _, stats = run(tmp_path, database)
    conn = db.connect(database)
    assert [tuple(r) for r in conn.execute("SELECT id, is_duplicate, duplicate_of FROM emails ORDER BY id")] == first
    assert tuple(conn.execute("SELECT notification_sent, notification_date FROM emails WHERE id = 4").fetchone()) \
        == (1, "2026-10-11 10:00:00")


def test_rerun_clears_stale_flags(tmp_path, database):
    conn = db.connect(database)
    conn.execute("UPDATE emails SET is_duplicate = 1, duplicate_of = '<1.JavaMail.evans@thyme>' WHERE id = 5")
    conn.close()
    run(tmp_path, database)
    conn = db.connect(database)
    assert tuple(conn.execute("SELECT is_duplicate, duplicate_of FROM emails WHERE id = 5").fetchone()) == (0, None)


def test_sentinel_copy_is_flagged_not_original(tmp_path, database):
    conn = db.connect(database)
    conn.execute("BEGIN")
    db.insert_email(conn, parsed(9, sentinel=True, folder="deleted_items"), db.AddressCache(conn))
    conn.execute("COMMIT")
    conn.close()
    groups, stats = run(tmp_path, database)
    g = next(g for g in groups if g.original.id == 1)
    assert 9 in [m.email.id for m in g.duplicates]
    assert stats["rules"]["groups_with_sentinel_dates"] == 1
