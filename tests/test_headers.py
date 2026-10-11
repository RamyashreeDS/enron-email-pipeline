"""RFC 2047 encoded words in text headers (regression: 21 Enron subjects were stored still
encoded, and some decoded to control characters)."""
from pathlib import Path

import pytest

from enron_pipeline.headers import Headers, decode_encoded_words
from enron_pipeline.parser import parse_file

FIXTURES = Path(__file__).parent / "fixtures" / "enron"


@pytest.mark.parametrize("raw, expected", [
    # Real values from the dataset.
    ("=?ANSI_X3.4-1968?Q?Cuiab=3F_-_Brazil_-_New_Phone_Number?=", "Cuiab? - Brazil - New Phone Number"),
    ("=?ANSI_X3.4-1968?Q?New_Employee_Service_=01)_Turbo_Tax?=", "New Employee Service ?) Turbo Tax"),
    ("=?ANSI_X3.4-1968?Q?Statements_concerning_last_week=01,s_price_spike?=",
     "Statements concerning last week?,s price spike"),  # \x01 followed by a real comma
    # Two adjacent encoded words: the space between them is not part of the text (RFC 2047).
    ("=?ANSI_X3.4-1968?Q?Important_Information_from_Pricewater?= "
     "=?ANSI_X3.4-1968?Q?houseCoopers_IAS_for_=3FLouise=3F_Kitchen?=",
     "Important Information from PricewaterhouseCoopers IAS for ?Louise? Kitchen"),
    # Plain text around an encoded word is kept.
    ("RE: =?ANSI_X3.4-1968?Q?Enron_Business_=01)_a_RAC?=", "RE: Enron Business ?) a RAC"),
    # Other charsets and the B (base64) encoding.
    ("=?utf-8?B?Q2Fmw6kgbWVldGluZw==?=", "Café meeting"),
    ("=?iso-8859-1?Q?Jos=E9?= update", "José update"),
    # A newline or tab smuggled into a header: newline -> '?', tab kept.
    ("=?utf-8?Q?line1=0Aline2=09end?=", "line1?line2\tend"),
])
def test_decode_encoded_words(raw, expected):
    assert decode_encoded_words(raw) == expected


@pytest.mark.parametrize("value", [
    None, "", "Re: Q4 plan", "Plain subject with =? in it", "Price =?? unknown",
    "José already decoded",
    "=?bogus-charset?Q?abc?=",  # unknown charset: decoded leniently, not crashed
])
def test_values_without_valid_encoded_words_are_safe(value):
    result = decode_encoded_words(value)
    assert result is None or isinstance(result, str)
    if value and "=?" not in value:
        assert result == value


def test_unknown_charset_falls_back():
    assert decode_encoded_words("=?bogus-charset?Q?abc?=") == "abc"


def test_get_text_decodes_but_get_does_not():
    h = Headers("Subject: =?ANSI_X3.4-1968?Q?Cuiab=3F_-_Brazil?=\nX-From: =?utf-8?Q?Jos=C3=A9?= <j@x.com>")
    assert h.get("Subject") == "=?ANSI_X3.4-1968?Q?Cuiab=3F_-_Brazil?="
    assert h.get_text("Subject") == "Cuiab? - Brazil"
    assert h.get_text_optional("X-From") == "José <j@x.com>"
    assert h.get_text("Missing") is None


def test_real_email_subject_is_decoded():
    # farmer-d/all_documents/3604.: 'Subject: =?ANSI_X3.4-1968?Q?New_Employee_Service_=01)_Turbo_Tax?='
    e = parse_file(FIXTURES / "farmer-d__all_documents__3604.txt", "maildir/farmer-d/all_documents/3604.")
    assert e.subject == "New Employee Service ?) Turbo Tax"
    assert all(ord(c) >= 32 or c == "\t" for c in e.subject)
