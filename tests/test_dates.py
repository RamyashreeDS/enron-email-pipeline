from datetime import datetime, timezone

import pytest

from enron_pipeline.dates import parse_date


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def codes(result):
    return [w.code for w in result.warnings]


@pytest.mark.parametrize("value, expected", [
    # The two forms seen in all 34,222 real headers.
    ("Mon, 4 Dec 2000 09:58:00 -0800 (PST)", utc(2000, 12, 4, 17, 58)),
    ("Fri, 28 Sep 2001 05:51:21 -0700 (PDT)", utc(2001, 9, 28, 12, 51, 21)),
    # Offset without the parenthesised comment.
    ("Tue, 15 May 2001 13:39:26 -0400", utc(2001, 5, 15, 17, 39, 26)),
    # Abbreviation only, as in headers embedded in bodies (skilling-j/notes_inbox/301.).
    ("Tue, 15 May 2001 13:39:26 EDT", utc(2001, 5, 15, 17, 39, 26)),
    ("Wed, 3 Jan 2001 08:00:00 CST", utc(2001, 1, 3, 14, 0)),
    ("Wed, 3 Jan 2001 08:00:00 GMT", utc(2001, 1, 3, 8, 0)),
    # Abbreviation the stdlib doesn't know: handled by the extra table.
    ("Wed, 3 Jan 2001 09:00:00 CET", utc(2001, 1, 3, 8, 0)),
    ("Mon, 2 Jul 2001 10:00:00 CEST", utc(2001, 7, 2, 8, 0)),
    # No weekday.
    ("4 Dec 2000 09:58:00 -0800", utc(2000, 12, 4, 17, 58)),
])
def test_parses_to_utc(value, expected):
    result = parse_date(value)
    assert result.utc == expected
    assert result.utc.tzinfo == timezone.utc
    assert not result.is_sentinel
    assert codes(result) == []


def test_result_crossing_midnight_changes_the_date():
    assert parse_date("Sun, 31 Dec 2000 20:30:00 -0800 (PST)").utc == utc(2001, 1, 1, 4, 30)


def test_lotus_notes_sentinel_is_flagged():
    # Real value from skilling-j/discussion_threads/1089.
    result = parse_date("Mon, 31 Dec 1979 16:00:00 -0800 (PST)")
    assert result.utc == utc(1980, 1, 1)
    assert result.is_sentinel
    assert codes(result) == ["DATE_SENTINEL"]


def test_missing_timezone_assumes_utc_and_warns():
    result = parse_date("Mon, 4 Dec 2000 09:58:00 -0000")
    assert result.utc == utc(2000, 12, 4, 9, 58)
    assert codes(result) == ["DATE_NO_TIMEZONE"]


def test_unknown_abbreviation_assumes_utc_and_warns():
    result = parse_date("Mon, 4 Dec 2000 09:58:00 XYZT")
    assert result.utc == utc(2000, 12, 4, 9, 58)
    assert codes(result) == ["DATE_NO_TIMEZONE"]


def test_implausible_year_is_kept_but_warned():
    result = parse_date("Thu, 1 Jan 2037 00:00:00 +0000")
    assert result.utc.year == 2037
    assert codes(result) == ["DATE_OUT_OF_RANGE"]


@pytest.mark.parametrize("value", ["", "   ", "not a date", "Mon, 99 Foo 2000 99:99:99", "2001"])
def test_garbage_raises_value_error(value):
    with pytest.raises(ValueError):
        parse_date(value)
