# Project rules

Enron email extraction, deduplication and MCP notification pipeline (take-home assignment).

## Requirements
- The requirements live in `docs/assignment.md`. Always re-read the relevant section before building a task.
- Mailbox selection and owner notes: `notes/mailbox_selection.md`.

## Environment
- Python 3.10+. Run everything from the project virtual environment: `.venv/bin/python`, `.venv/bin/pip`.
- Record every dependency in `requirements.txt`.

## Error handling
- The pipeline must never crash on malformed emails. Catch the exception, log it with the file path and reason, and skip that email.

## Email sending (safety)
- Never send email to real Enron addresses (or any address taken from the dataset).
- Live sends happen only with the `--send-live` flag. Every live send goes to the user's own Gmail address through a recipient override, which is read from configuration (for example a `NOTIFY_RECIPIENT_OVERRIDE` environment variable) and never hard-coded.
- Live sends are capped by a send limit (small default, e.g. 5). Without an override address configured, live mode must refuse to send.
- Dry-run (the default) only writes `.eml` drafts to `output/replies/`.

## Git
- Never commit `data/`, `docs/`, `.venv/`, or any credentials (`.env`, MCP config with real values, OAuth tokens or client secrets). Only `mcp_config.json.example` with placeholders is committed.

## Code style
- Prefer small, testable modules in `src/`, with tests alongside.
- Explain the plan before making large changes.
