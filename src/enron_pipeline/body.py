"""Decode an email body according to its Content-Transfer-Encoding."""
from __future__ import annotations

import base64
import binascii
import quopri

from .models import ParseWarning
from .rawfile import decode_text, normalize_newlines


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
