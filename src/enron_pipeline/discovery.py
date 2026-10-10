"""Find email files in a maildir tree."""
from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path


def _natural_key(name: str) -> list:
    """Sort '2.' before '10.' so runs are in a readable, deterministic order."""
    return [int(tok) if tok.isdigit() else tok for tok in re.split(r"(\d+)", name)]


def iter_email_files(
    root: str | Path,
    mailboxes: Iterable[str] | None = None,
    onerror: Callable[[OSError], None] | None = None,
) -> Iterator[tuple[Path, str]]:
    """Yield (absolute_path, source_file) for every email file under `root`.

    `source_file` is relative to root's parent, e.g. 'maildir/lay-k/inbox/45.', matching
    the dataset's own layout. Hidden files and directories are skipped, symlinks are not
    followed, and only regular files are yielded. Directory errors go to `onerror`.
    """
    root = Path(root)
    if mailboxes:
        tops = []
        for m in mailboxes:
            if not m or m in (".", "..") or "/" in m or "\\" in m:
                if onerror:
                    onerror(ValueError(f"Invalid mailbox name: {m!r}"))
                continue
            tops.append(root / m)
    else:
        try:
            tops = sorted((p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")),
                          key=lambda p: _natural_key(p.name))
        except OSError as exc:
            if onerror:
                onerror(exc)
            return
    for top in tops:
        if not top.is_dir():
            if onerror:
                onerror(FileNotFoundError(f"Mailbox directory not found: {top}"))
            continue
        for dirpath, dirnames, filenames in os.walk(top, onerror=onerror, followlinks=False):
            dirnames[:] = sorted((d for d in dirnames if not d.startswith(".")), key=_natural_key)
            for name in sorted(filenames, key=_natural_key):
                if name.startswith("."):
                    continue
                path = Path(dirpath) / name
                if path.is_symlink() or not path.is_file():
                    continue
                yield path, path.relative_to(root.parent).as_posix()
