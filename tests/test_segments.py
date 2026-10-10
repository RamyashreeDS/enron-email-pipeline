from enron_pipeline.segments import split_body


def test_no_markers_everything_is_body():
    s = split_body("Hi Jeff,\n\nSee you at 3.\n\nKen\n", "Meeting")
    assert s.body == "Hi Jeff,\n\nSee you at 3.\n\nKen"
    assert s.forwarded_content is None and s.quoted_content is None and s.marker is None


def test_lotus_forward_wrapped_over_two_lines():
    text = ("FYI\n\n---------------------- Forwarded by Steven J Kean/NA/Enron on 06/13/2001\n"
            "08:00 AM ---------------------------\n\nOriginal text here.\n")
    s = split_body(text, "Policy")
    assert s.body == "FYI"
    assert s.forwarded_content.startswith("---------------------- Forwarded by Steven J Kean")
    assert s.forwarded_content.endswith("Original text here.")
    assert s.marker == "forward"


def test_forward_marker_is_forwarded_even_with_re_subject():
    s = split_body("ok\n----- Forwarded by A/B on 01/02/2001 03:04 PM -----\nx", "RE: thing")
    assert s.forwarded_content and s.quoted_content is None


def test_original_message_classified_by_subject():
    text = "My reply.\n\n-----Original Message-----\nFrom: A\nSent: Monday\nTo: B\nSubject: x\n\nold"
    assert split_body(text, "RE: x").quoted_content.startswith("-----Original Message-----")
    assert split_body(text, "FW: x").forwarded_content.startswith("-----Original Message-----")
    assert split_body(text, "Fwd[2]: x").forwarded_content is not None
    assert split_body(text, "x").quoted_content is not None


def test_original_message_with_question_mark_prefix():
    # The dataset replaced a non-ASCII character with '?' in some files.
    s = split_body("Thanks\n?-----Original Message-----\nFrom: A\nSent: B\n", "RE: y")
    assert s.body == "Thanks" and s.marker == "original_message"


def test_outlook_header_without_original_message_line():
    text = "See below.\n\nFrom: Jeff.Skilling@enron.com [mailto:Jeff.Skilling@enron.com]\nSent: Tuesday\nTo: x\n\nold"
    s = split_body(text, "RE: z")
    assert s.body == "See below." and s.marker == "outlook_header"


def test_lotus_reply_block_name_then_timestamp():
    text = ("Sounds good.\n\n\n\n\nKathy Mayfield\n12/13/2000 05:02 PM\n\n\n"
            "To: Rosalee Fleming/Corp/Enron@ENRON\ncc:  \nSubject: Thank you\n\nold text")
    s = split_body(text, "Re: Thank you")
    assert s.body == "Sounds good."
    assert s.quoted_content.startswith("Kathy Mayfield\n12/13/2000 05:02 PM")


def test_lotus_reply_block_blank_line_between_name_and_timestamp():
    text = ("Please come.\n\n\tRita Wynne @ ECT\n\n\t12/14/99 02:38:45 PM\n\n"
            "To: Janet H Wallis/HOU/ECT@ECT, Ami Chokshi/Corp/Enron@ENRON, Howard B\n"
            "Camp/HOU/ECT@ECT\ncc: Daren J Farmer/HOU/ECT@ECT\nSubject: Entex\n\nold")
    s = split_body(text, "Entex")
    assert s.body == "Please come."
    assert s.quoted_content.lstrip().startswith("Rita Wynne @ ECT")


def test_lotus_inline_attribution():
    text = ("Thanks!\n\n\nbill_morgan@kindermorgan.com on 12/13/2000 04:34:58 PM\n"
            "To: kathy.mayfield@enron.com\ncc:\n\nSubject: Gift\n\nold")
    s = split_body(text, "Re: Gift")
    assert s.body == "Thanks!" and s.marker == "lotus_header"


def test_memo_header_in_own_text_is_not_a_marker():
    # 'To: All Enron Employees / From: ... / Subject:' memos are the sender's own text.
    text = "To:\t\tAll Enron Employees\n\nFrom:\tRaymond Bowen, Jr.\n\nSubject:\tUpdate\n\nPost-Petition..."
    s = split_body(text, "Update")
    assert s.marker is None and s.body.startswith("To:")


def test_wrote_attribution_is_quoted():
    text = "Agreed.\n\nOn Thu, 16 Aug 2001, Fred wrote:\n> old line 1\n> old line 2\n"
    s = split_body(text, "Re: plan")
    assert s.body == "Agreed."
    assert s.quoted_content.startswith("On Thu, 16 Aug 2001, Fred wrote:")
    assert s.marker == "wrote"


def test_wrapped_wrote_attribution_starts_on_previous_line():
    s = split_body("Yes.\n\nOn Thu, 16 Aug 2001 00:14:01 Fred D Haulenbeek\nwrote:\n> hi", "Re: a")
    assert s.body == "Yes."
    assert s.quoted_content.startswith("On Thu, 16 Aug 2001")


def test_interleaved_quotes_leave_answers_in_body():
    text = "> Can you come?\nYes.\n> What time?\n3pm.\n"
    s = split_body(text, "Re: q")
    assert s.body == "Yes.\n3pm."
    assert s.quoted_content == "> Can you come?\n> What time?"


def test_fully_quoted_message_is_dequoted_into_body():
    text = "> Dear Jeff,\n>\n> Hello there.\n>\n> Luis\n"
    s = split_body(text, "contact")
    assert s.body == "Dear Jeff,\n\nHello there.\n\nLuis"
    assert s.quoted_content is None and s.marker == "fully_quoted"


def test_fully_quoted_message_with_inner_chain_is_split_again():
    text = "> Please see below.\n>\n> -----Original Message-----\n> From: A\n> Sent: B\n> old\n"
    s = split_body(text, "FW: x")
    assert s.body == "Please see below."
    assert s.forwarded_content.startswith("-----Original Message-----")


def test_attachment_line_does_not_stop_fully_quoted_detection():
    s = split_body("> Dear Jeff,\n> text\n\n - deck.ppt\n", "contact")
    assert s.body.startswith("Dear Jeff,") and s.marker == "fully_quoted"


def test_embedded_raw_message():
    text = ("best regards, j.c.\n\nReceived: from mail5 (x)\nFrom: a@b.com\nTo: c@d.com\n"
            "Subject: boat rides\nMime-Version: 1.0\n\noriginal")
    s = split_body(text, "[Fwd: boat rides]")
    assert s.body == "best regards, j.c."
    assert s.forwarded_content.startswith("Received: from mail5") and s.marker == "embedded_message"


def test_trailing_separator_lines_removed_from_body():
    s = split_body("Thanks\n\n________________________________\n-----Original Message-----\nFrom: x\n", "RE: a")
    assert s.body == "Thanks"


def test_empty_text():
    s = split_body("", "")
    assert s.body == "" and s.forwarded_content is None and s.quoted_content is None
