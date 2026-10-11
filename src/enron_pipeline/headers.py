"""Read header values as plain, unfolded strings."""
from __future__ import annotations

import re
from email.errors import HeaderParseError
from email.header import decode_header
from email.message import Message
from email.parser import HeaderParser
from email.policy import compat32

_FOLD_RE = re.compile(r"\r?\n(?=[ \t])")
# C0 control characters except tab, plus DEL. In decoded headers they become '?'.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")


def unfold(value: str) -> str:
    """RFC 5322 unfolding: drop the line break, keep the folding whitespace."""
    return _FOLD_RE.sub("", value)


def _decode_bytes(chunk: bytes, charset: str | None) -> str:
    if charset is None:
        # Plain text between encoded words; decode_header returns it as raw-unicode-escape bytes.
        return chunk.decode("raw-unicode-escape")
    for enc in (charset, "utf-8", "cp1252", "latin-1"):  # latin-1 never fails
        try:
            return chunk.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    raise AssertionError("latin-1 decoding cannot fail")  # pragma: no cover


def decode_encoded_words(value: str | None) -> str | None:
    """Decode RFC 2047 encoded words ('=?charset?Q?...?=') in a text header.

    In the Enron data, 21 subjects were stored still encoded (charset ANSI_X3.4-1968), and
    some decode to control characters (e.g. \\x01 where a curly quote was). Control
    characters in the decoded text become '?', the same character the dataset uses for
    other characters it couldn't represent. Values without encoded words are returned
    unchanged.
    """
    if not value or "=?" not in value:
        return value
    try:
        parts = decode_header(value)
    except (HeaderParseError, ValueError):
        return value
    if all(isinstance(chunk, str) for chunk, _ in parts):
        return value  # nothing was actually encoded
    decoded = "".join(chunk if isinstance(chunk, str) else _decode_bytes(chunk, charset)
                      for chunk, charset in parts)
    return _CONTROL_RE.sub("?", decoded)


class Headers:
    def __init__(self, header_text: str):
        self._msg: Message = HeaderParser(policy=compat32).parsestr(header_text + "\n\n")

    def has(self, name: str) -> bool:
        return self._msg.get(name) is not None

    def get(self, name: str) -> str | None:
        """First occurrence of `name`, unfolded and stripped; None if absent.

        An empty value is returned as "" so callers can tell "absent" from "empty".
        """
        value = self._msg.get(name)
        if value is None:
            return None
        return unfold(str(value)).strip()

    def get_optional(self, name: str) -> str | None:
        """Like get(), but an empty value becomes None."""
        value = self.get(name)
        return value or None

    def get_text(self, name: str) -> str | None:
        """Like get(), with RFC 2047 encoded words decoded. For text headers only (Subject,
        X-From, ...); address headers are parsed by addresses.py, because a decoded display
        name may contain a comma that would split an address list."""
        return decode_encoded_words(self.get(name))

    def get_text_optional(self, name: str) -> str | None:
        return self.get_text(name) or None
