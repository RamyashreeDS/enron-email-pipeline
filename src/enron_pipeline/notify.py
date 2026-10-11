"""Task 4: duplicate notices: dry-run .eml drafts, and live sends through the Gmail MCP server.

Usage:
    python -m enron_pipeline.notify                     # dry run (default): drafts only
    python -m enron_pipeline.notify --send-live         # send up to 5 notices (ceiling 10)

Dry run writes one draft per duplicate group, addressed to the original sender, into
output/replies.zip, plus a few unzipped samples in output/replies/. Live mode sends only to
NOTIFY_RECIPIENT_OVERRIDE (from the environment or .env), logs every attempt in
data/send_log.csv (git-ignored, full) and writes output/send_log.csv (committed) with the
recipient address masked, and marks notification_sent/notification_date only after a
confirmed send. Decisions N1-N8: notes/notify_decisions.md.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import os
import re
import sqlite3
import sys
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

from . import db, dedupe
from .mcp_client import GmailMcpSender, SendResult, load_server_config

log = logging.getLogger("enron_pipeline.notify")

DEFAULT_MAX_SENDS = 5
HARD_MAX_SENDS = 10  # N7a: refuse anything above this, whatever is typed
SAMPLE_COUNT = 25
SEND_LOG_COLUMNS = ("timestamp", "recipient", "subject", "status", "error",
                    "intended_recipient", "duplicate_message_id", "gmail_message_id")
OVERRIDE_VAR = "NOTIFY_RECIPIENT_OVERRIDE"
_ADDRESS_RE = re.compile(r"^[^@\s<>,;\"']+@[^@\s<>,;\"']+\.[A-Za-z]{2,}$")


# --- groups ---------------------------------------------------------------------------

@dataclass
class NoticeGroup:
    original: dict
    duplicates: list[dict]
    target: dict  # the latest duplicate: the notification target
    similarity: float  # target vs original, recomputed with Task 3's rule
    category: str  # dedupe.SAME_TIMESTAMP or dedupe.DIFFERENT_TIMESTAMPS

    @property
    def any_notified(self) -> bool:
        """N7d: a group counts as notified if ANY of its emails was notified."""
        return bool(self.original["notification_sent"]) or any(d["notification_sent"] for d in self.duplicates)


def _order_key(row: dict) -> tuple:
    # Same ordering as Task 3 (X2, X4): sentinel dates last, then date, then lowest id.
    return (bool(row["date_is_sentinel"]), row["date"], row["id"])


def _candidate(row: dict) -> dedupe.Candidate:
    return dedupe.Candidate(
        row["id"], row["message_id"], row["from_address"], row["subject"], row["date"],
        bool(row["date_is_sentinel"]), dedupe.collapse_whitespace(row["body"]),
        dedupe.collapse_whitespace(row["forwarded_content"]), dedupe.collapse_whitespace(row["quoted_content"]))


_COLUMNS = ("id, message_id, from_address, subject, date, date_is_sentinel, body, forwarded_content, "
            "quoted_content, duplicate_of, notification_sent, notification_date")


def load_groups(conn: sqlite3.Connection) -> list[NoticeGroup]:
    """Rebuild the duplicate groups from the flags Task 3 wrote, ordered by original."""
    dups: dict[str, list[dict]] = {}
    for r in conn.execute(f"SELECT {_COLUMNS} FROM emails WHERE is_duplicate = 1"):
        dups.setdefault(r["duplicate_of"], []).append(dict(r))
    originals = {r["message_id"]: dict(r) for r in conn.execute(
        f"SELECT {_COLUMNS} FROM emails WHERE message_id IN "
        "(SELECT DISTINCT duplicate_of FROM emails WHERE is_duplicate = 1)")}
    groups = []
    for mid, members in dups.items():
        original = originals[mid]
        members.sort(key=_order_key)
        target = members[-1]
        score, _ = dedupe.similarity(_candidate(original), _candidate(target))
        dates = {original["date"]} | {m["date"] for m in members}
        category = dedupe.SAME_TIMESTAMP if len(dates) == 1 else dedupe.DIFFERENT_TIMESTAMPS
        groups.append(NoticeGroup(original, members, target, score, category))
    groups.sort(key=lambda g: _order_key(g.original))
    return groups


def select_live(groups: list[NoticeGroup], n: int) -> tuple[list[NoticeGroup], int]:
    """N7b: the n most recent different-timestamp groups (by the target's date) that have
    not been notified (N7d). Returns (selected, skipped_already_notified)."""
    eligible = [g for g in groups if g.category == dedupe.DIFFERENT_TIMESTAMPS]
    # Newest real date first; a sentinel (1980-01-01, "unknown") target is not "recent", so it goes last.
    eligible.sort(key=lambda g: (g.target["date"], g.target["id"]), reverse=True)
    eligible.sort(key=lambda g: bool(g.target["date_is_sentinel"]))  # stable: keeps the date order
    skipped = sum(1 for g in eligible if g.any_notified)
    return [g for g in eligible if not g.any_notified][:n], skipped


# --- the template (docs/assignment.md 4.2) -----------------------------------------------

def format_sent(date: str) -> str:
    """N4: database UTC date -> '2001-10-25 16:18:00 UTC'."""
    return f"{date} UTC"


def notice_subject(group: NoticeGroup) -> str:
    """N3: literal, '[Duplicate Notice] Re: <original_subject>'."""
    return f"[Duplicate Notice] Re: {group.original['subject']}"


def notice_body(group: NoticeGroup) -> str:
    t, o = group.target, group.original
    return "\n".join([
        "This is an automated notification from the Email Deduplication System.",
        "",
        "Your email has been identified as a potential duplicate:",
        "",
        "Your Email (Flagged):",
        f"Message-ID: {t['message_id']}",
        f"Date Sent: {format_sent(t['date'])}",
        f"Subject: {t['subject']}",
        "",
        "Original Email on Record:",
        f"Message-ID: {o['message_id']}",
        f"Date Sent: {format_sent(o['date'])}",
        f"Similarity Score: {group.similarity:.1f}%",
        "",
        "If this was NOT a duplicate and you intended to send this email,",
        "please reply with CONFIRM to restore it to active status.",
        "No action is required if this is indeed a duplicate.",
        "",
    ])


def live_body(group: NoticeGroup) -> str:
    """N5: live sends start with one line naming the real (Enron) recipient."""
    return f"[Test delivery: intended for {group.target['from_address']}]\n\n" + notice_body(group)


def build_draft(group: NoticeGroup, now: datetime) -> EmailMessage:
    """The dry-run draft, exactly as the template: To, Subject, Date, References (N4)."""
    msg = EmailMessage(policy=policy.SMTP)
    msg["To"] = group.target["from_address"]
    msg["Subject"] = notice_subject(group)
    msg["Date"] = format_datetime(now)
    msg["References"] = group.target["message_id"]
    msg.set_content(notice_body(group))
    return msg


def draft_filename(group: NoticeGroup) -> str:
    """The target's message_id, made filename-safe: '<1.2.JavaMail.evans@thyme>' -> '1.2.JavaMail.evans@thyme.eml'."""
    return re.sub(r"[^A-Za-z0-9._@-]", "_", group.target["message_id"].strip("<>")) + ".eml"


# --- dry run --------------------------------------------------------------------------

def pick_samples(groups: list[NoticeGroup], count: int = SAMPLE_COUNT) -> list[NoticeGroup]:
    """The groups live mode would send first, plus groups spread evenly across the rest."""
    chosen = select_live(groups, DEFAULT_MAX_SENDS)[0]
    ids = {id(g) for g in chosen}
    rest = [g for g in groups if id(g) not in ids]
    need = max(0, count - len(chosen))
    if rest and need:
        step = len(rest) / need
        chosen += [rest[int(i * step)] for i in range(min(need, len(rest)))]
    return chosen


def dry_run(conn: sqlite3.Connection, zip_path: str | Path, samples_dir: str | Path,
            sample_count: int = SAMPLE_COUNT, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc).replace(microsecond=0)
    groups = load_groups(conn)
    zip_path, samples_dir = Path(zip_path), Path(samples_dir)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    samples_dir.mkdir(parents=True, exist_ok=True)
    for old in samples_dir.glob("*.eml"):  # only our drafts; anything else is left alone
        old.unlink()

    errors = []
    written = 0
    tmp = zip_path.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for g in groups:
            try:
                data = build_draft(g, now).as_bytes()
            except Exception as exc:  # noqa: BLE001 - one bad group must not stop the run
                errors.append({"duplicate_message_id": g.target["message_id"], "error": f"{type(exc).__name__}: {exc}"})
                continue
            info = zipfile.ZipInfo(f"replies/{draft_filename(g)}", date_time=now.timetuple()[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
            written += 1
    tmp.replace(zip_path)  # swap in only when complete

    samples = pick_samples(groups, sample_count)
    for g in samples:
        (samples_dir / draft_filename(g)).write_bytes(build_draft(g, now).as_bytes())

    by_cat = {c: sum(1 for g in groups if g.category == c) for c in (dedupe.SAME_TIMESTAMP, dedupe.DIFFERENT_TIMESTAMPS)}
    return {
        "mode": "dry_run",
        "generated_at": db.format_date(now),
        "groups": len(groups),
        "drafts_written": written,
        "draft_errors": errors,
        "groups_by_category": by_cat,
        "zip_path": str(zip_path),
        "zip_bytes": zip_path.stat().st_size,
        "samples_written": len(samples),
        "samples_dir": str(samples_dir),
        "already_notified_groups": sum(1 for g in groups if g.any_notified),
    }


# --- live mode guards -------------------------------------------------------------------

class LiveModeRefused(Exception):
    """Live sending must not happen; the message says why."""


def read_override(env_file: str | Path = ".env") -> str | None:
    """NOTIFY_RECIPIENT_OVERRIDE from the environment, else from the .env file."""
    value = os.environ.get(OVERRIDE_VAR)
    if value is None and Path(env_file).is_file():
        for line in Path(env_file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{OVERRIDE_VAR}="):
                value = line.split("=", 1)[1]
                break
    if value is None:
        return None
    return value.strip().strip("'\"").strip().lower() or None


def check_override(conn: sqlite3.Connection, address: str | None) -> str:
    """CLAUDE.md: live mode refuses without a safe override address."""
    if not address:
        raise LiveModeRefused(f"{OVERRIDE_VAR} is not set (add it to .env); live mode refuses to send")
    if not _ADDRESS_RE.match(address):
        raise LiveModeRefused(f"{OVERRIDE_VAR} is not a valid email address")
    if address.endswith("@enron.com") or address.endswith(".enron.com"):
        raise LiveModeRefused(f"{OVERRIDE_VAR} is an Enron address; live mode never sends to Enron")
    in_data = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM addresses WHERE address = ?) OR "
        "EXISTS (SELECT 1 FROM emails WHERE from_address = ?)", (address, address)).fetchone()[0]
    if in_data:
        raise LiveModeRefused(f"{OVERRIDE_VAR} appears in the Enron dataset; live mode never sends to dataset addresses")
    return address


def check_max_sends(n: int) -> int:
    if n < 1:
        raise LiveModeRefused("--max-sends must be at least 1")
    if n > HARD_MAX_SENDS:
        raise LiveModeRefused(f"--max-sends {n} is above the hard ceiling of {HARD_MAX_SENDS}")
    return n


# --- live mode --------------------------------------------------------------------------

class SendLog:
    """Append-only output/send_log.csv (N7c columns). Each row is flushed immediately."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new = not self.path.exists() or self.path.stat().st_size == 0
        self._fh = self.path.open("a", newline="", encoding="utf-8")
        self._w = csv.writer(self._fh)
        if new:
            self._w.writerow(SEND_LOG_COLUMNS)
            self._fh.flush()

    def write(self, recipient: str, subject: str, result: SendResult, group: NoticeGroup) -> None:
        now = db.format_date(datetime.now(timezone.utc))
        self._w.writerow((now, recipient, subject, result.status, result.error,
                          group.target["from_address"], group.target["message_id"], result.gmail_message_id))
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def close(self) -> None:
        self._fh.close()


def mask_address(address: str) -> str:
    """'ramya@gmail.com' -> 'r***@gmail.com' (the committed log must not reveal the override)."""
    local, at, domain = address.partition("@")
    return f"{local[:1]}***@{domain}" if at and local else "***"


def write_masked_log(full_path: str | Path, masked_path: str | Path) -> int:
    """Copy the full send log with every recipient address masked, wherever it appears.
    Returns the number of rows written."""
    full_path, masked_path = Path(full_path), Path(masked_path)
    with full_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    secrets = sorted({r["recipient"] for r in rows if r.get("recipient")}, key=len, reverse=True)
    masked_path.parent.mkdir(parents=True, exist_ok=True)
    with masked_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=SEND_LOG_COLUMNS)
        w.writeheader()
        for r in rows:
            out = {}
            for k in SEND_LOG_COLUMNS:
                v = r.get(k) or ""
                for secret in secrets:
                    v = re.sub(re.escape(secret), mask_address(secret), v, flags=re.I)
                out[k] = v
            w.writerow(out)
    return len(rows)


def mark_notified(conn: sqlite3.Connection, group: NoticeGroup) -> str:
    when = db.format_date(datetime.now(timezone.utc))
    conn.execute("UPDATE emails SET notification_sent = 1, notification_date = ? WHERE id = ?",
                 (when, group.target["id"]))  # autocommit connection: saved immediately
    return when


async def _send_all(conn, selected, override, sender_factory, send_log) -> dict:
    out = {"sent": 0, "failed": 0, "unconfirmed": 0, "results": []}

    def record(g, subject, result):
        send_log.write(override, subject, result, g)
        out[result.status] += 1
        entry = {"duplicate_message_id": g.target["message_id"], "status": result.status,
                 "gmail_message_id": result.gmail_message_id, "error": result.error}
        if result.ok:
            entry["notification_date"] = mark_notified(conn, g)
        out["results"].append(entry)

    try:
        async with sender_factory() as sender:
            for g in selected:
                subject = notice_subject(g)
                result = await sender.send([override], subject, live_body(g), in_reply_to=g.target["message_id"])
                record(g, subject, result)
                if result.status == "unconfirmed":
                    log.error("Send result unclear; stopping so it can be checked by hand: %s", result.error)
                    break
    except Exception as exc:  # noqa: BLE001 - e.g. the server could not start; log, don't crash
        done = {r["duplicate_message_id"] for r in out["results"]}
        for g in selected:
            if g.target["message_id"] not in done:
                record(g, notice_subject(g), SendResult("failed", error=f"MCP server error: {type(exc).__name__}: {exc}"))
    return out


def live_run(conn: sqlite3.Connection, max_sends: int, send_log_path: str | Path, env_file: str | Path = ".env",
             sender_factory=None, mcp_config: str | Path | None = "mcp_config.json",
             server_log: str | Path = "data/mcp_server.log",
             masked_log_path: str | Path | None = None) -> dict:
    """Send up to max_sends notices. Raises LiveModeRefused before anything is sent if a guard fails."""
    check_max_sends(max_sends)
    override = check_override(conn, read_override(env_file))
    groups = load_groups(conn)
    selected, skipped = select_live(groups, max_sends)
    result = {"mode": "live", "max_sends": max_sends, "selected": len(selected),
              "skipped_already_notified": skipped}
    if not selected:
        return {**result, "sent": 0, "failed": 0, "unconfirmed": 0, "results": []}

    errlog = None
    if sender_factory is None:
        config = load_server_config(mcp_config)
        Path(server_log).parent.mkdir(parents=True, exist_ok=True)
        errlog = open(server_log, "a", encoding="utf-8")  # the server's own diagnostics (git-ignored)
        sender_factory = lambda: GmailMcpSender(config, errlog=errlog)  # noqa: E731
    send_log = SendLog(send_log_path)
    try:
        out = asyncio.run(_send_all(conn, selected, override, sender_factory, send_log))
    finally:
        send_log.close()
        if errlog:
            errlog.close()
        if masked_log_path:
            write_masked_log(send_log_path, masked_log_path)
    return {**result, **out}


# --- CLI --------------------------------------------------------------------------------

def report(stats: dict) -> str:
    if stats["mode"] == "dry_run":
        c = stats["groups_by_category"]
        return "\n".join([
            "Notification dry run (nothing sent)",
            "===================================",
            f"Duplicate groups:          {stats['groups']:>7,}  (same timestamp {c[dedupe.SAME_TIMESTAMP]:,}, "
            f"different timestamps {c[dedupe.DIFFERENT_TIMESTAMPS]:,})",
            f"Drafts written:            {stats['drafts_written']:>7,}  -> {stats['zip_path']} "
            f"({stats['zip_bytes'] / 1e6:.1f} MB)",
            f"Draft errors:              {len(stats['draft_errors']):>7,}",
            f"Samples unzipped:          {stats['samples_written']:>7,}  -> {stats['samples_dir']}/",
            f"Groups already notified:   {stats['already_notified_groups']:>7,}",
        ])
    lines = [
        "Notification live run",
        "=====================",
        f"Max sends: {stats['max_sends']}   Selected: {stats['selected']}   "
        f"Skipped (already notified): {stats['skipped_already_notified']}",
        f"Sent: {stats['sent']}   Failed: {stats['failed']}   Unconfirmed: {stats['unconfirmed']}",
    ]
    for r in stats["results"]:
        lines.append(f"  {r['status']:<12} {r['duplicate_message_id']}  "
                     f"{('gmail id ' + r['gmail_message_id']) if r['gmail_message_id'] else r['error']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Duplicate notices: dry-run drafts (default) or live sends via MCP.")
    ap.add_argument("--db", default="data/enron.db")
    ap.add_argument("--send-live", action="store_true", help="really send (to NOTIFY_RECIPIENT_OVERRIDE only)")
    ap.add_argument("--max-sends", type=int, default=DEFAULT_MAX_SENDS,
                    help=f"notices to send in live mode (default {DEFAULT_MAX_SENDS}, ceiling {HARD_MAX_SENDS})")
    ap.add_argument("--zip", default="output/replies.zip", help="dry run: archive with every draft")
    ap.add_argument("--replies-dir", default="output/replies", help="dry run: unzipped sample drafts")
    ap.add_argument("--samples", type=int, default=SAMPLE_COUNT)
    ap.add_argument("--send-log", default="data/send_log.csv",
                    help="full live-send log, with your address (git-ignored location)")
    ap.add_argument("--send-log-public", default="output/send_log.csv",
                    help="committed copy of the send log, recipient address masked")
    ap.add_argument("--env-file", default=".env")
    ap.add_argument("--mcp-config", default="mcp_config.json",
                    help="mcpServers config (same shape as mcp_config.json.example); default server if missing")
    ap.add_argument("--stats-json", default=None,
                    help="default: output/notify_stats.json (dry run) or output/send_stats.json (live)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if not Path(args.db).is_file():
        log.error("database not found: %s", args.db)
        return 2
    conn = db.connect(args.db)
    try:
        if args.send_live:
            try:
                stats = live_run(conn, args.max_sends, args.send_log, args.env_file, mcp_config=args.mcp_config,
                                 masked_log_path=args.send_log_public)
            except LiveModeRefused as exc:
                print(f"Live mode refused: {exc}. Nothing was sent.", file=sys.stderr)
                return 2
        else:
            stats = dry_run(conn, args.zip, args.replies_dir, args.samples)
    finally:
        conn.close()
    print(report(stats))
    stats_path = Path(args.stats_json or ("output/send_stats.json" if args.send_live else "output/notify_stats.json"))
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    stats_path.write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
    if stats["mode"] == "live":
        return 0 if stats["failed"] == 0 and stats["unconfirmed"] == 0 else 1
    return 0 if not stats["draft_errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
