"""Task 2 runner: parse the maildir and load the records into SQLite.

Usage:
    python -m enron_pipeline.load --root data/maildir --db data/enron.db [--mailbox lay-k]
        [--limit N] [--sample N --seed S]

Re-running is safe: emails whose message_id is already in the database are skipped and
reported as a single count (decision T3 in notes/db_decisions.md).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import db
from .extract import TsvLog, iter_parsed, select_files
from .stats import ExtractionStats

log = logging.getLogger("enron_pipeline.load")


@dataclass
class LoadStats:
    inserted: int = 0
    already_in_db: int = 0  # message_id loaded by an earlier run: counted only, not logged
    duplicate_in_run: int = 0  # two files in this run share a message_id: logged
    db_errors: int = 0
    addresses_created: int = 0
    recipient_rows: Counter = field(default_factory=Counter)
    runtime_seconds: float = 0.0
    database: dict = field(default_factory=dict)
    checks: dict = field(default_factory=dict)

    @property
    def handled(self) -> int:
        return self.inserted + self.already_in_db + self.duplicate_in_run + self.db_errors

    def to_dict(self) -> dict:
        return {
            "inserted": self.inserted,
            "skipped_already_in_db": self.already_in_db,
            "skipped_duplicate_message_id_in_run": self.duplicate_in_run,
            "db_errors": self.db_errors,
            "addresses_created": self.addresses_created,
            "recipient_rows_created": dict(self.recipient_rows),
            "runtime_seconds": round(self.runtime_seconds, 2),
            "database_totals": self.database,
            "database_checks": self.checks,
        }

    def report(self, parsed: int) -> str:
        r = self.recipient_rows
        consistent = "yes" if self.handled == parsed else f"NO ({self.handled} handled vs {parsed} parsed)"
        lines = [
            "Load statistics",
            "===============",
            f"Records from parser:          {parsed:>9,}",
            f"Inserted:                     {self.inserted:>9,}",
            f"Skipped, already in database: {self.already_in_db:>9,}",
            f"Skipped, duplicate in run:    {self.duplicate_in_run:>9,}",
            f"Database errors:              {self.db_errors:>9,}",
            f"All records accounted for:    {consistent}",
            "",
            f"Addresses created:            {self.addresses_created:>9,}",
            f"Recipient rows created:       {sum(r.values()):>9,}  "
            f"(to {r['to']:,} / cc {r['cc']:,} / bcc {r['bcc']:,})",
            f"Runtime:                      {self.runtime_seconds:>8.2f}s",
            "",
            "Database totals now:",
        ]
        lines += [f"  {k:<26}{v:>9,}" for k, v in self.database.items()]
        lines += ["", "Database checks:"]
        lines += [f"  {k:<26}{v!s:>9}" for k, v in self.checks.items()]
        return "\n".join(lines)


def load(root: str | Path, db_path: str | Path = "data/enron.db", mailboxes: list[str] | None = None,
         limit: int | None = None, sample: int | None = None, seed: int = 42,
         error_log_path: str | Path = "error_log.txt",
         warnings_path: str | Path = "output/parse_warnings.txt",
         stats_path: str | Path | None = "output/extraction_stats.json",
         load_stats_path: str | Path | None = "output/load_stats.json",
         batch_size: int = 1000) -> tuple[ExtractionStats, LoadStats]:
    start = time.perf_counter()
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = db.connect(db_path)
    db.create_schema(conn)
    cache = db.AddressCache(conn)
    stats, ls = ExtractionStats(), LoadStats()
    error_log, warning_log = TsvLog(error_log_path), TsvLog(warnings_path)
    seen_this_run: dict[str, str] = {}  # message_id -> source_file that carried it first
    pending = 0
    try:
        conn.execute("BEGIN")
        files = select_files(Path(root), mailboxes, limit, sample, seed, error_log)
        for email in iter_parsed(files, stats, error_log, warning_log):
            first = seen_this_run.get(email.message_id)
            if first is not None:
                ls.duplicate_in_run += 1
                error_log.write(email.source_file, "DUPLICATE_MESSAGE_ID",
                                f"{email.message_id} already came from {first} in this run; kept that copy")
                continue
            seen_this_run[email.message_id] = email.source_file
            try:
                inserted = db.insert_email(conn, email, cache)
            except Exception as exc:  # noqa: BLE001 - one bad record must not stop the load
                ls.db_errors += 1
                error_log.write(email.source_file, "DB_ERROR", f"{type(exc).__name__}: {exc}")
                continue
            if not inserted:
                ls.already_in_db += 1
                continue
            ls.inserted += 1
            for kind, attr in db.RECIPIENT_FIELDS:
                ls.recipient_rows[kind] += len(getattr(email, attr))
            pending += 1
            if pending >= batch_size:
                conn.execute("COMMIT")
                conn.execute("BEGIN")
                pending = 0
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")  # only the current, uncommitted batch is lost
        raise
    finally:
        error_log.close()
        warning_log.close()
        ls.addresses_created = cache.created
        ls.database = db.counts(conn)
        ls.checks = db.check_database(conn)
        conn.close()

    ls.runtime_seconds = stats.runtime_seconds = time.perf_counter() - start
    for path, payload in ((stats_path, stats.to_json()), (load_stats_path, json.dumps(ls.to_dict(), indent=2))):
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(payload + "\n", encoding="utf-8")
    return stats, ls


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Parse Enron maildir files and load them into SQLite.")
    ap.add_argument("--root", default="data/maildir", help="maildir root (default: data/maildir)")
    ap.add_argument("--db", default="data/enron.db", help="SQLite database file (default: data/enron.db)")
    ap.add_argument("--mailbox", action="append", dest="mailboxes", help="only this mailbox (repeatable)")
    ap.add_argument("--limit", type=int, help="stop after N files (in sorted order)")
    ap.add_argument("--sample", type=int, help="random sample of N files (reproducible with --seed)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--error-log", default="error_log.txt")
    ap.add_argument("--warnings-log", default="output/parse_warnings.txt")
    ap.add_argument("--stats-json", default="output/extraction_stats.json")
    ap.add_argument("--load-stats-json", default="output/load_stats.json")
    ap.add_argument("--batch-size", type=int, default=1000, help="emails per transaction")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not Path(args.root).is_dir():
        log.error("maildir root not found: %s", args.root)
        return 2
    stats, ls = load(args.root, args.db, args.mailboxes, args.limit, args.sample, args.seed,
                     args.error_log, args.warnings_log, args.stats_json, args.load_stats_json,
                     args.batch_size)
    print(f"Parsed {stats.parsed:,} of {stats.found:,} files ({stats.failed:,} parse failures; "
          f"details in {args.error_log}).\n")
    print(ls.report(stats.parsed))
    ok = (stats.consistent and ls.handled == stats.parsed
          and ls.checks.get("integrity_check") == "ok" and ls.checks.get("foreign_key_violations") == 0)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
