"""Parse address-list headers (From, To, Cc, Bcc) into clean email addresses.

Real Enron values include Lotus Notes artifacts such as 'e-mail <.ray@enron.com>' (leading
dot) and quote-mangled local parts like '"\\'thomas.perkins\\'"@enron.com'. Each list item is
parsed on its own so that one malformed item can't take down the rest of the list
(Python's strict getaddresses() returns [('', '')] for a whole header if any part is bad).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from email.utils import getaddresses

# local@domain.tld; no whitespace, brackets, quotes, separators or a second '@'.
_BAD = r"\s<>()\[\],;:\"'@"
_VALID_RE = re.compile(rf"^[^{_BAD}]+@[^{_BAD}.][^{_BAD}]*\.[^{_BAD}.]+$")


@dataclass
class AddressList:
    valid: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)  # raw items that held no usable address


def split_items(value: str) -> list[str]:
    """Split on commas that are outside double quotes and angle brackets."""
    items, buf = [], []
    in_quotes = False
    depth = 0
    prev = ""
    for ch in value:
        if ch == '"' and prev != "\\":
            in_quotes = not in_quotes
        elif not in_quotes and ch == "<":
            depth += 1
        elif not in_quotes and ch == ">" and depth:
            depth -= 1
        if ch == "," and not in_quotes and depth == 0:
            items.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        prev = ch
    items.append("".join(buf))
    return [i.strip() for i in items if i.strip()]


def clean_address(addr: str) -> str:
    """Strip quotes, angle brackets and leading/trailing dots; lowercase."""
    addr = addr.strip().strip("<>").replace('"', "").replace("'", "")
    local, at, domain = addr.rpartition("@")
    if at:
        addr = f"{local.strip('.')}@{domain.strip('.')}"
    return addr.strip().lower()


def is_valid_address(addr: str) -> bool:
    return bool(_VALID_RE.match(addr))


def _addresses_in_item(item: str) -> list[str]:
    parsed = [addr for _, addr in getaddresses([item]) if addr]
    if not parsed and "@" in item:
        # getaddresses gave up on a mangled item; fall back to the bracketed part or the raw text.
        m = re.search(r"<([^<>]*)>\s*$", item)
        parsed = [m.group(1) if m else item]
    return parsed


def parse_address_list(value: str | None) -> AddressList:
    result = AddressList()
    if not value:
        return result
    seen: set[str] = set()
    for item in split_items(re.sub(r"\s+", " ", value)):
        if re.fullmatch(r"[^@<>\"]*:\s*;?", item):
            continue  # empty group, e.g. "undisclosed-recipients:;"
        found = False
        for raw_addr in _addresses_in_item(item):
            addr = clean_address(raw_addr)
            if is_valid_address(addr):
                found = True
                if addr not in seen:
                    seen.add(addr)
                    result.valid.append(addr)
        if not found:
            result.rejected.append(item)
    return result
