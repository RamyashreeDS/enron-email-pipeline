"""Task 1 runner: discover, parse, log failures and report statistics.

Usage:
    python -m enron_pipeline.extract --root data/maildir [--mailbox lay-k] [--limit N]
        [--sample N --seed S] [--jsonl PATH]
"""
from __future__ import annotations

import argparse
import itertools
import json
import logging
import random
import sys
import time
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

from .discovery import iter_email_files
from .models import ParsedEmail, ParseError
from .parser import parse_file
from .stats import ExtractionStats

log = logging.getLogger("enron_pipeline.extract")

LOG_HEADER = "# timestamp_utc\tsource_file\treason\tdetail\n"
MAX_DETAIL = 500


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clean(text: str) -> str:
    text = " ".join(str(text).split())  # one line, no tabs
    return text if len(text) <= MAX_DETAIL else text[:MAX_DETAIL] + "..."


class TsvLog:
    """Append-only tab-separated log: timestamp, source_file, reason, detail."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh: TextIO = self.path.open("w", encoding="utf-8")
        self._fh.write(LOG_HEADER)

    def write(self, source_file: str, reason: str, detail: str) -> None:
        self._fh.write(f"{_now()}\t{source_file}\t{reason}\t{_clean(detail)}\n")

    def close(self) -> None:
        self._fh.close()


def iter_parsed(
    files: Iterable[tuple[Path, str]],
    stats: ExtractionStats,
    error_log: TsvLog,
    warning_log: TsvLog | None = None,
    progress_every: int = 5000,
) -> Iterator[ParsedEmail]:
    """Parse each file, yielding successes. Failures are logged and skipped; never raises
    for a bad email."""
    for path, source_file in files:
        stats.record_found(source_file)
        try:
            email = parse_file(path, source_file)
        except ParseError as exc:
            stats.record_failure(source_file, exc.code)
            error_log.write(source_file, exc.code, exc.detail)
        except Exception as exc:  # noqa: BLE001 - the pipeline must not crash on malformed input
            stats.record_failure(source_file, "UNEXPECTED_ERROR")
            error_log.write(source_file, "UNEXPECTED_ERROR", f"{type(exc).__name__}: {exc}")
            log.debug("Unexpected error in %s", source_file, exc_info=True)
        else:
            stats.record_success(email)
            for w in email.warnings:
                stats.record_warning(w.code)
                if warning_log:
                    warning_log.write(source_file, w.code, w.detail)
            yield email
        if progress_every and stats.found % progress_every == 0:
            log.info("... %d files processed (%d parsed, %d failed)", stats.found, stats.parsed, stats.failed)


def select_files(root: Path, mailboxes: list[str] | None, limit: int | None,
                 sample: int | None, seed: int,
                 error_log: TsvLog) -> Iterable[tuple[Path, str]]:
    def onerror(exc: Exception) -> None:
        # Directory-level problem (unreadable folder, missing mailbox): log it, keep going.
        where = getattr(exc, "filename", None) or str(root)
        error_log.write(str(where), "READ_ERROR", f"{type(exc).__name__}: {exc}")
        log.warning("Discovery problem: %s", exc)

    files: Iterable[tuple[Path, str]] = iter_email_files(root, mailboxes, onerror=onerror)
    if sample:
        pool = list(files)
        files = sorted(random.Random(seed).sample(pool, min(sample, len(pool))), key=lambda f: f[1])
    if limit:
        files = itertools.islice(files, limit)
    return files


def run(root: str | Path, mailboxes: list[str] | None = None, limit: int | None = None,
        sample: int | None = None, seed: int = 42,
        error_log_path: str | Path = "error_log.txt",
        warnings_path: str | Path = "output/parse_warnings.txt",
        stats_path: str | Path | None = "output/extraction_stats.json",
        jsonl_path: str | Path | None = None) -> ExtractionStats:
    stats = ExtractionStats()
    start = time.perf_counter()
    error_log, warning_log = TsvLog(error_log_path), TsvLog(warnings_path)
    jsonl = None
    if jsonl_path:
        Path(jsonl_path).parent.mkdir(parents=True, exist_ok=True)
        jsonl = Path(jsonl_path).open("w", encoding="utf-8")
    try:
        files = select_files(Path(root), mailboxes, limit, sample, seed, error_log)
        for email in iter_parsed(files, stats, error_log, warning_log):
            if jsonl:
                d = email.to_dict()
                d["warnings"] = [f"{w['code']}: {w['detail']}" for w in d["warnings"]]
                jsonl.write(json.dumps(d) + "\n")  # ASCII-escaped: no raw U+0085/U+2028 line breaks
    finally:
        error_log.close()
        warning_log.close()
        if jsonl:
            jsonl.close()
    stats.runtime_seconds = time.perf_counter() - start
    if stats_path:
        Path(stats_path).parent.mkdir(parents=True, exist_ok=True)
        Path(stats_path).write_text(stats.to_json() + "\n", encoding="utf-8")
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Extract structured fields from Enron maildir files.")
    ap.add_argument("--root", default="data/maildir", help="maildir root (default: data/maildir)")
    ap.add_argument("--mailbox", action="append", dest="mailboxes",
                    help="only this mailbox (repeatable), e.g. --mailbox lay-k")
    ap.add_argument("--limit", type=int, help="stop after N files (in sorted order)")
    ap.add_argument("--sample", type=int, help="random sample of N files (reproducible with --seed)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--error-log", default="error_log.txt")
    ap.add_argument("--warnings-log", default="output/parse_warnings.txt")
    ap.add_argument("--stats-json", default="output/extraction_stats.json")
    ap.add_argument("--jsonl", help="also write parsed records to this JSONL file")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if not Path(args.root).is_dir():
        log.error("maildir root not found: %s", args.root)
        return 2
    stats = run(args.root, args.mailboxes, args.limit, args.sample, args.seed,
                args.error_log, args.warnings_log, args.stats_json, args.jsonl)
    print(stats.report())
    print(f"\nFailures logged to {args.error_log}; warnings to {args.warnings_log}; "
          f"stats saved to {args.stats_json}")
    return 0 if stats.consistent else 1


if __name__ == "__main__":
    sys.exit(main())
