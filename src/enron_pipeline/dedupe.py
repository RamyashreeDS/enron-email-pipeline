"""Task 3: find duplicate emails, flag them in the database, and report them.

A duplicate group is a set of emails with the same from_address, the same subject after
removing Re:/Fw:/Fwd: prefixes, and bodies at least 90% similar. The earliest email is
the original; every other member is flagged. Decisions X1-X10: notes/dedupe_decisions.md.

Usage:
    python -m enron_pipeline.dedupe --db data/enron.db
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz

from . import db

log = logging.getLogger("enron_pipeline.dedupe")

THRESHOLD = 90.0
REPORT_COLUMNS = ("duplicate_message_id", "original_message_id", "subject", "from_address",
                  "duplicate_date", "original_date", "similarity_score")
SAME_TIMESTAMP, DIFFERENT_TIMESTAMPS = "same_timestamp", "different_timestamps"
BODY, FORWARDED, QUOTED = "body", "forwarded", "quoted"  # which text two emails were compared on

# Any number of leading Re:/Fw:/Fwd: prefixes, in any case, including forms like 'RE[2]:'.
_PREFIX_RE = re.compile(r"^\s*(?:(?:re|fw|fwd)\s*(?:\[\d+\])?\s*:\s*)+", re.I)
_WS_RE = re.compile(r"\s+")


def normalize_subject(subject: str) -> str:
    """Remove leading Re:/Fw:/Fwd: prefixes; the rest is kept exactly (X7)."""
    return _PREFIX_RE.sub("", subject or "")


def collapse_whitespace(text: str | None) -> str:
    """Every run of spaces, tabs and newlines becomes one space (X6, option ii)."""
    return _WS_RE.sub(" ", text or "").strip()


@dataclass
class Candidate:
    id: int
    message_id: str
    from_address: str
    subject: str
    date: str  # 'YYYY-MM-DD HH:MM:SS' UTC
    is_sentinel: bool
    body: str  # whitespace-collapsed
    forwarded: str  # whitespace-collapsed
    quoted: str  # whitespace-collapsed

    @property
    def order_key(self) -> tuple:
        # X4: sentinel-dated copies after all real dates; X2: lowest id breaks ties.
        return (self.is_sentinel, self.date, self.id)


def similarity(a: Candidate, b: Candidate) -> tuple[float, str]:
    """Return (score 0-100, which text was compared).

    X3: compare the bodies; if both bodies are empty, compare forwarded_content; if both
    forwarded parts are empty too, compare quoted_content (X3 extension).
    """
    if a.body or b.body:
        return fuzz.ratio(a.body, b.body), BODY
    if a.forwarded or b.forwarded:
        return fuzz.ratio(a.forwarded, b.forwarded), FORWARDED
    return fuzz.ratio(a.quoted, b.quoted), QUOTED


@dataclass
class Member:
    email: Candidate
    score: float  # similarity to the group's original
    compared: str  # BODY, FORWARDED or QUOTED


@dataclass
class Group:
    original: Candidate
    duplicates: list[Member] = field(default_factory=list)

    @property
    def size(self) -> int:
        return 1 + len(self.duplicates)

    @property
    def category(self) -> str:
        dates = {self.original.date} | {m.email.date for m in self.duplicates}
        return SAME_TIMESTAMP if len(dates) == 1 else DIFFERENT_TIMESTAMPS

    @property
    def notification_target(self) -> Candidate:
        """The latest duplicate (by the same ordering; highest id on a tie)."""
        return max((m.email for m in self.duplicates), key=lambda e: e.order_key)

    def used_rule(self, compared: str) -> bool:
        return any(m.compared == compared for m in self.duplicates)


def group_bucket(emails: Iterable[Candidate], threshold: float = THRESHOLD) -> tuple[list[Group], int]:
    """X5: in order, each email joins the first group whose original it matches at
    >= threshold, otherwise it starts a new group. Returns (groups with 2+ members,
    number of comparisons)."""
    groups: list[Group] = []
    comparisons = 0
    for email in sorted(emails, key=lambda e: e.order_key):
        for g in groups:
            comparisons += 1
            score, compared = similarity(g.original, email)
            if score >= threshold:
                g.duplicates.append(Member(email, score, compared))
                break
        else:
            groups.append(Group(email))
    return [g for g in groups if g.duplicates], comparisons


def load_candidates(conn: sqlite3.Connection) -> list[Candidate]:
    rows = conn.execute(
        "SELECT id, message_id, from_address, subject, date, date_is_sentinel, body, forwarded_content, "
        "quoted_content FROM emails ORDER BY id")
    return [Candidate(r[0], r[1], r[2], r[3], r[4], bool(r[5]), collapse_whitespace(r[6]),
                      collapse_whitespace(r[7]), collapse_whitespace(r[8])) for r in rows]


def bucket(emails: Iterable[Candidate]) -> dict[tuple[str, str], list[Candidate]]:
    buckets: dict[tuple[str, str], list[Candidate]] = defaultdict(list)
    for e in emails:
        buckets[(e.from_address, normalize_subject(e.subject))].append(e)
    return buckets


def find_groups(emails: list[Candidate], threshold: float = THRESHOLD) -> tuple[list[Group], dict]:
    buckets = bucket(emails)
    groups: list[Group] = []
    comparisons = 0
    candidate_buckets = 0
    for key in sorted(buckets):  # deterministic order
        members = buckets[key]
        if len(members) < 2:
            continue
        candidate_buckets += 1
        found, n = group_bucket(members, threshold)
        groups.extend(found)
        comparisons += n
    groups.sort(key=lambda g: g.original.order_key)
    info = {"emails_scanned": len(emails), "buckets": len(buckets),
            "candidate_buckets": candidate_buckets, "comparisons": comparisons}
    return groups, info


def apply_flags(conn: sqlite3.Connection, groups: list[Group]) -> None:
    """X9: clear every duplicate flag, then set the new ones, in one transaction.
    notification_sent / notification_date are never touched."""
    conn.execute("BEGIN")
    try:
        conn.execute("UPDATE emails SET is_duplicate = 0, duplicate_of = NULL "
                     "WHERE is_duplicate = 1 OR duplicate_of IS NOT NULL")
        conn.executemany(
            "UPDATE emails SET is_duplicate = 1, duplicate_of = ? WHERE id = ?",
            [(g.original.message_id, m.email.id) for g in groups for m in g.duplicates])
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def write_report(path: str | Path, groups: list[Group]) -> int:
    """One row per flagged email, ordered by original then duplicate (X10)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = 0
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(REPORT_COLUMNS)
        for g in groups:
            for m in sorted(g.duplicates, key=lambda m: m.email.order_key):
                w.writerow((m.email.message_id, g.original.message_id, m.email.subject,
                            m.email.from_address, m.email.date, g.original.date, f"{m.score:.1f}"))
                rows += 1
    return rows


