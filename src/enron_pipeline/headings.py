"""Extract section headings from email text.

The spec doesn't define "headings", so a heading here is a short standalone line that is
formatted as a title in plain text, matching at least one of:

  1. underlined   - the next line is only '---' or '===' (3 or more)
  2. ALL CAPS     - at least 4 letters, no lowercase, blank line (or start) before it
  3. label line   - Title Case, at most 6 words, ends in ':' with nothing after,
                    blank line before it
  4. banner       - '*** Text ***', '=== Text ===', '+++ Text +++'
  5. numbered     - '1. Title', 'II. Title', 'A) Title'; Title Case, at most 8 words

Lines are skipped if they are longer than 70 characters or 10 words, hold an email
address, URL, phone number or clock time, look like a table row, are mostly
digits/punctuation, are placeholders like '[IMAGE]', are embedded header labels
(To:, cc:, Subject:, ...), are a salutation (first line of the text or of an embedded
message after its 'Subject:' line), are an all-caps signature name ('RICKY A. ARCHER'),
are a label introducing a URL ('Alternative URL:'), or are common sign-offs ('THANKS',
'**end**').
"""
from __future__ import annotations

import re

MAX_LEN = 70
MAX_WORDS = 10

_UNDERLINE_RE = re.compile(r"^\s*(-{3,}|={3,})\s*$")
_BANNER_RE = re.compile(r"^\s*([*=+#]){2,}\s*(?P<text>[^*=+#\s].*?)\s*\1{2,}\s*$")
_NUMBERED_RE = re.compile(r"^\s*(?:\d{1,2}|[IVX]{1,4}|[A-H])[.)]\s+(?P<text>\S.*)$")
_EXCLUDE_RE = re.compile(
    r"@|https?:|www\.|\.com\b|\d{3}[-.\s)]+\d{3}[-.\s]\d{4}|"
    r"\b\d{1,2}:\d{2}\s*([AP]M)?\b|"  # clock times: news-wire lines, schedules
    r"original message|forwarded by|forwarded message",
    re.I,
)
_PLACEHOLDER_RE = re.compile(r"^\[[^\]]*\]$")  # [IMAGE]
# Table rows: two 3+-space gaps, or one 4+-space/tab gap.
_TABLE_ROW_RE = re.compile(r"\S(?: {3,}|\t)\S.*\S(?: {3,}|\t)\S|\S(?: {4,}|\t+)\S")
_SALUTATION_RE = re.compile(r"^\s*(dear|hi|hello|hey)\b", re.I)
_CAPS_NAME_RE = re.compile(r"^[A-Z][A-Z'-]+\s+[A-Z]\.\s+[A-Z][A-Z'-]+$")
_URL_LINE_RE = re.compile(r"^\s*(https?:|www\.)", re.I)
_SUBJECT_LINE_RE = re.compile(r"^\s*Subject:", re.I)
_HEADER_LABELS = {"to", "cc", "bcc", "from", "sent", "date", "subject", "re", "fw", "fwd",
                  "importance", "attachments"}
_SIGN_OFFS = {"THANKS", "THANK YOU", "THANKS!", "REGARDS", "FYI", "CHEERS", "PLEASE",
              "URGENT", "BEST REGARDS", "THANKS AGAIN", "CONFIDENTIAL", "END", "EOM"}
_SMALL_WORDS = {"a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "into", "of",
                "on", "or", "the", "to", "vs", "via", "with", "&", "-", "/"}


def _letters(s: str) -> int:
    return sum(c.isalpha() for c in s)


def _is_title_case(text: str) -> bool:
    words = [w for w in re.split(r"\s+", text.strip(" :")) if w]
    if not words or not words[0][:1].isupper():
        return False
    return all(w[:1].isupper() or w[:1].isdigit() or w.lower() in _SMALL_WORDS for w in words)


def _candidate(line: str) -> bool:
    s = line.strip()
    if not s or len(s) > MAX_LEN or len(s.split()) > MAX_WORDS:
        return False
    if _EXCLUDE_RE.search(s) or _PLACEHOLDER_RE.match(s) or _TABLE_ROW_RE.search(s):
        return False
    if _CAPS_NAME_RE.match(s) or s.strip("*=+#! ").upper() in _SIGN_OFFS or _SALUTATION_RE.match(s):
        return False
    non_space = len(s.replace(" ", ""))
    return _letters(s) >= 2 and _letters(s) / non_space >= 0.5


def _is_header_label(s: str) -> bool:
    return s.split(":", 1)[0].strip().lower() in _HEADER_LABELS if ":" in s else False


def _match(lines: list[str], i: int) -> str | None:
    """Return the heading text if line i is a heading, else None."""
    line = lines[i]
    s = line.strip()
    prev_blank = i == 0 or not lines[i - 1].strip()

    m = _BANNER_RE.match(line)
    if m and _candidate(m.group("text")):
        return m.group("text").strip()
    if not _candidate(line) or _is_header_label(s):
        return None
    if (i + 1 < len(lines) and _UNDERLINE_RE.match(lines[i + 1]) and not _UNDERLINE_RE.match(line)
            and not s.endswith((".", ",", ";", "?"))):
        # A real underline is about as long as its text; a long rule under a signature isn't.
        if 0.5 * len(s) <= len(lines[i + 1].strip()) <= 1.5 * len(s) + 3:
            return s
    if (prev_blank and _letters(s) >= 4 and s == s.upper()
            and not s.endswith((".", ",", ";", "?")) and s.rstrip("!:") not in _SIGN_OFFS
            and s not in _SIGN_OFFS):
        return s.rstrip(":").strip() or None
    if prev_blank and s.endswith(":") and len(s.split()) <= 6 and _is_title_case(s):
        nxt = next((l for l in lines[i + 1:i + 3] if l.strip()), "")
        if not _URL_LINE_RE.match(nxt):
            return s[:-1].strip()
    m = _NUMBERED_RE.match(line)
    if m:
        text = m.group("text").strip()
        if len(text.split()) <= 8 and not text.endswith((".", ",", ";")) and _is_title_case(text):
            return s
    return None


def extract_headings(text: str | None) -> list[str]:
    if not text:
        return []
    lines = text.split("\n")
    # Lines where a salutation can appear: the first line, and the first line of each
    # embedded message (after its 'Subject:' line).
    openers: set[int] = set()
    expect = True
    for i, l in enumerate(lines):
        if _SUBJECT_LINE_RE.match(l):
            expect = True
        elif expect and l.strip():
            openers.add(i)
            expect = False
    found: list[str] = []
    seen: set[str] = set()
    for i in range(len(lines)):
        if i in openers and (lines[i].rstrip().endswith((":", ",")) or _SALUTATION_RE.match(lines[i])):
            continue  # salutation
        h = _match(lines, i)
        if h and h.lower() not in seen:
            seen.add(h.lower())
            found.append(h)
    return found
