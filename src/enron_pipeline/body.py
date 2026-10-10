"""Decode an email body according to its Content-Transfer-Encoding."""
from __future__ import annotations

import base64
import binascii
import quopri
import re

from .models import ParseWarning
from .rawfile import decode_text, normalize_newlines

_HEADER_LINE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*:")
# Headers that only appear in real message envelopes, never in a Lotus/Outlook reply block.
_TRANSPORT_HEADERS = {
    "return-path", "received", "message-id", "mime-version", "content-type",
    "content-transfer-encoding", "content-disposition", "x-mailer", "x-mimeole",
    "x-originating-ip", "x-priority", "x-msmail-priority", "delivered-to",
}


def strip_leaked_headers(body: str) -> tuple[str, bool, str | None]:
    """Remove an SMTP header block that leaked into the start of the body.

    About 105 Enron bodies begin with the original message's Return-Path/Received/
    Content-Type lines. The block is removed only if it has 3 or more header lines and at
    least one transport-only header, so a body that starts with a reply header block
    ('To: / cc: / Subject:') is left alone. Returns (body, stripped, leaked_content_type).
    """
    lines = body.split("\n")
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    names: list[str] = []
    content_type: str | None = None
    while i < len(lines) and lines[i].strip():
        line = lines[i]
        if _HEADER_LINE_RE.match(line):
            name = line.split(":", 1)[0].lower()
            names.append(name)
            if name == "content-type":
                content_type = line.split(":", 1)[1].strip()
        elif names:
            # Continuation; in leaked blocks these are often not indented.
            if names[-1] == "content-type" and content_type is not None:
                content_type += " " + line.strip()
        else:
            return body, False, None  # first line isn't a header
        i += 1
    if len(names) < 3 or not _TRANSPORT_HEADERS.intersection(names):
        return body, False, None
    return "\n".join(lines[i:]).lstrip("\n"), True, content_type


def decode_body(body: str, transfer_encoding: str | None, file_encoding: str) -> tuple[str, list[ParseWarning]]:
    """Return (decoded_text, warnings). Undeclared encodings are left untouched.

    `file_encoding` is the codec the file was decoded with, so the original bytes can be
    recovered before transfer-decoding.
    """
    cte = (transfer_encoding or "").strip().lower()
    if cte not in ("quoted-printable", "base64"):
        return body, []
    try:
        raw = body.encode(file_encoding)
        if cte == "quoted-printable":
            decoded = quopri.decodestring(raw)
        else:
            decoded = base64.b64decode(raw, validate=False)
        text, _ = decode_text(decoded)
        return normalize_newlines(text), []
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        return body, [ParseWarning("BODY_DECODE_FAILED", f"{cte}: {type(exc).__name__}: {exc}")]
