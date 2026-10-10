"""The pipeline must never crash on malformed input: every file is either parsed or logged."""
import os
import random

import pytest

from enron_pipeline import extract
from enron_pipeline.models import ParseError
from enron_pipeline.parser import parse_bytes

GOOD = (b"Message-ID: <1.2.JavaMail.evans@thyme>\r\nDate: Mon, 4 Dec 2000 09:58:00 -0800 (PST)\r\n"
        b"From: a@enron.com\r\nTo: b@enron.com\r\nSubject: hi\r\n\r\nbody\n")


@pytest.mark.parametrize("raw, code", [
    (b"", "EMPTY_FILE"),
    (b"   \r\n\r\n  ", "EMPTY_FILE"),
    (b"just some text with no headers\nat all\n", "NO_HEADERS"),
    (b"\x00\x01\x02\xff\xfe binary", "NO_HEADERS"),
    (GOOD.replace(b"Message-ID: <1.2.JavaMail.evans@thyme>\r\n", b""), "MISSING_MESSAGE_ID"),
    (GOOD.replace(b"Date: Mon, 4 Dec 2000 09:58:00 -0800 (PST)", b"Date: someday"), "INVALID_DATE"),
    (GOOD.replace(b"From: a@enron.com\r\n", b""), "MISSING_FROM"),
    (GOOD.replace(b"To: b@enron.com\r\n", b""), "MISSING_TO"),
    (GOOD.replace(b"To: b@enron.com", b"To: nobody"), "INVALID_TO"),
    (GOOD.replace(b"Subject: hi\r\n", b""), "MISSING_SUBJECT"),
])
def test_bad_inputs_raise_parse_error_with_code(raw, code):
    with pytest.raises(ParseError) as exc:
        parse_bytes(raw, "maildir/x/y/1.")
    assert exc.value.code == code


def test_several_problems_are_all_reported():
    raw = GOOD.replace(b"From: a@enron.com\r\n", b"").replace(b"To: b@enron.com\r\n", b"")
    with pytest.raises(ParseError) as exc:
        parse_bytes(raw, "x")
    assert exc.value.code == "MISSING_FROM" and "MISSING_TO" in exc.value.detail


def test_headers_only_file_parses_with_empty_body():
    e = parse_bytes(GOOD.split(b"\r\n\r\n")[0], "x")
    assert e.body == "" and e.subject == "hi"


def test_empty_subject_is_allowed():
    assert parse_bytes(GOOD.replace(b"Subject: hi", b"Subject: "), "x").subject == ""


def test_lf_only_file_parses():
    assert parse_bytes(GOOD.replace(b"\r\n", b"\n"), "x").to_addresses == ["b@enron.com"]


def test_bad_quoted_printable_falls_back_to_raw_body():
    raw = GOOD.replace(b"Subject: hi\r\n", b"Subject: hi\r\nContent-Transfer-Encoding: base64\r\n")
    e = parse_bytes(raw.replace(b"body\n", b"!!! not base64 ===\n"), "x")
    assert isinstance(e.body, str)


def test_huge_body(tmp_path):
    raw = GOOD + (b"> quoted line of text\n" + b"plain line\n") * 250_000  # ~5 MB
    e = parse_bytes(raw, "x")
    assert len(e.body) > 1_000_000


def _write_tree(root):
    mb = root / "maildir" / "test-u" / "inbox"
    mb.mkdir(parents=True)
    rng = random.Random(0)
    files = {
        "1.": GOOD,
        "2.": b"",
        "3.": bytes(rng.randrange(256) for _ in range(4096)),
        "4.": GOOD.replace(b"To: b@enron.com\r\n", b""),
        "5.": GOOD.replace(b"\r\n\r\nbody", b"\r\n\r\n" + bytes(range(256)) * 10),
        "6.": b"From: x\r\n" * 10000,
        "7.": GOOD.replace(b"Subject: hi", b"Subject: hi\nbroken continuation"),
    }
    for name, data in files.items():
        (mb / name).write_bytes(data)
    (mb / ".DS_Store").write_bytes(b"junk")  # hidden: skipped
    os.symlink(mb / "1.", mb / "8.")          # symlink: skipped
    return root / "maildir", len(files)


def test_run_never_crashes_and_accounts_for_every_file(tmp_path):
    root, n = _write_tree(tmp_path)
    err, warn, stats_json = tmp_path / "error_log.txt", tmp_path / "w.txt", tmp_path / "s.json"
    stats = extract.run(root, error_log_path=err, warnings_path=warn, stats_path=stats_json,
                        jsonl_path=tmp_path / "out.jsonl")
    assert stats.found == n
    assert stats.consistent
    assert stats.parsed == 3  # 1., 5. and 7.
    assert stats.failures_by_reason["UNEXPECTED_ERROR"] == 0
    lines = err.read_text().splitlines()
    assert lines[0].startswith("# timestamp_utc\tsource_file\treason\tdetail")
    assert len(lines) == 1 + stats.failed
    assert all(len(l.split("\t")) == 4 for l in lines[1:])
    assert stats_json.exists() and len((tmp_path / "out.jsonl").read_text().splitlines()) == 3


def test_unexpected_exception_is_logged_not_raised(tmp_path, monkeypatch):
    root, n = _write_tree(tmp_path)

    def boom(path, source_file):
        raise RuntimeError("simulated bug")

    monkeypatch.setattr(extract, "parse_file", boom)
    stats = extract.run(root, error_log_path=tmp_path / "e.txt", warnings_path=tmp_path / "w.txt",
                        stats_path=None)
    assert stats.failed == n and stats.failures_by_reason["UNEXPECTED_ERROR"] == n
    assert "RuntimeError: simulated bug" in (tmp_path / "e.txt").read_text()


def test_missing_mailbox_is_logged(tmp_path):
    root, _ = _write_tree(tmp_path)
    stats = extract.run(root, mailboxes=["no-such-user", "../escape"], error_log_path=tmp_path / "e.txt",
                        warnings_path=tmp_path / "w.txt", stats_path=None)
    assert stats.found == 0
    log = (tmp_path / "e.txt").read_text()
    assert "Mailbox directory not found" in log and "Invalid mailbox name" in log
