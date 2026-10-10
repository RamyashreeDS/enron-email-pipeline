"""Extraction statistics: totals, failure reasons, per-mailbox counts, field completeness."""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field

from .models import ParsedEmail

# Fields whose completeness is reported. Stage 2 adds the optional fields here.
COMPLETENESS_FIELDS = (
    "message_id", "date", "from_address", "to_addresses", "subject", "body", "source_file",
)


def _mailbox_of(source_file: str) -> str:
    parts = source_file.split("/")
    return parts[1] if len(parts) > 2 else "(unknown)"


def _filled(value) -> bool:
    if value is None:
        return False
    if isinstance(value, (str, list)):
        return len(value) > 0
    return True


@dataclass
class ExtractionStats:
    found: int = 0
    parsed: int = 0
    failed: int = 0
    runtime_seconds: float = 0.0
    failures_by_reason: Counter = field(default_factory=Counter)
    warnings_by_code: Counter = field(default_factory=Counter)
    filled: Counter = field(default_factory=Counter)
    sentinel_dates: int = 0
    per_mailbox: dict[str, Counter] = field(default_factory=dict)

    def _mb(self, source_file: str) -> Counter:
        return self.per_mailbox.setdefault(_mailbox_of(source_file), Counter())

    def record_found(self, source_file: str) -> None:
        self.found += 1
        self._mb(source_file)["found"] += 1

    def record_success(self, email: ParsedEmail) -> None:
        self.parsed += 1
        self._mb(email.source_file)["parsed"] += 1
        for name in COMPLETENESS_FIELDS:
            if _filled(getattr(email, name)):
                self.filled[name] += 1
        if email.date_is_sentinel:
            self.sentinel_dates += 1

    def record_failure(self, source_file: str, code: str) -> None:
        self.failed += 1
        self._mb(source_file)["failed"] += 1
        self.failures_by_reason[code] += 1

    def record_warning(self, code: str) -> None:
        self.warnings_by_code[code] += 1

    @property
    def consistent(self) -> bool:
        return self.parsed + self.failed == self.found

    def completeness(self) -> dict[str, float]:
        return {n: (100.0 * self.filled[n] / self.parsed if self.parsed else 0.0) for n in COMPLETENESS_FIELDS}

    def to_dict(self) -> dict:
        return {
            "files_found": self.found,
            "parsed": self.parsed,
            "failed": self.failed,
            "success_rate_pct": round(100.0 * self.parsed / self.found, 2) if self.found else 0.0,
            "runtime_seconds": round(self.runtime_seconds, 2),
            "counts_consistent": self.consistent,
            "failures_by_reason": dict(self.failures_by_reason.most_common()),
            "per_mailbox": {mb: dict(c) for mb, c in sorted(self.per_mailbox.items())},
            "field_completeness_pct": {k: round(v, 2) for k, v in self.completeness().items()},
            "sentinel_dates": self.sentinel_dates,
            "warnings_by_code": dict(self.warnings_by_code.most_common()),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def report(self) -> str:
        d = self.to_dict()
        out = [
            "Extraction statistics",
            "=====================",
            f"Files found:        {self.found:>8,}",
            f"Parsed:             {self.parsed:>8,}  ({d['success_rate_pct']}%)",
            f"Failed:             {self.failed:>8,}",
            f"Runtime:            {d['runtime_seconds']:>8}s",
            f"Counts consistent:  {'yes' if self.consistent else 'NO (parsed + failed != found)'}",
            "",
            "Failures by reason:",
        ]
        out += [f"  {code:<20}{n:>8,}" for code, n in self.failures_by_reason.most_common()] or ["  (none)"]
        out += ["", "Per mailbox:              found   parsed   failed"]
        for mb, c in sorted(self.per_mailbox.items()):
            out.append(f"  {mb:<20}{c['found']:>9,}{c['parsed']:>9,}{c['failed']:>9,}")
        out += ["", "Field completeness (% of parsed records with a non-empty value):"]
        out += [f"  {name:<20}{pct:>7.2f}%" for name, pct in self.completeness().items()]
        out += ["", f"Sentinel (1980-01-01 UTC) dates: {self.sentinel_dates:,}", "", "Warnings by type:"]
        out += [f"  {code:<20}{n:>8,}" for code, n in self.warnings_by_code.most_common()] or ["  (none)"]
        return "\n".join(out)
