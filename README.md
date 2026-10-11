# Enron Email Pipeline

Parses raw Enron emails into structured records, stores them in SQLite, finds duplicate
emails, and writes (or, on request, sends) a notice for each duplicate group through a
Gmail MCP server. Built for a take-home assignment, with Claude Code as the AI coding
assistant (see [`AI_USAGE.md`](AI_USAGE.md)).

```
raw maildir files ──► Task 1: parse ──► Task 2: SQLite ──► Task 3: find duplicates ──► Task 4: notices
  34,222 files         32,805 records     data/enron.db      7,356 groups                7,356 .eml drafts
                       1,417 failures                        14,896 emails flagged       5 sent live (Gmail MCP)
```

| Result | Number |
|---|--:|
| Email files scanned (5 mailboxes) | 34,222 |
| Parsed into records | 32,805 (95.86%) |
| Parse failures, logged in `error_log.txt` | 1,417 (1,415 with no `To` header, 2 with no valid sender) |
| Distinct recipient addresses / recipient rows | 14,815 / 328,828 |
| Duplicate groups / emails flagged as duplicates | 7,356 / 14,896 |
| …of which are the same email filed in several folders | 6,852 groups (93.1%) / 13,371 emails (89.8%) |
| Notification drafts (dry run) | 7,356, one per group |
| Notices sent live to the author's own inbox | 5 |
| Full run time | about 20 seconds |
| Tests | 276 |

---

