"""Read header values as plain, unfolded strings."""
from __future__ import annotations

import re
from email.message import Message
from email.parser import HeaderParser
from email.policy import compat32

_FOLD_RE = re.compile(r"\r?\n(?=[ \t])")


def unfold(value: str) -> str:
    """RFC 5322 unfolding: drop the line break, keep the folding whitespace."""
    return _FOLD_RE.sub("", value)


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