def build_stats(groups: list[Group], info: dict, runtime: float) -> dict:
    def summary(gs: list[Group]) -> dict:
        flagged = sum(len(g.duplicates) for g in gs)
        return {
            "groups": len(gs),
            "emails_flagged": flagged,
            "average_group_size": round((flagged + len(gs)) / len(gs), 2) if gs else 0.0,
        }

    by_cat = {cat: [g for g in groups if g.category == cat] for cat in (SAME_TIMESTAMP, DIFFERENT_TIMESTAMPS)}
    scores = [m.score for g in groups for m in g.duplicates]
    return {
        **summary(groups),
        "by_category": {
            SAME_TIMESTAMP: {"description": "every copy has the same timestamp (one email filed in several folders)",
                             **summary(by_cat[SAME_TIMESTAMP])},
            DIFFERENT_TIMESTAMPS: {"description": "copies have different timestamps (likely real re-sends)",
                                   **summary(by_cat[DIFFERENT_TIMESTAMPS])},
        },
        "group_size_distribution": dict(sorted(Counter(g.size for g in groups).items())),
        "similarity": {
            "exact_100": sum(1 for s in scores if s == 100.0),
            "90_to_99.9": sum(1 for s in scores if s < 100.0),
            "minimum": round(min(scores), 1) if scores else None,
        },
        "rules": {
            "groups_using_forwarded_content_rule": sum(1 for g in groups if g.used_rule(FORWARDED)),
            "groups_using_quoted_content_rule": sum(1 for g in groups if g.used_rule(QUOTED)),
            "groups_matched_on_completely_empty_text": sum(
                1 for g in groups if any(m.compared == QUOTED and not m.email.quoted and not g.original.quoted
                                         for m in g.duplicates)),
            "groups_with_sentinel_dates": sum(
                1 for g in groups if g.original.is_sentinel or any(m.email.is_sentinel for m in g.duplicates)),
            "groups_spanning_mailboxes": None,  # filled in by run(), which knows source files
        },
        **info,
        "runtime_seconds": round(runtime, 2),
    }


