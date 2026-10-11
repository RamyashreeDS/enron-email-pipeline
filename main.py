"""Run the whole Enron pipeline with one command.

    python main.py                                   # Tasks 1-4, notifications as dry run
    python main.py --send-live [--max-sends 5]       # ...and send notices via the Gmail MCP server

Stages:
  1-2. parse every email under --maildir and load it into SQLite (--db)
  3.   find and flag duplicate groups, write duplicates_report.csv
  4.   write notification drafts (output/replies.zip, samples in output/replies/); with
       --send-live, also send up to --max-sends notices to NOTIFY_RECIPIENT_OVERRIDE only

Run from the repository root; outputs are written relative to it. Each stage can also be run
on its own: python -m enron_pipeline.load | .dedupe | .notify (see README).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

if sys.version_info < (3, 10):  # before importing anything that needs 3.10+
    sys.exit(f"This project needs Python 3.10 or newer; this is Python {sys.version.split()[0]} "
             f"({sys.executable}). On macOS, /usr/bin/python3 is 3.9: install a newer Python "
             "(e.g. `brew install python@3.11`) and create the virtual environment with it.")

try:
    from enron_pipeline import db, dedupe, load, notify
except ImportError:  # not installed with `pip install -r requirements.txt`: use the source tree
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
    from enron_pipeline import db, dedupe, load, notify

DATASET_HELP = """\
The Enron dataset was not found at: {path}

  1. Download https://www.cs.cmu.edu/~enron/enron_mail_20150507.tar.gz (~443 MB) into data/
  2. Extract the mailboxes used in this project (from the repository root):
       tar -xzf data/enron_mail_20150507.tar.gz -C data \\
           maildir/lay-k/ maildir/skilling-j/ maildir/kitchen-l/ maildir/farmer-d/ maildir/lokay-m/
  3. Run again:  python main.py   (or point elsewhere with --maildir PATH)"""


def check_dataset(maildir: Path, mailboxes: list[str] | None) -> str | None:
    """Return an error message if the maildir is missing or empty, else None."""
    if not maildir.is_dir():
        return DATASET_HELP.format(path=maildir)
    boxes = [maildir / m for m in mailboxes] if mailboxes else [p for p in maildir.iterdir() if p.is_dir()]
    missing = [b.name for b in boxes if not b.is_dir()]
    if missing:
        return f"Mailbox(es) not found under {maildir}: {', '.join(missing)}"
    if not any(f.is_file() for b in boxes for f in b.rglob("*")):
        return DATASET_HELP.format(path=maildir) + "\n\n(The folder exists but contains no email files.)"
    return None


def stage(title: str) -> float:
    print(f"\n=== {title} " + "=" * max(0, 66 - len(title)))
    return time.perf_counter()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Enron email pipeline: parse, load, dedupe, notify.")
    ap.add_argument("--maildir", default="data/maildir", help="extracted maildir root (default: data/maildir)")
    ap.add_argument("--db", default="data/enron.db", help="SQLite database file (default: data/enron.db)")
    ap.add_argument("--mailbox", action="append", dest="mailboxes",
                    help="only this mailbox (repeatable); default: every mailbox under --maildir")
    ap.add_argument("--send-live", action="store_true",
                    help="really send notices (to NOTIFY_RECIPIENT_OVERRIDE in .env only)")
    ap.add_argument("--max-sends", type=int, default=notify.DEFAULT_MAX_SENDS,
                    help=f"live notices to send (default {notify.DEFAULT_MAX_SENDS}, ceiling {notify.HARD_MAX_SENDS})")
    args = ap.parse_args(argv)

    maildir, db_path = Path(args.maildir), Path(args.db)
    problem = check_dataset(maildir, args.mailboxes)
    if problem:
        print(problem, file=sys.stderr)
        return 2
    if args.send_live:
        try:
            notify.check_max_sends(args.max_sends)  # refuse early, before minutes of parsing
        except notify.LiveModeRefused as exc:
            print(f"Live mode refused: {exc}. Nothing was run.", file=sys.stderr)
            return 2

    started = time.perf_counter()
    try:
        t = stage("Tasks 1-2: parse and load")
        stats, ls = load.load(maildir, db_path, args.mailboxes)
        print(f"Files found {stats.found:,} | parsed {stats.parsed:,} | parse failures {stats.failed:,} "
              f"(see error_log.txt)")
        print(f"Inserted {ls.inserted:,} | already in database {ls.already_in_db:,} | "
              f"duplicate message_ids {ls.duplicate_in_run:,} | database errors {ls.db_errors:,}")
        print(f"Database {db_path}: {ls.database['emails']:,} emails, {ls.database['addresses']:,} addresses, "
              f"{ls.database['recipient_rows']:,} recipient rows | integrity {ls.checks['integrity_check']} "
              f"| {time.perf_counter() - t:.1f}s")

        t = stage("Task 3: duplicate detection")
        _, ds = dedupe.run(db_path)
        same, diff = ds["by_category"][dedupe.SAME_TIMESTAMP], ds["by_category"][dedupe.DIFFERENT_TIMESTAMPS]
        print(f"Groups {ds['groups']:,} | flagged {ds['emails_flagged']:,} | average group size "
              f"{ds['average_group_size']} (see duplicates_report.csv)")
        print(f"  same timestamp (folder copies): {same['groups']:,} groups, {same['emails_flagged']:,} flagged")
        print(f"  different timestamps (re-sends): {diff['groups']:,} groups, {diff['emails_flagged']:,} flagged"
              f" | {time.perf_counter() - t:.1f}s")

        t = stage("Task 4: notifications" + (" (dry run + LIVE)" if args.send_live else " (dry run)"))
        conn = db.connect(db_path)
        try:
            dr = notify.dry_run(conn, "output/replies.zip", "output/replies")
            Path("output/notify_stats.json").write_text(json.dumps(dr, indent=2) + "\n", encoding="utf-8")
            print(f"Drafts {dr['drafts_written']:,} -> {dr['zip_path']} ({dr['zip_bytes'] / 1e6:.1f} MB), "
                  f"{dr['samples_written']} samples in {dr['samples_dir']}/ | draft errors {len(dr['draft_errors'])}")
            live = None
            if args.send_live:
                try:
                    live = notify.live_run(conn, args.max_sends, "data/send_log.csv",
                                           masked_log_path="output/send_log.csv")
                except notify.LiveModeRefused as exc:
                    print(f"Live mode refused: {exc}. Nothing was sent.", file=sys.stderr)
                    return 2
                Path("output/send_stats.json").write_text(json.dumps(live, indent=2) + "\n", encoding="utf-8")
                print(f"Live: selected {live['selected']} | sent {live['sent']} | failed {live['failed']} | "
                      f"unconfirmed {live['unconfirmed']} | skipped (already notified) "
                      f"{live['skipped_already_notified']} (see output/send_log.csv)")
            else:
                print("No email sent (dry run). Add --send-live to send notices to your own address.")
            print(f"{time.perf_counter() - t:.1f}s")
        finally:
            conn.close()
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - report clearly instead of a traceback
        print(f"\nPipeline stopped: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"\nDone in {time.perf_counter() - started:.1f}s.")
    ok = (stats.consistent and ls.handled == stats.parsed and ls.checks["integrity_check"] == "ok"
          and ds["report_rows"] == ds["emails_flagged"] and not dr["draft_errors"]
          and (live is None or (live["failed"] == 0 and live["unconfirmed"] == 0)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
