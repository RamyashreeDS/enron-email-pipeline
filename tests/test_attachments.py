import pytest

from enron_pipeline.attachments import has_attachment


@pytest.mark.parametrize("text", [
    "Please review. <<Q3 Forecast.xls>>",
    " <<LEO_Equity_Story_C(10)_English.ppt>>",
    "Here it is << File: GENIE.doc >>",
    "Signed,\n\n - resume.doc\n",
    "\n - YPO 2001.jpg\n",
    "Embedded Picture (Device Independent Bitmap)",
    "Embedded StdOleLink",
    "------=_Part_1\nContent-Disposition: attachment; filename=x.pdf\n",
])
def test_file_level_evidence_counts(text):
    assert has_attachment("text/plain", None, text)


@pytest.mark.parametrize("text", [
    "I have attached the file you asked for.",     # the word alone doesn't count (D7)
    "See attachment.",
    "[IMAGE]\n[IMAGE]",                            # newsletter image placeholders
    "<< File: http://www.SkiHomeRental.com >>",    # a link, not a file
    "- see the notes above",                       # dash line without a filename
    "Price was <<low>> today",
    "",
])
def test_no_file_level_evidence(text):
    assert not has_attachment("text/plain", None, text)


def test_multipart_content_type():
    assert has_attachment("multipart/mixed; boundary=x", None, "")
    assert has_attachment(None, "multipart/related; boundary=y", "")
    assert not has_attachment("multipart/alternative; boundary=z", None, "")
