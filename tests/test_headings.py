import pytest

from enron_pipeline.headings import extract_headings


def test_underlined_heading():
    assert extract_headings("Intro text\n\nAgenda\n------\n1 thing") == ["Agenda"]


def test_long_rule_under_a_signature_is_not_an_underline():
    assert extract_headings("Thanks\n\nHarry\n--------------------------------------------") == []


def test_all_caps_heading_needs_blank_line_before():
    text = "Hello all,\n\nPOWER MARKETS UPDATE\nPrices rose.\n\nGAS\nFlat.\nNOT A HEADING\n"
    assert extract_headings(text) == ["POWER MARKETS UPDATE"]


def test_label_line():
    text = "Hi team,\n\nThis is the plan.\n\nAction Items:\n- call Bob\n\nNext Steps:\nwait"
    assert extract_headings(text) == ["Action Items", "Next Steps"]


def test_banner_heading():
    assert extract_headings("text\n\n*** Daily Gas Report ***\nmore") == ["Daily Gas Report"]


def test_numbered_title_case_sections():
    text = "Body\n\n1. Flow Control\nsome detail.\n\n2. Your Participation\nmore"
    assert extract_headings(text) == ["1. Flow Control", "2. Your Participation"]


def test_numbered_list_items_in_sentence_case_are_not_headings():
    assert extract_headings("Steps\n\n1. Scroll down to promotions & special offers\n") == []


@pytest.mark.parametrize("text", [
    "Gentlemen:\n\nThe meeting is set.",               # salutation (first line)
    "Ok\n\nSubject: Amoco\n\nDaren:\n\nYou put this in", # salutation in an embedded message
    "x\n\nDear Mr. Skilling\n\ntext",                   # salutation anywhere
    "x\n\n[IMAGE]\n",                                    # placeholder
    "x\n\n**end**\n",                                    # sign-off banner
    "x\n\nTHANKS!\n",                                    # sign-off
    "x\n\nRICKY A. ARCHER\nFuel Supply Manager",         # all-caps signature name
    "x\n\nCALL ME AT 713-853-5984\n",                    # phone number
    "x\n\nFOR MORE SEE WWW.CERA.COM\n",                  # URL
    "x\n\n11:45AM EDT 30-MAY-01 LEHMAN BROTHERS\n",      # news-wire timestamp line
    "x\n\nLS HPL KATY          30.000/ ENRON\n",          # table row
    "x\n\nAlternative URL:\nhttp://www.cera.com/x",      # label introducing a URL
    "x\n\nTo:\nSubject:\n",                              # embedded header labels
    "x\n\nWHAT DO YOU THINK?\n",                         # a question, not a title
])
def test_not_headings(text):
    assert extract_headings(text) == []


def test_duplicates_removed_case_insensitively():
    assert extract_headings("a\n\nSUMMARY\nx\n\nSummary:\ny") == ["SUMMARY"]


def test_empty():
    assert extract_headings("") == [] and extract_headings(None) == []
