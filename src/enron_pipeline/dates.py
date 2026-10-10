"""Parse email Date headers and normalize them to UTC."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from .models import ParseWarning

# Lotus Notes "null date": shown as 'Mon, 31 Dec 1979 16:00:00 -0800 (PST)' = 1980-01-01 00:00 UTC.
SENTINEL_UTC = datetime(1980, 1, 1, tzinfo=timezone.utc)

# Plausible range for this corpus; dates outside it are kept but warned about.
PLAUSIBLE_YEARS = (1985, 2003)

# Abbreviations the stdlib doesn't know (it already handles UT/GMT/Z and the US zones
# EST/EDT/CST/CDT/MST/MDT/PST/PDT). Offsets in minutes east of UTC.
EXTRA_TZ_MINUTES = {
    "HST": -600, "AKST": -540, "AKDT": -480, "AST": -240, "ADT": -180,
    "NST": -210, "NDT": -150, "BST": 60, "IST": 60, "WET": 0, "WEST": 60,
    "CET": 60, "CEST": 120, "MET": 60, "MEST": 120, "EET": 120, "EEST": 180,
    "MSK": 180, "JST": 540, "KST": 540, "HKT": 480, "SGT": 480,
    "AWST": 480, "ACST": 570, "AEST": 600, "AEDT": 660, "NZST": 720, "NZDT": 780,
}

_TRAILING_TZ_RE = re.compile(r"\b([A-Z]{2,5})\)?\s*$")


@dataclass
class DateResult:
    utc: datetime
    is_sentinel: bool
    warnings: list[ParseWarning] = field(default_factory=list)


def parse_date(value: str) -> DateResult:
    """Parse an RFC 2822-style date into an aware UTC datetime.

    Raises ValueError if the value can't be parsed at all.
    """
    if not value or not value.strip():
        raise ValueError("empty date")
    warnings: list[ParseWarning] = []
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError) as exc:
        raise ValueError(f"unparseable date {value!r}: {exc}") from None
    if dt is None:  # defensive: older Pythons could return None instead of raising
        raise ValueError(f"unparseable date {value!r}")

    if dt.tzinfo is None:
        # No numeric offset and no zone the stdlib knows (or an explicit '-0000').
        m = _TRAILING_TZ_RE.search(value.strip())
        abbr = m.group(1) if m else None
        if abbr in EXTRA_TZ_MINUTES:
            dt = dt.replace(tzinfo=timezone(timedelta(minutes=EXTRA_TZ_MINUTES[abbr])))
        else:
            dt = dt.replace(tzinfo=timezone.utc)
            warnings.append(ParseWarning("DATE_NO_TIMEZONE", f"Assumed UTC for {value!r}"))

    utc = dt.astimezone(timezone.utc)
    is_sentinel = utc == SENTINEL_UTC
    if is_sentinel:
        warnings.append(ParseWarning("DATE_SENTINEL", f"Lotus Notes null date {value!r}"))
    elif not PLAUSIBLE_YEARS[0] <= utc.year <= PLAUSIBLE_YEARS[1]:
        warnings.append(ParseWarning("DATE_OUT_OF_RANGE", f"{utc.isoformat()} from {value!r}"))
    return DateResult(utc, is_sentinel, warnings)
