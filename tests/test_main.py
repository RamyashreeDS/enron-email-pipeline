"""main.py: the single end-to-end command."""
import importlib.util
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "enron"

spec = importlib.util.spec_from_file_location("pipeline_main", ROOT / "main.py")
pipeline_main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline_main)


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    """An empty project folder as the current directory (outputs are relative to it)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NOTIFY_RECIPIENT_OVERRIDE", raising=False)
    return tmp_path


def make_maildir(root: Path) -> Path:
    for f in FIXTURES.iterdir():
        dest = root / "maildir" / (f.stem.replace("__", "/") + ".")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(f, dest)
    return root / "maildir"


def test_missing_dataset_gives_clear_instructions(workdir, capsys):
    assert pipeline_main.main([]) == 2
    err = capsys.readouterr().err
    assert "The Enron dataset was not found at: data/maildir" in err
    assert "enron_mail_20150507.tar.gz" in err and "tar -xzf" in err
    assert not (workdir / "data" / "enron.db").exists()  # nothing was started


def test_empty_maildir(workdir, capsys):
    (workdir / "data" / "maildir" / "lay-k").mkdir(parents=True)
    assert pipeline_main.main([]) == 2
    assert "contains no email files" in capsys.readouterr().err


def test_missing_mailbox(workdir, capsys):
    maildir = make_maildir(workdir)
    assert pipeline_main.main(["--maildir", str(maildir), "--mailbox", "nobody-x"]) == 2
    assert "Mailbox(es) not found" in capsys.readouterr().err


def test_send_ceiling_refused_before_any_work(workdir, capsys):
    maildir = make_maildir(workdir)
    assert pipeline_main.main(["--maildir", str(maildir), "--send-live", "--max-sends", "50"]) == 2
    assert "hard ceiling" in capsys.readouterr().err
    assert not (workdir / "data" / "enron.db").exists()


def test_full_run_dry(workdir, capsys):
    maildir = make_maildir(workdir)
    assert pipeline_main.main(["--maildir", str(maildir), "--db", "data/enron.db"]) == 0
    out = capsys.readouterr().out
    for title in ("Tasks 1-2: parse and load", "Task 3: duplicate detection", "Task 4: notifications (dry run)"):
        assert title in out
    assert "No email sent (dry run)" in out
    for f in ("error_log.txt", "duplicates_report.csv", "output/replies.zip", "output/notify_stats.json",
              "output/extraction_stats.json", "output/load_stats.json", "output/duplicate_stats.json"):
        assert (workdir / f).exists(), f
    assert not (workdir / "output" / "send_log.csv").exists()
    # A second run is safe: everything already loaded is skipped, results are the same.
    assert pipeline_main.main(["--maildir", str(maildir)]) == 0
    assert "Inserted 0 | already in database 12" in capsys.readouterr().out


def test_send_live_without_override_refuses(workdir, capsys):
    maildir = make_maildir(workdir)
    assert pipeline_main.main(["--maildir", str(maildir), "--send-live"]) == 2
    assert "NOTIFY_RECIPIENT_OVERRIDE is not set" in capsys.readouterr().err
    assert not (workdir / "data" / "send_log.csv").exists()
