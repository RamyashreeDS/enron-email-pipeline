"""End-to-end parsing of real Enron emails that exercise specific edge cases.

Fixtures are byte-for-byte copies of files from the public Enron dataset; the file name is
the original path with '/' replaced by '__'.
"""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from enron_pipeline.models import ParseError
from enron_pipeline.parser import parse_file

FIXTURES = Path(__file__).parent / "fixtures" / "enron"


def parse(name):
    return parse_file(FIXTURES / f"{name}.txt", "maildir/" + name.replace("__", "/") + ".")


def test_source_file_is_relative_maildir_path():
    assert parse("skilling-j__notes_inbox__36").source_file == "maildir/skilling-j/notes_inbox/36."


def test_bare_lf_in_subject_is_repaired_and_x_headers_survive():
    # The stdlib parser stops at the broken Subject line and loses every X- header.
    from enron_pipeline.headers import Headers
    from enron_pipeline.rawfile import split_raw

    raw = (FIXTURES / "kitchen-l__calendar__35.txt").read_bytes()
    rf = split_raw(raw)
    h = Headers(rf.header_text)
    assert rf.header_repairs == 1
    assert h.get("Subject") == "Meeting w/ -Andy Zipper -  Your office (Jessica Ramiez)"
    assert h.get("X-Folder") == "\\ExMerge - Kitchen, Louise\\Calendar"
    assert h.get("X-Origin") == "KITCHEN-L"
    # ...and the email itself has no To header, so it fails as MISSING_TO (decision D1).
    with pytest.raises(ParseError) as exc:
        parse("kitchen-l__calendar__35")
    assert exc.value.code == "MISSING_TO"


def test_missing_to_fails():
    with pytest.raises(ParseError) as exc:
        parse("skilling-j__notes_inbox__287")
    assert exc.value.code == "MISSING_TO"


def test_invalid_from_fails():
    with pytest.raises(ParseError) as exc:
        parse("skilling-j__inbox__223")
    assert exc.value.code == "INVALID_FROM"
    assert "pep <performance.>" in exc.value.detail


def test_non_ascii_header_bytes_decode_to_str():
    e = parse("lay-k__inbox__533")
    assert isinstance(e.x_from, str) and "enronsato@hotmail.com" in e.x_from
    assert e.from_address == "enronsato@hotmail.com"
    assert e.to_addresses == ["klay@enron.com"]


def test_long_folded_to_with_non_ascii_address():
    e = parse("kitchen-l___americas__regulatory__113")
    assert len(e.to_addresses) == 109
    assert "ren\xe9.lazure@enron.com" in e.to_addresses
    assert e.subject == "RE: Legislative Report (07/20/2001)"


def test_date_normalized_to_utc():
    e = parse("skilling-j__notes_inbox__36")  # Wed, 13 Dec 2000 01:13:00 -0800 (PST)
    assert e.date == datetime(2000, 12, 13, 9, 13, tzinfo=timezone.utc)
    assert not e.date_is_sentinel


def test_sentinel_date_is_flagged():
    e = parse("skilling-j__discussion_threads__1089")
    assert e.date == datetime(1980, 1, 1, tzinfo=timezone.utc)
    assert e.date_is_sentinel
    assert [w.code for w in e.warnings] == ["DATE_SENTINEL"]


def test_quoted_printable_is_decoded():
    e = parse("skilling-j__notes_inbox__36")
    assert "=20" not in e.body and "=\n" not in e.body
    assert e.body.startswith("Congratulation Jeff!")


def test_bcc_captured_as_given():
    e = parse("skilling-j__notes_inbox__36")
    assert e.cc_addresses and e.bcc_addresses == e.cc_addresses  # dataset artifact (D2)


def test_optional_x_headers():
    e = parse("skilling-j__notes_inbox__376")
    assert e.x_from == '"Little, Melissa" <melissa.little@csfb.com>'
    assert e.x_origin == "SKILLING-J"
    assert e.x_folder.startswith("\\Jeff_Skilling_Oct2001\\")
    assert e.x_cc is None and e.x_bcc is None
    assert e.content_type == "text/plain"


# --- body / forwarded / quoted separation -------------------------------------------

def test_pure_lotus_forward_has_empty_body():
    e = parse("skilling-j__notes_inbox__3")
    assert e.body == ""
    assert e.forwarded_content.startswith("---------------------- Forwarded by Rosalee Fleming")
    assert "Thank you for the Charity Gift Card." in e.forwarded_content
    assert e.quoted_content is None


def test_original_message_with_fw_subject_is_forwarded():
    e = parse("skilling-j__notes_inbox__376")  # subject 'FW: ...'
    assert e.body.startswith("Let's try this again....")
    assert e.body.endswith("(212) 325-2881")
    assert e.forwarded_content.startswith("-----Original Message-----")
    assert e.quoted_content is None


def test_original_message_with_re_subject_is_quoted():
    e = parse("kitchen-l__sent_items__1027")  # subject 'RE: ...'
    assert e.body.endswith("Thanks\n\nLouise")
    assert e.quoted_content.lstrip().startswith("-----Original Message-----")
    assert e.forwarded_content is None


def test_lotus_letterhead_reply_header_is_quoted():
    e = parse("farmer-d___sent_mail__1")
    assert e.body == "Can you come down to my desk around 1:00?\n\nD"
    assert "Enron North America Corp." not in e.body
    assert "Do you have any time this afternoon" in e.quoted_content


def test_fully_quoted_message_becomes_body():
    e = parse("skilling-j__notes_inbox__100")
    assert e.body.startswith("Dear Jeff,")
    assert ">" not in e.body.split("\n")[0]
    assert e.quoted_content is None and e.forwarded_content is None


def test_embedded_raw_message_is_forwarded():
    e = parse("lay-k__all_documents__1012")
    assert e.body.endswith("best regards, j.c. freeman")
    assert e.forwarded_content.startswith("Received: from mail5.registeredsite.com")


def test_leaked_smtp_headers_are_stripped():
    e = parse("skilling-j__notes_inbox__301")
    assert e.leaked_headers_stripped
    assert e.body.startswith("Mr. Skilling,")
    assert "Return-Path" not in e.body


# --- has_attachment -----------------------------------------------------------------

@pytest.mark.parametrize("name, expected", [
    ("skilling-j__notes_inbox__100", True),   # <<LEO_Equity_Story_C(10)_English.ppt>>
    ("skilling-j__notes_inbox__301", True),   # leaked multipart/mixed + ' - resume.doc'
    ("skilling-j__notes_inbox__376", True),   # ' - Preface3.1.doc'
    ("kitchen-l__sent_items__1027", False),   # '<< File: http://...>>' is a link, not a file
    ("skilling-j__notes_inbox__36", False),
    ("skilling-j__notes_inbox__3", False),
])
def test_has_attachment(name, expected):
    assert parse(name).has_attachment is expected
