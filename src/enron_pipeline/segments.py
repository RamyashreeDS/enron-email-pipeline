"""Separate an email body into the sender's own text, forwarded content and quoted replies.

Everything before the first forward/reply marker is the sender's text, except '>'-quoted
lines, which go to quoted_content. Everything from the first marker to the end (nested
chains included) goes to a single field, chosen by the marker type:

  forward markers ('----- Forwarded by', 'Forwarded message'),
  or a raw RFC 822 header block pasted into the text               -> forwarded_content
  '... wrote:'                                                       -> quoted_content
  Original Message / Outlook From-Sent / Lotus header blocks         -> forwarded_content if
                                                                        the subject starts with
                                                                        FW:/FWD:, else quoted
"""
from __future__ import annotations

import re
from dataclasses import dataclass

FORWARD, QUOTED = "forwarded", "quoted"

_FWD_BY_RE = re.compile(r"^\s*-{3,}\s*Forwarded by\b", re.I)
_FWD_MSG_RE = re.compile(r"^\s*-{3,}\s*(Begin\s+)?Forwarded\s+(message|e-?mail|mail)\b", re.I)
_BEGIN_FWD_RE = re.compile(r"^\s*Begin forwarded message:", re.I)
# A leading '?' appears where the dataset replaced a non-ASCII character.
_ORIGINAL_RE = re.compile(r"^[\s?]*-{2,}\s*Original Message\s*-{2,}", re.I)
_FROM_RE = re.compile(r"^\s*From:\s*\S", re.I)
_SENT_RE = re.compile(r"^\s*(Sent|Date):\s*\S", re.I)
_TO_RE = re.compile(r"^\s*To:", re.I)
_DATETIME = r"\d{1,2}/\d{1,2}/\d{2,4}\s+\d{1,2}:\d{2}(:\d{2})?\s*([AP]M)?"
_DATETIME_RE = re.compile(_DATETIME, re.I)
_DATETIME_LINE_RE = re.compile(rf"^\s*{_DATETIME}\s*$", re.I)
_CC_RE = re.compile(r"^\s*cc:", re.I)
_SUBJECT_RE = re.compile(r"^\s*Subject:", re.I)
_WROTE_RE = re.compile(r"\bwrote:\s*$", re.I)
_QUOTE_RE = re.compile(r"^\s*>")
_SEPARATOR_RE = re.compile(r"^\s*[-_=*~]{3,}\s*$")
_FW_SUBJECT_RE = re.compile(r"^\s*(fw|fwd)\s*(\[\d+\])?\s*:", re.I)
_HEADER_LINE_RE = re.compile(r"^([A-Za-z][A-Za-z0-9-]*):")
_TRANSPORT = {"received", "return-path", "message-id", "mime-version", "content-type",
              "content-transfer-encoding", "x-mailer", "delivered-to"}
_ENVELOPE = {"from", "subject", "date"}
_ATTACHMENT_LINE_RE = re.compile(r"^[ \t]*-[ \t]+\S.*\.[A-Za-z0-9]{2,4}[ \t]*$")


@dataclass
class Segments:
    body: str
    forwarded_content: str | None
    quoted_content: str | None
    marker: str | None  # which marker started the forwarded/quoted tail (diagnostic)


def _lotus_block_start(lines: list[str], t: int) -> int | None:
    """If line t ('To:') opens a Lotus Notes reply header, return the index where the block
    starts (the attribution above it), else None.

    Lotus blocks look like 'To: ... / cc: ... / Subject: ...', usually preceded by the
    sender and timestamp in one of several layouts:
        Kathy Mayfield                 Rita Wynne @ ECT
        12/13/2000 05:02 PM            <blank>
                                       12/14/99 02:38:45 PM
        Enron North America Corp.  From:  Megan Parker @ ENRON   03/09/2001 01:02 PM
        bill_morgan@kindermorgan.com on 12/13/2000 04:34:58 PM
    """
    subject = next((j for j in range(t + 1, min(len(lines), t + 12)) if _SUBJECT_RE.match(lines[j])), None)
    if subject is None:
        return None
    has_cc = any(_CC_RE.match(l) for l in lines[t + 1:subject])

    k = t - 1
    while k >= 0 and not lines[k].strip():
        k -= 1
    if k < 0 or not _DATETIME_RE.search(lines[k]):
        return t if has_cc else None
    start = k
    if _DATETIME_LINE_RE.match(lines[k]):
        # Timestamp on its own line: the sender's name is just above (at most one blank between).
        n = k - 1
        if n >= 0 and not lines[n].strip():
            n -= 1
        if n >= 0 and lines[n].strip() and len(lines[n].strip()) <= 100 \
                and not lines[n].rstrip().endswith((".", "!", "?")):
            start = n
    elif re.match(r"^\s+From:", lines[k]):
        # Letterhead layout: an indented company line ('\tEnron North America Corp.') sits above.
        n = k - 1
        while n >= 0 and k - n <= 3 and not lines[n].strip():
            n -= 1
        if n >= 0 and lines[n][:1] in (" ", "\t") and len(lines[n].strip()) <= 60 and ":" not in lines[n]:
            start = n
    return start