## Contents
1. [Repository layout](#1-repository-layout)
2. [Setup on a fresh machine](#2-setup-on-a-fresh-machine)
3. [How to run](#3-how-to-run)
4. [Gmail MCP setup (only for live sending)](#4-gmail-mcp-setup-only-for-live-sending)
5. [Architecture](#5-architecture)
6. [Mailbox selection](#6-mailbox-selection)
7. [Key decisions and trade-offs](#7-key-decisions-and-trade-offs)
8. [What I'd do with more time](#8-what-id-do-with-more-time)
9. [Known limits](#9-known-limits)
10. [More detail](#10-more-detail)

---

## 1. Repository layout

| Path | What it is |
|---|---|
| `main.py` | One command for the whole pipeline (Tasks 1–4) |
| `src/enron_pipeline/` | The pipeline code, as small modules (see [Architecture](#5-architecture)) |
| `tests/` | 276 pytest tests, with 15 real Enron emails as fixtures and a fake Gmail MCP server |
| `schema.sql` | The database schema (the loader runs this file) |
| `sample_queries.sql` | 6 sample queries, each with its expected output |
| `duplicates_report.csv` | Every flagged duplicate with its original and similarity score |
| `error_log.txt` | Every parse failure: file path and reason |
| `output/replies.zip` | All 7,356 notification drafts (`.eml`) |
| `output/replies/` | 25 of those drafts, unzipped, for quick reading |
| `output/send_log.csv` | The live sends (your address masked; see [§4](#4-gmail-mcp-setup-only-for-live-sending)) |
| `output/*.json`, `output/parse_warnings.txt` | Statistics from each stage, and non-fatal parse warnings |
| `mcp_config.json.example`, `.env.example` | Templates for the Gmail MCP server and your recipient override (placeholders only) |
| `notes/` | Every decision with its reason and its effect on the data |
| `CLAUDE.md` | The project rules given to the AI assistant (Claude Code): where the requirements are, never crash on bad emails, never email dataset addresses, live sends only to my own address with a cap, never commit data or credentials |
| `exploration/` | A one-off script used to confirm mailbox owners and roles |
| `data/` | **Not in git.** The dataset, the database and private logs live here |

---

## 2. Setup on a fresh machine

These steps were tested on a fresh clone with a new virtual environment. Each warning
below comes from a problem hit during that test.

### What you need
- **Python 3.10 or newer.** Tested on 3.11 and 3.13.
  > ⚠️ **macOS:** the built-in `/usr/bin/python3` is **3.9**, which is too old. With it,
  > `pip` fails with a misleading *"setup.py not found … editable mode"* error rather than
  > a version message. Install a newer Python (e.g. `brew install python@3.11`) and check
  > with `python3 --version` before creating the virtual environment.
- **Internet access** to install packages from PyPI and download the dataset.
- About **750 MB** of disk: the archive (443 MB), the five extracted mailboxes (165 MB) and
  the database (about 110 MB).
- **Node.js** only if you want to send notices live (§4). Nothing else needs it,
  including the tests.

### Steps
```bash
# 1. Get the code and move into it. Run every command below from this folder.
git clone https://github.com/RamyashreeDS/enron-email-pipeline.git enron-pipeline
cd enron-pipeline

# 2. Create a virtual environment with Python 3.10+ and install the requirements
python3.11 -m venv .venv            # any python3.10+ works
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```
> ⚠️ **Run `pip install -r requirements.txt` from the repository root.** The file
> contains `-e .`, which installs the project from the *current folder*. From any other
> folder, pip fails with the same misleading "setup.py not found" error.

```bash
# 3. Download the dataset (it is not in the repository; data/ is git-ignored)
mkdir -p data
curl -L -o data/enron_mail_20150507.tar.gz https://www.cs.cmu.edu/~enron/enron_mail_20150507.tar.gz

# 4. Extract only the five mailboxes this project uses (there's no need to unpack all 150)
tar -xzf data/enron_mail_20150507.tar.gz -C data \
    maildir/lay-k/ maildir/skilling-j/ maildir/kitchen-l/ maildir/farmer-d/ maildir/lokay-m/
```
This creates `data/maildir/<mailbox>/…`, the default location the pipeline reads from. If
your data is somewhere else, pass `--maildir /path/to/maildir`.

If you run the pipeline before the dataset is in place, it stops straight away and prints
these download and extract instructions.

---

## 3. How to run

> ⚠️ **Run from the repository root.** All outputs are written relative to the current folder.

### The whole pipeline (dry run: no email is sent)
```bash
.venv/bin/python main.py
```
This parses and loads the emails, finds the duplicates and writes the notification
drafts, printing a short summary after each stage. Options:

| Option | Default | Meaning |
|---|---|---|
| `--maildir PATH` | `data/maildir` | where the extracted mailboxes are |
| `--db PATH` | `data/enron.db` | the SQLite database file (created if missing) |
| `--mailbox NAME` | all | only this mailbox (repeatable) |
| `--send-live` | off | also **send** notices, only to your own address (§4) |
| `--max-sends N` | 5 | how many notices to send live (hard ceiling 10) |

### Live mode (sends real email to your own address only)
```bash
.venv/bin/python main.py --send-live              # sends 5 notices
.venv/bin/python main.py --send-live --max-sends 3
```
This needs the one-time setup in §4. Without `NOTIFY_RECIPIENT_OVERRIDE` in `.env`, live
mode refuses to send anything.

### One stage at a time
```bash
.venv/bin/python -m enron_pipeline.extract --mailbox lay-k --sample 500   # Task 1 only: parse, no database
.venv/bin/python -m enron_pipeline.load                                   # Tasks 1-2: parse and load
.venv/bin/python -m enron_pipeline.dedupe                                 # Task 3
.venv/bin/python -m enron_pipeline.notify                                 # Task 4, dry run
.venv/bin/python -m enron_pipeline.notify --send-live --max-sends 5       # Task 4, live
```
Every command takes `--help`.

### Tests
```bash
.venv/bin/python -m pytest -q
```
There are 276 tests, which run in a few seconds. They don't need the dataset, Node.js or
a network connection: live sending is tested against a fake Gmail MCP server
(`tests/fake_gmail_mcp_server.py`) over the real MCP protocol.

### Sample queries
```bash
sqlite3 -header -column data/enron.db < sample_queries.sql
```

### Running it again: what to expect
- **A second run is safe.** Emails whose `message_id` is already in the database are
  skipped and reported as one count, so `output/load_stats.json` will show `inserted: 0`.
  To rebuild from scratch, delete `data/enron.db` first.
- **Duplicate flags are recomputed** on every run. The notification history is kept.
- **`git status` will show changed files after a run.** The pipeline regenerates the
  committed outputs (`error_log.txt`, `output/*.json`, `output/replies.zip` and the 25
  samples). Only timestamps and run times change: `duplicates_report.csv` comes out
  byte-identical, and the drafts differ only in their `Date:` header.
- **Notification state belongs to one database.** A fresh clone builds its own
  `data/enron.db` with nothing marked as notified, so `--send-live` there would send
  notices already sent from another machine.

---

## 4. Gmail MCP setup (only for live sending)

Live mode sends through **[@artymclabin/gmail-mcp](https://github.com/ArtyMcLabin/Gmail-MCP-Server)
version 1.2.3** (MIT licence). It's a maintained fork of the best-known Gmail MCP server,
and it has the `send_email` tool the assignment asks for. It signs in with the
**send-only** Gmail permission, so the token can't read or change your mailbox.

**Safety rules, enforced in code:**
- Every live notice goes **only** to the address in `NOTIFY_RECIPIENT_OVERRIDE`. Live mode
  refuses if that address is missing, malformed, an `@enron.com` address, or any address
  that appears in the dataset.
- At most 10 notices can be sent per run (5 by default).
- Dry run is the default.

### One-time setup
1. **Google Cloud project.** In the [Google Cloud Console](https://console.cloud.google.com/):
   - create a project and enable the **Gmail API**
   - configure the **OAuth consent screen** (Google Auth Platform) with user type
     External, publishing status **Testing**, and **your Gmail added as a test user**
2. **Allow the send scope.** On the consent screen's **Data access** page, choose *Add or
   remove scopes*, tick **`.../auth/gmail.send`** only, then *Update* and *Save*.
3. **Create the OAuth client.** Go to **Credentials → Create credentials → OAuth client
   ID**, choose application type **Desktop app**, then **Download JSON**.
4. **Store the client file outside the repository:**
   ```bash
   mkdir -p ~/.gmail-mcp && chmod 700 ~/.gmail-mcp
   mv ~/Downloads/client_secret_*.json ~/.gmail-mcp/gcp-oauth.keys.json
   chmod 600 ~/.gmail-mcp/gcp-oauth.keys.json
   ```
5. **Install Node.js** (it runs the MCP server), e.g. `brew install node`, then check with `node --version`.
6. **Sign in once, with send-only access.** Run this in a normal terminal, since it waits
   for the browser:
   ```bash
   npx -y @artymclabin/gmail-mcp@1.2.3 auth --scopes=gmail.send
   ```
   Pick your Gmail in the browser. On "Google hasn't verified this app", choose
   **Continue** (allowed because you're a test user), then allow **"Send email on your
   behalf"**. The token is saved to `~/.gmail-mcp/credentials.json`.
7. **Set your recipient override** in a git-ignored `.env` file in the repository root:
   ```bash
   cp .env.example .env      # then edit it: NOTIFY_RECIPIENT_OVERRIDE=your.address@gmail.com
   chmod 600 .env
   ```
8. **Optional: use the same server from Claude Code.** Register it privately (local
   scope, stored in your Claude Code config rather than the repo). **Put the name `gmail`
   before the `-e` options**, because `-e` takes several values and would otherwise
   swallow the name:
   ```bash
   claude mcp add gmail --scope local \
     -e GMAIL_OAUTH_PATH=$HOME/.gmail-mcp/gcp-oauth.keys.json \
     -e GMAIL_CREDENTIALS_PATH=$HOME/.gmail-mcp/credentials.json \
     -- npx -y @artymclabin/gmail-mcp@1.2.3
   claude mcp get gmail      # should show: Status ✔ Connected
   ```
   Restart Claude Code, and `/mcp` lists the `send_email` tool.

The pipeline starts the same server by itself. It reads `mcp_config.json` if you create
one (same format as `mcp_config.json.example`, git-ignored); otherwise it uses those same
pinned defaults.

### Where secrets live (none of them in git)

| Item | Location |
|---|---|
| OAuth client (`gcp-oauth.keys.json`) | `~/.gmail-mcp/`, outside the repo |
| Gmail token (`credentials.json`) | `~/.gmail-mcp/`, outside the repo |
| Your recipient address | `.env` in the repo root, git-ignored |
| Full send log, with your address | `data/send_log.csv`, git-ignored |
| Committed send log | `output/send_log.csv`, with the address masked (`r***@gmail.com`) |

**Note:** in Testing mode, Google expires the token after about **7 days**. If live
sending later fails with an authentication error, repeat step 6.

---

## 5. Architecture

```
data/maildir/**  ─►  discovery ─► rawfile ─► headers / dates / addresses ─► body ─► segments ─► attachments / headings
 (raw files)          find files   split &     header fields                  decode   own text vs    optional fields
                                   repair                                              forwarded/quoted
                                                     │
                                                     ▼  ParsedEmail records (models)        failures ─► error_log.txt
                                                   parser ──────────────────────────────────────────────────┐
                                                     │                                                       │
                     Task 2   load ─► db ─► data/enron.db  (emails · addresses · email_recipients)  ◄─ schema.sql
                                                     │
                     Task 3   dedupe ─► is_duplicate / duplicate_of flags  +  duplicates_report.csv
                                                     │
                     Task 4   notify ─► output/replies.zip (+ samples)          [dry run, default]
                                     └► mcp_client ─► Gmail MCP server ─► Gmail  [--send-live]
                                                     ├► data/send_log.csv  +  output/send_log.csv (masked)
                                                     └► notification_sent / notification_date
```

| Module (`src/enron_pipeline/`) | Job |
|---|---|
| `discovery.py` | Walks the maildir in a fixed order; skips hidden files and symlinks |
| `rawfile.py` | Decodes bytes and splits headers from body; repairs header values broken by a stray line break |
| `headers.py` | Reads header values; decodes RFC 2047 encoded text (`=?…?=`) in subjects |
| `dates.py` | Parses dates to UTC and detects the Lotus Notes "unknown date" (1980-01-01) |
| `addresses.py` | Turns From/To/Cc/Bcc into clean addresses, one list item at a time |
| `body.py` | Decodes quoted-printable/base64 and strips SMTP headers that leaked into the body |
| `segments.py` | Splits the body into the sender's own text, forwarded content and quoted replies |
| `attachments.py`, `headings.py` | The `has_attachment` and `headings` fields |
| `parser.py`, `models.py` | Puts one email together into a `ParsedEmail`, or raises a `ParseError` with a reason code |
| `extract.py`, `stats.py` | Task 1 runner: never crashes on a bad email, logs failures, collects statistics |
| `db.py`, `load.py` | Task 2: schema, inserts (one transaction per 1,000 emails), integrity checks |
| `dedupe.py` | Task 3: buckets, fuzzy comparison, flags, report |
| `notify.py`, `mcp_client.py` | Task 4: template, drafts, live-mode guards, MCP `send_email` calls, send logs |

**Database tables** (`schema.sql`):
- **`emails`:** one row per email, holding every single-value field, plus `is_duplicate`,
  `duplicate_of`, `notification_sent` and `notification_date`
- **`addresses`:** each distinct recipient address, stored once
- **`email_recipients`:** the linking table, one row per (email, address, to/cc/bcc), in
  the original order

`message_id` is UNIQUE, and `date`, `from_address` and `subject` are indexed. CHECK
constraints keep the flags and dates consistent.

---

## 6. Mailbox selection

The assignment asks for at least 5 mailboxes and 10,000 emails. These five give **34,222
files** and a mix of roles: two top executives, the COO of Enron Americas, a gas
logistics manager, and a pipeline account director. That means a range
of writing styles and mail tools (Lotus Notes and Outlook formats), and both personal and
broadcast mail. Owners and roles were confirmed from each mailbox's sent mail (sender
names and signatures; `exploration/sent_identity.py`).

| Mailbox | Owner | Role | Files | Parsed | Failed |
|---|---|---|--:|--:|--:|
| `lay-k` | Kenneth Lay | Chairman of the Board | 5,937 | 5,600 | 337 |
| `skilling-j` | Jeff Skilling | President & COO, later CEO | 4,139 | 3,744 | 395 |
| `kitchen-l` | Louise Kitchen | COO, Enron Americas | 5,546 | 5,304 | 242 |
| `farmer-d` | Daren J. Farmer | Gas logistics manager (Texas) | 13,032 | 12,862 | 170 |
| `lokay-m` | Michelle Lokay | Account Director, Transwestern pipeline | 5,568 | 5,295 | 273 |

Two things to know when reading the data:
- **Assistants sent most executive mail.** Lay's and Skilling's mail was mostly sent by
  their assistants: only about 15 of Lay's 540 sent emails and about 76 of Skilling's
  605 carry their own name.
- **Names vary.** For example "Lay, Kenneth" and "Kenneth Lay", or "Daren" and "Darren".

Details: [`notes/mailbox_selection.md`](notes/mailbox_selection.md).

---

## 7. Key decisions and trade-offs

The guiding rule throughout was to **follow the assignment literally, and document the
consequences.** Every decision below has its reason and its measured effect in `notes/`.

### Task 1: parsing ([`notes/parse_decisions.md`](notes/parse_decisions.md))
- **No `To` header means a failed parse** (1,415 emails). `To` is a mandatory field.
  This leaves broadcast memos ("All Enron Worldwide") out of the database. 81 of those
  emails have real addresses in `X-To` (probably BCC copies), but they still fail rather
  than falling back to `X-To`.
- **Splitting the body.** The text before the first forward or reply marker is the
  sender's own text. The rest goes to `forwarded_content`, or to `quoted_content` for a
  reply (decided by the subject: `FW:` means forwarded).
  - Lines starting with `>` always go to `quoted_content`, even under a `FW:` subject,
    because the spec defines quoted content as "lines beginning with >".
  - The splitter recognizes several Lotus Notes and Outlook layouts that were found in
    the data.
- **Empty values are allowed:** an empty subject (724 emails) or an empty own body (1,794
  pure forwards).
- **Dataset quirks:**
  - `Bcc` is identical to `Cc` in every email that has it, a known artifact of how the
    dataset was converted. It is stored as given.
  - 17 emails carry the Lotus Notes "unknown date" (1980-01-01), flagged with
    `date_is_sentinel`.
  - 21 subjects were still MIME-encoded and are decoded at parse time.
- **`has_attachment`** counts only real file evidence (`<<report.xls>>`, ` - resume.doc`,
  multipart MIME), not the word "attached". It is true for 12.7% of emails.
- **"Headings"** isn't defined by the spec. Here it means short title-like lines:
  underlined, ALL CAPS, `Label:`, `*** banner ***` or `1. Title`. This is a heuristic.

### Task 2: database ([`notes/db_decisions.md`](notes/db_decisions.md))
- **The sender is stored as text.** `from_address` is a text column, exactly as the spec
  names and indexes it. To/Cc/Bcc are normalized into `addresses` + `email_recipients`.
- **Keys:** an integer primary key, with `message_id` UNIQUE.
- **Dates:** UTC text, `YYYY-MM-DD HH:MM:SS`.
- **`duplicate_of`** holds the original's `message_id`.
- **The only extra column** is `date_is_sentinel`.
- **Reruns:** a `message_id` already in the database is skipped, never overwritten, so
  reruns can't wipe duplicate or notification state.
  - The trade-off: parser fixes don't reach rows that are already loaded; delete
    `data/enron.db` to rebuild.
  - The same `message_id` appearing twice within one run is logged in `error_log.txt`.

### Task 3: duplicates ([`notes/dedupe_decisions.md`](notes/dedupe_decisions.md))
**How it works:** emails are bucketed by sender and by subject with leading
`Re:`/`Fw:`/`Fwd:` removed. Within a bucket, each email is compared with the group's
original using `rapidfuzz` `fuzz.ratio`, after collapsing whitespace, and joins the group
if it scores ≥ 90.

**The main finding: 93% of duplicate groups are folder copies, not re-sends.** Lotus
Notes filed the same email in several folders (`sent`, `_sent_mail`, `all_documents`,
`discussion_threads`), and each copy got its own `message_id`, so the database's UNIQUE
rule can't catch them. They do meet the spec's definition (same sender, same subject,
body ≥ 90% similar), so they are flagged as the spec says, but the statistics report
them separately:

| | Groups | Emails flagged |
|---|--:|--:|
| Same timestamp (one email in several folders) | 6,852 (93.1%) | 13,371 (89.8%) |
| Different timestamps (likely real re-sends) | 504 | 1,525 |
| **Total** | **7,356** | **14,896** |

Even the 504 re-send groups include folder copies: of their 1,525 flagged emails, 668
share the original's timestamp and 857 are genuinely later. An example of a real re-send
is a weekly "Staffing" request sent again a week later with only the date changed
(99.3% similar).

Other rules:
- **Earliest and latest:** the earliest email is the original. On equal timestamps the
  lowest database id wins, and the latest (highest id) gets the notice. A copy dated
  1980-01-01 (the unknown date) is never the original.
- **Empty bodies:** if both bodies are empty, the forwarded content is compared; if
  that's empty too, the quoted content. This fixed false matches such as two different
  forwards, or two different empty "Remove" replies.
- **Subjects match exactly after the prefixes are removed,** so case differences count
  ("Q4 Plan" ≠ "Q4 plan"). Comparison runs across all mailboxes.

### Task 4: notifications ([`notes/notify_decisions.md`](notes/notify_decisions.md))
- **One draft per duplicate group** (7,356), addressed to the sender of the latest
  duplicate.
  > **Contradiction in the assignment:** §4.2 says to send a notice "for each duplicate
  > group", but the Deliverables table describes `output/replies/` as drafts "for all
  > flagged duplicates", which would be 14,896. This project follows §4.2: one notice
  > per group, matching what live mode sends. All 7,356 drafts are in
  > `output/replies.zip` (5.2 MB) rather than as thousands of loose files in git, with
  > 25 unzipped samples in `output/replies/`.
- **Drafts follow the template exactly:** `To`, `Subject`, `Date` and
  `References: <duplicate's message_id>`.
  - The subject is literal, `[Duplicate Notice] Re: <original subject>`. So when the
    original already starts with "Re:" (1,731 groups), the notice reads "Re: Re: …".
  - Body dates are written as `2001-10-25 16:18:00 UTC`.
- **Live sends:**
  - They go only to your override address, and add one first line,
    `[Test delivery: intended for <original sender>]`.
  - The 5 most recent re-send groups are sent first.
  - A group is never notified twice: it's skipped if any of its emails is already
    marked.
  - `notification_sent` and `notification_date` are set only after Gmail confirms the
    send.
  - `send_log.csv` has the spec's 5 columns first, then three tracing columns: the
    intended recipient, the duplicate's `message_id` and the Gmail message id.

---

## 8. What I'd do with more time

- **Detect folder copies explicitly.** Mark copies with the same sender, subject,
  timestamp and text as "one email filed in several folders". They would still meet the
  spec's definition of a duplicate, but reports and notices could then separate them
  from real re-sends instead of relying only on the timestamp categories.
- **Catch template emails.** Emails built from the same template (e.g. "Deal # 361155 has
  been created…" vs "Deal # 362921…") pass the 90% threshold while being about different
  things. Giving changed numbers and IDs extra weight in the comparison would keep them
  apart.
- **Learn heading detection from labelled examples.** Replace the hand-written heading
  rules with a small model trained on headings labelled by hand, and measure its
  precision and recall.
- **Handle the CONFIRM replies.** The notice asks senders to reply "CONFIRM" if their
  email wasn't a duplicate. Reading those replies through the MCP server and restoring the
  email's active status (clearing `is_duplicate`) would complete that loop.
- **Scale up to all 150 mailboxes** (517,401 files). That means streaming the parse
  instead of holding records in memory, and running the fuzzy comparisons in parallel per
  bucket.

---

## 9. Known limits

- **Broadcast memos are missing.** The strict `To` rule drops 1,415 emails (4.1%),
  including all-company announcements.
- **Templates can look like duplicates.** Two emails built from the same template can be
  ≥ 90% alike while being about different things (e.g. "Deal # 361155 has been created…"
  vs "Deal # 362921…"). That is a limit of the spec's definition.
- **Folder copies dominate the duplicate counts** (see §7). Without the category split,
  the totals would overstate how often people actually re-sent email.
- **Headings are heuristic.** An all-caps signature name can slip through.
- **The body split depends on markers.** It relies on known forward and reply markers;
  an unusual layout stays in the body.
- **Some text is garbled in the source.** Characters the dataset couldn't represent appear
  as `?` (e.g. "Cuiab? - Brazil"); 252 emails still show `=20`-style quoted-printable
  artifacts that weren't declared as such.
- **A few emails have no text at all** (3 emails), so they match each other at 100%.
  This forms 1 group.
- **Live mode has a tiny crash window.** If the program stops right after Gmail accepts
  a send but before the log and database are updated, a rerun could send that one notice
  again. At most 10 notices can ever be sent per run.
- **Gmail sets the header details on live sends.** The server's `inReplyTo` writes both
  `In-Reply-To` and `References` with the same value, and Gmail adds its own `Date:` and
  `Message-ID:`. Drafts show the template's headers exactly.
- **The sign-in token expires** after about 7 days in Google's Testing mode.
- **Python 3.10 itself wasn't tested.** The project was run on Python 3.11 and 3.13.

---

## 10. More detail

- `notes/`: every decision (parsing D1–D10, database T1–T8, duplicates X1–X10,
  notifications N1–N8), each with its reason, its measured effect, and the bugs found
  along the way.
- [`AI_USAGE.md`](AI_USAGE.md): how Claude Code was used, the prompts, the debugging
  stories, and the MCP integration.
- `sample_queries.sql`: example queries with expected results.