def _mailboxes_by_id(conn: sqlite3.Connection) -> dict[int, str]:
    return {r[0]: r[1].split("/")[1] for r in conn.execute("SELECT id, source_file FROM emails")}


def run(db_path: str | Path, report_path: str | Path = "duplicates_report.csv",
        stats_path: str | Path | None = "output/duplicate_stats.json",
        threshold: float = THRESHOLD) -> tuple[list[Group], dict]:
    start = time.perf_counter()
    conn = db.connect(db_path)
    try:
        emails = load_candidates(conn)
        groups, info = find_groups(emails, threshold)
        apply_flags(conn, groups)
        rows = write_report(report_path, groups)
        stats = build_stats(groups, info, time.perf_counter() - start)
        mb = _mailboxes_by_id(conn)
        stats["rules"]["groups_spanning_mailboxes"] = sum(
            1 for g in groups if len({mb[g.original.id]} | {mb[m.email.id] for m in g.duplicates}) > 1)
        stats["report_rows"] = rows
        stats["database_flagged"] = conn.execute("SELECT count(*) FROM emails WHERE is_duplicate = 1").fetchone()[0]
    finally:
        conn.close()
    if stats_path:
        Path(stats_path).parent.mkdir(parents=True, exist_ok=True)
        Path(stats_path).write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
    return groups, stats


def report(stats: dict) -> str:
    same, diff = stats["by_category"][SAME_TIMESTAMP], stats["by_category"][DIFFERENT_TIMESTAMPS]
    r = stats["rules"]
    lines = [
        "Duplicate detection statistics",
        "==============================",
        f"Emails scanned:              {stats['emails_scanned']:>8,}",
        f"Candidate buckets:           {stats['candidate_buckets']:>8,}  (same sender + normalized subject, 2+ emails)",
        f"Comparisons:                 {stats['comparisons']:>8,}",
        "",
        f"Total duplicate groups:      {stats['groups']:>8,}",
        f"Total emails flagged:        {stats['emails_flagged']:>8,}",
        f"Average group size:          {stats['average_group_size']:>8}  (including the original)",
        "",
        "By category:                   groups   flagged  avg size",
        f"  same timestamp (folder copies) {same['groups']:>7,} {same['emails_flagged']:>9,} {same['average_group_size']:>9}",
        f"  different timestamps (re-sends){diff['groups']:>7,} {diff['emails_flagged']:>9,} {diff['average_group_size']:>9}",
        "",
        "Group sizes: " + ", ".join(f"{k}: {v:,}" for k, v in stats["group_size_distribution"].items()),
        f"Similarity of flagged emails: {stats['similarity']['exact_100']:,} exact (100%), "
        f"{stats['similarity']['90_to_99.9']:,} at 90-99.9% (lowest {stats['similarity']['minimum']})",
        "",
        "Rules:",
        f"  groups using the empty-body rule (X3):      {r['groups_using_forwarded_content_rule']:>6,}  (compared forwarded_content)",
        f"  groups using the X3 extension:              {r['groups_using_quoted_content_rule']:>6,}  (compared quoted_content)",
        f"    ...matched on completely empty text:      {r['groups_matched_on_completely_empty_text']:>6,}",
        f"  groups with sentinel dates (X4):            {r['groups_with_sentinel_dates']:>6,}",
        f"  groups spanning 2+ mailboxes (X8):          {r['groups_spanning_mailboxes']:>6,}",
        "",
        f"Report rows: {stats['report_rows']:,}   Flagged in database: {stats['database_flagged']:,}   "
        f"Runtime: {stats['runtime_seconds']}s",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Find and flag duplicate emails in the database.")
    ap.add_argument("--db", default="data/enron.db")
    ap.add_argument("--report", default="duplicates_report.csv")
    ap.add_argument("--stats-json", default="output/duplicate_stats.json")
    ap.add_argument("--threshold", type=float, default=THRESHOLD, help="minimum similarity, 0-100 (default 90)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not Path(args.db).is_file():
        log.error("database not found: %s (run python -m enron_pipeline.load first)", args.db)
        return 2
    _, stats = run(args.db, args.report, args.stats_json, args.threshold)
    print(report(stats))
    ok = stats["report_rows"] == stats["emails_flagged"] == stats["database_flagged"]
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