def _is_embedded_message(lines: list[str], i: int) -> bool:
    """A raw RFC 822 header block (3+ header lines, a transport header, and From/Subject/Date)
    starting at line i, e.g. a bounce or a Netscape '[Fwd: ...]' forward."""
    if not _HEADER_LINE_RE.match(lines[i]) or (i > 0 and lines[i - 1].strip()):
        return False
    names = set()
    count = 0
    for line in lines[i:i + 60]:
        if not line.strip():
            break
        m = _HEADER_LINE_RE.match(line)
        if m:
            names.add(m.group(1).lower())
            count += 1
    return count >= 3 and bool(names & _TRANSPORT) and bool(names & _ENVELOPE)


def find_marker(lines: list[str]) -> tuple[int, str] | None:
    """Return (line_index, marker_kind) of the first forward/reply marker, or None."""
    for i, line in enumerate(lines):
        if not line.strip() or _QUOTE_RE.match(line):
            continue
        if _FWD_BY_RE.match(line) or _FWD_MSG_RE.match(line) or _BEGIN_FWD_RE.match(line):
            return i, "forward"
        if _ORIGINAL_RE.match(line):
            return i, "original_message"
        if _is_embedded_message(lines, i):
            return i, "embedded_message"
        if _FROM_RE.match(line) and any(_SENT_RE.match(l) for l in lines[i + 1:i + 4]):
            return i, "outlook_header"
        if _TO_RE.match(line):
            start = _lotus_block_start(lines, i)
            if start is not None:
                return start, "lotus_header"
        if _WROTE_RE.search(line):
            # 'On <date>, <name>\nwrote:' — start at the 'On' line when the attribution wraps.
            if line.strip().lower() == "wrote:" and i > 0 and lines[i - 1].strip():
                return i - 1, "wrote"
            return i, "wrote"
    return None


def _category(marker: str, subject: str) -> str:
    if marker in ("forward", "embedded_message"):
        return FORWARD
    if marker == "wrote":
        return QUOTED
    return FORWARD if _FW_SUBJECT_RE.match(subject or "") else QUOTED


def _dequote(lines: list[str]) -> str:
    """Remove one level of '>' quoting."""
    return "\n".join(re.sub(r"^\s*> ?", "", l, count=1) for l in lines)


def _clean(lines: list[str]) -> str:
    """Join lines, dropping leading/trailing blank and separator-only lines."""
    while lines and (not lines[-1].strip() or _SEPARATOR_RE.match(lines[-1])):
        lines = lines[:-1]
    while lines and not lines[0].strip():
        lines = lines[1:]
    return "\n".join(l.rstrip() for l in lines)


def split_body(text: str, subject: str = "", _depth: int = 0) -> Segments:
    lines = text.split("\n")
    found = find_marker(lines)
    head, tail = (lines[:found[0]], lines[found[0]:]) if found else (lines, [])

    own = [l for l in head if not _QUOTE_RE.match(l)]
    inline_quotes = [l for l in head if _QUOTE_RE.match(l)]

    body = _clean(own)
    forwarded = quoted = None
    if tail:
        tail_text = _clean(tail)
        if _category(found[1], subject) == FORWARD:
            forwarded = tail_text or None
        else:
            quoted = tail_text or None
    if inline_quotes:
        q = _clean(inline_quotes)
        if q:
            quoted = f"{q}\n\n{quoted}" if quoted else q

    # A message that is entirely '>'-quoted (forwarded inline as a quote): its text is what
    # the sender sent, so it becomes the body. Lotus attachment-list lines (' - file.ppt')
    # and separators don't count as the sender's own text here.
    own_text = [l for l in own if l.strip() and not _ATTACHMENT_LINE_RE.match(l)
                and not _SEPARATOR_RE.match(l)]
    if not own_text and not tail and inline_quotes:
        dequoted = _dequote(head)
        if _depth < 5:
            # Split the unquoted text again: it may hold its own forward/reply chain.
            inner = split_body(dequoted, subject, _depth + 1)
            return Segments(inner.body, inner.forwarded_content, inner.quoted_content, "fully_quoted")
        return Segments(_clean(dequoted.split("\n")), None, None, "fully_quoted")

    marker = found[1] if found else ("inline_quote" if inline_quotes else None)
    return Segments(body, forwarded, quoted, marker)
