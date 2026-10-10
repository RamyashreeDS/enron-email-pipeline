"""Infer whether an email had an attachment.

Only file-level evidence counts (decision D7): a multipart MIME type, an attachment
disposition, or a reference to a named file in the forms Lotus Notes and Outlook leave in
the text. The word "attached" alone is not used (6,594 files, mostly prose), and
'[IMAGE]' placeholders from HTML newsletters are not attachments.
"""
from __future__ import annotations

import re

_EXT = (r"(?:docx?|xls[xmb]?|pptx?|pps|pdf|zip|rtf|wpd|txt|csv|jpe?g|gif|bmp|tiff?|png|pcx|"
        r"html?|msg|eml|mpp|vsd|exe|wav|mp3|avi|mpe?g|xml|dat|tar|gz)")

_PATTERNS = [
    # <<Report.xls>>  (Outlook)
    re.compile(rf"<<\s*[^<>\n]+\.{_EXT}\s*>>", re.I),
    # << File: GENIE.doc >>  (Lotus Notes); URLs in this form are links, not files
    re.compile(r"<<\s*File:\s*(?!\s*(?:https?:|www\.|mailto:))[^<>\s][^<>]*>>", re.I),
    # " - resume.doc" on its own line  (Lotus Notes attachment list)
    re.compile(rf"^[ \t]*-[ \t]+\S[^\n]*\.{_EXT}[ \t]*$", re.I | re.M),
    # Embedded objects converted from Notes/Outlook
    re.compile(r"\bEmbedded (?:StdOleLink|Picture)\b"),
    # MIME parts that leaked into the body
    re.compile(r"^Content-Disposition:\s*(?:attachment|inline;\s*filename)", re.I | re.M),
]

_MULTIPART_RE = re.compile(r"^\s*multipart/(mixed|related)\b", re.I)


def has_attachment(content_type: str | None, leaked_content_type: str | None, text: str) -> bool:
    for ct in (content_type, leaked_content_type):
        if ct and _MULTIPART_RE.match(ct):
            return True
    return any(p.search(text) for p in _PATTERNS)
