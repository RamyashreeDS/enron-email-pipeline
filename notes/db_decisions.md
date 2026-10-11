# Database decisions (Task 2)

Decisions for the SQLite database (`docs/assignment.md` Task 2, items 6–10), with the
reason for each. Same guiding rule as `notes/parse_decisions.md`: follow the spec
literally and document the consequences.

## Design summary

Three tables:

| Table | One row per | Rows |
|---|---|--:|
| `emails` | parsed email | 32,805 |
| `addresses` | distinct recipient address | 14,815 |
| `email_recipients` | (email, address, to/cc/bcc), i.e. the linking table | 328,828 |

- `addresses` holds recipients only, because senders are a text column (T1). Of the
  17,142 distinct addresses in the data, 2,327 only ever appear as senders, so they are
  in `emails.from_address` but not in `addresses`.
- The schema lives in `schema.sql` (repo root); the loader runs that same file, so the
  file and the database can't drift apart.
- The database file is `data/enron.db`. It is git-ignored and rebuilt with one command.
- Indexes: `emails(date)`, `emails(from_address)`, `emails(subject)` (spec item 9), plus
  `email_recipients(address_id)` for "emails received by X". UNIQUE columns get their own
  index automatically.

## Decisions

| # | Decision | Reason |
|---|---|---|
| T1 | **The sender is a text column `emails.from_address`.** To/Cc/Bcc recipients are normalized into `addresses` + `email_recipients`. | Matches the spec's field name and its "index on from_address" requirement word for word, and sender queries need no join. The spec's normalization example is specifically "to/cc/bcc". Trade-off: "everything involving X, sent or received" needs a UNION of the sender column and the linking table. |
| T2 | **`emails.id` is an integer primary key; `message_id` is `UNIQUE`.** | Recipient rows store a small integer instead of a ~45-character string (~15 MB smaller, faster joins). The spec's requirement, a unique constraint on message_id, is met by `UNIQUE`. |
| T3 | **If a message_id is already in the database, the new row is skipped and the existing row is kept unchanged.** | Re-running the pipeline is safe and never touches Task 3/4 state (`is_duplicate`, `notification_sent`, `notification_date`), and it follows "prevent exact duplicates on insert" (item 8) literally. See "Second runs" below for how skips are reported. Trade-off: parser improvements don't reach rows already loaded; delete `data/enron.db` to rebuild. |
| T4 | **Dates are ISO-8601 text in UTC, `YYYY-MM-DD HH:MM:SS`** (e.g. `2000-12-04 17:58:00`). | Readable, sorts correctly as text, and is SQLite's native date format, so `date()`, `strftime()` and range comparisons work directly. The UTC offset is implied; a CHECK constraint enforces the exact format so comparisons stay correct. |
| T5 | **`duplicate_of` stores the original's `message_id`** (text, a foreign key to `emails.message_id`). | The spec says it "references the original (earliest) message_id"; the duplicates report and the notification template use message_ids too. |
| T6 | **`headings` is a TEXT column, one heading per line.** | The spec lists headings as TEXT. Trade-off: heading searches use `LIKE`, not exact lookups. |
| T7 | **Only one column beyond the spec: `date_is_sentinel`.** No `mailbox` column, no diagnostic columns, no `parse_failures` table. | Task 3 needs it so that a copy dated 1980-01-01 (the Lotus Notes null date, D3) is never chosen as the original. Everything else stays as the spec lists it; parse failures stay in `error_log.txt`, as required. |
| T8 | **Loading is a separate command:** `python -m enron_pipeline.load`. It parses and loads in one pass. A root `main.py` that chains all tasks comes later. | Each task can be run, tested and explained on its own. |

## Second runs and duplicate message_ids (T3 in detail)

The loader inserts with `INSERT ... ON CONFLICT(message_id) DO NOTHING`. Only a message_id
conflict is skipped; any other rule violation (a CHECK or NOT NULL failure) is an error
for that email, logged as `DB_ERROR`, and the email is skipped. (`INSERT OR IGNORE` was
not used because it would silently swallow those violations too.)

A skipped message_id is reported in one of two ways:

| Situation | How it's reported |
|---|---|
| The message_id was **already in the database before this run** (normal on a second run) | **A single count** in the load stats (`already_in_db`). Nothing is written to `error_log.txt`. |
| Two files **in the same run** share a message_id (a real data problem) | One line per file in `error_log.txt` with reason `DUPLICATE_MESSAGE_ID`, naming the file whose copy was kept |

Parse failures are still logged on every run, since they are failures of that run.

## Other rules the database enforces (CHECK constraints)

- Booleans (`has_attachment`, `date_is_sentinel`, `is_duplicate`, `notification_sent`) are 0 or 1.
- `is_duplicate = 1` if and only if `duplicate_of` is set; an email can't be a duplicate of itself.
- `notification_sent = 1` requires a `notification_date`.
- `date` and `notification_date` must match `YYYY-MM-DD HH:MM:SS`.
- `recipient_type` is `to`, `cc` or `bcc`; `position` keeps each list's original order.
- The same address can't appear twice in one field of one email, but it can be in both To and Cc (207 emails).
- Deleting an email deletes its recipient rows (`ON DELETE CASCADE`). Foreign keys are switched on for every connection.
