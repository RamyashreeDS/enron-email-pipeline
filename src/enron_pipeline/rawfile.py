"""Turn raw email bytes into a repaired header block and a body string.

Enron files use CRLF in the header block but mostly bare LF in bodies, and a few header
values contain a stray bare LF (e.g. a Subject broken across lines), which makes the
stdlib parser end the header block early and lose every later header.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .models import ParseError

# A header field name: printable ASCII except ':' and space, followed by ':'.
_FIELD_RE = re.compile(r"^[!-9;-~]+:")
# The header/body separator: the first empty line.
_SEPARATOR_RE = re.compile(r"\r?\n\r?\n")

TEXT_ENCODINGS = ("utf-8", "cp1252", "latin-1")  # latin-1 never fails


@dataclass
class RawEmail:
    header_text: str  # repaired header block, one "Name: value" per logical line, LF-separated
    body: str  # raw (still transfer-encoded) body, LF line endings
    encoding: str  # codec used to decode the file bytes
    header_repairs: int  # number of stray lines joined back into the previous header


def decode_text(raw: bytes) -> tuple[str, str]:
    """Decode bytes with the first codec that works; returns (text, codec)."""
    for enc in TEXT_ENCODINGS:
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise AssertionError("latin-1 decoding cannot fail")  # pragma: no cover


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def repair_header_block(block: str) -> tuple[list[str], int]:
    """Split a header block into logical header lines, re-joining broken values.

    Returns (lines, repairs). Continuation lines keep their leading whitespace so that
    standard RFC 5322 unfolding still applies later.
    """
    repairs = 0
    if "\r\n" in block:
        # CRLF is the real line terminator here; a bare LF is part of a value.
        block, repairs = re.subn(r"(?<!\r)\n", " ", block)
        physical = block.split("\r\n")
    else:
        physical = normalize_newlines(block).split("\n")

    lines: list[str] = []
    for line in physical:
        if _FIELD_RE.match(line):
            lines.append(line)
        elif not lines:
            raise ParseError("NO_HEADERS", f"First line is not a header: {line[:80]!r}")
        elif line[:1] in (" ", "\t"):
            lines.append(line)  # normal folded continuation
        else:
            # Non-indented, colon-less line inside the header block: part of the previous value.
            lines.append(" " + line)
            repairs += 1
    return lines, repairs


def split_raw(raw: bytes) -> RawEmail:
    if not raw.strip():
        raise ParseError("EMPTY_FILE", "File is empty or whitespace only")
    text, encoding = decode_text(raw)
    text = text.lstrip("﻿")
    m = _SEPARATOR_RE.search(text)
    if m:
        header_block, body = text[: m.start()], text[m.end():]
    else:
        header_block, body = text, ""  # headers only
    if not _FIELD_RE.match(header_block):
        raise ParseError("NO_HEADERS", f"File does not start with a header: {header_block[:80]!r}")
    lines, repairs = repair_header_block(header_block)
    return RawEmail("\n".join(lines), normalize_newlines(body), encoding, repairs)
