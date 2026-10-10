"""Data types shared across the pipeline."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime


class ParseError(Exception):
    """An email could not be turned into a valid record.

    `code` is a stable reason code (e.g. MISSING_TO) used in error_log.txt and stats;
    `detail` is a human-readable explanation.
    """

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass
class ParseWarning:
    """A non-fatal data problem found while parsing (the record is still kept)."""

    code: str
    detail: str = ""


@dataclass
class ParsedEmail:
    # Mandatory fields (spec section 3.1)
    message_id: str
    date: datetime  # timezone-aware, UTC
    from_address: str
    to_addresses: list[str]
    subject: str
    body: str
    source_file: str

    # True when the date is the Lotus Notes "null date" (1979-12-31 16:00 PST = 1980-01-01 UTC),
    # so later stages never treat that copy as the earliest original.
    date_is_sentinel: bool = False

    # Optional fields (spec section 3.2).
    cc_addresses: list[str] = field(default_factory=list)
    bcc_addresses: list[str] = field(default_factory=list)
    x_from: str | None = None
    x_to: str | None = None
    x_cc: str | None = None
    x_bcc: str | None = None
    x_folder: str | None = None
    x_origin: str | None = None
    content_type: str | None = None
    has_attachment: bool | None = None
    forwarded_content: str | None = None
    quoted_content: str | None = None
    headings: str | None = None

    # Diagnostics (not spec fields): which marker split the body, and whether an SMTP header
    # block that leaked into the body was removed.
    split_marker: str | None = None
    leaked_headers_stripped: bool = False

    warnings: list[ParseWarning] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["date"] = self.date.isoformat()
        return d
