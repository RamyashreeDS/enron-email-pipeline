"""Summarize X-From names and title-bearing signatures in a mailbox's sent folders.

Usage: python3 -I sent_identity.py <maildir_root> <mailbox> [<mailbox> ...]
"""
import collections
import email
import email.policy
import os
import re
import sys

SENT_FOLDERS = {"sent", "_sent", "_sent_mail", "sent_items"}

# Markers where quoted/forwarded content begins; text after them isn't the sender's own.
QUOTE_MARKERS = re.compile(
    r"^\s*(-{3,}\s*Original Message|-{5,}\s*Forwarded by|-{3,}\s*Forwarded|"
    r"From:\s|To:\s|>|.*\bwrote:\s*$|.*\d{1,2}/\d{1,2}/\d{2,4} \d{1,2}:\d{2}\s*[AP]M)",
    re.IGNORECASE,
)
TITLE_WORDS = re.compile(
    r"\b(Chairman|Chief|CEO|COO|CFO|President|Vice President|VP|Director|"
    r"Managing Director|Manager|Analyst|Specialist|Associate|Counsel|Executive|"
    r"Trader|Officer|Coordinator|Representative|Head of)\b"
)


def own_text(body):
    lines = []
    for line in body.splitlines():
        if QUOTE_MARKERS.match(line):
            break
        lines.append(line.rstrip())
    return lines


def signature(lines, max_lines=8):
    """Return the trailing block (up to max_lines non-empty lines) if it has a title word."""
    tail = [l for l in lines if l.strip()][-max_lines:]
    if not tail:
        return None
    # Look for a short line with a title word; signatures are short lines, not prose.
    for i, l in enumerate(tail):
        s = l.strip()
        if len(s) <= 70 and TITLE_WORDS.search(s) and not s.endswith((".", "?", "!")) or (
            len(s) <= 70 and TITLE_WORDS.search(s) and i >= len(tail) - 4
        ):
            start = max(0, i - 2)
            block = [x.strip() for x in tail[start : i + 3]]
            return " | ".join(block)
    return None


def main():
    root, mailboxes = sys.argv[1], sys.argv[2:]
    for mb in mailboxes:
        xfrom = collections.Counter()
        sigs = collections.Counter()
        n = 0
        base = os.path.join(root, mb)
        folders = sorted(d for d in os.listdir(base) if d in SENT_FOLDERS)
        for folder in folders:
            for dirpath, _, files in os.walk(os.path.join(base, folder)):
                for f in files:
                    with open(os.path.join(dirpath, f), "rb") as fh:
                        msg = email.message_from_binary_file(fh, policy=email.policy.compat32)
                    n += 1
                    xf = (msg.get("X-From") or "").strip()
                    xfrom[xf or "(missing)"] += 1
                    payload = msg.get_payload(decode=True) or b""
                    body = payload.decode("latin-1", errors="replace")
                    sig = signature(own_text(body))
                    if sig:
                        sigs[sig] += 1
        print(f"=== {mb}  sent folders: {', '.join(folders)}  emails: {n}")
        print("  Top X-From:")
        for name, c in xfrom.most_common(6):
            print(f"    {c:6d}  {name}")
        print("  Top title-bearing signature blocks:")
        for s, c in sigs.most_common(8):
            print(f"    {c:6d}  {s[:220]}")
        print()


if __name__ == "__main__":
    main()
