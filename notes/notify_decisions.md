# Notification decisions (Task 4)

Decisions for the duplicate notifications (`docs/assignment.md` Task 4 and §5), with the
reason for each. They follow the project rules in `CLAUDE.md`:
- never send to dataset addresses
- live sends go only to the user's Gmail through `NOTIFY_RECIPIENT_OVERRIDE`, and live mode
  refuses without it
- a small send cap
- dry run is the default
- never commit credentials

An earlier decision also applies: drafts show the **original sender** as `To:`, and only
live delivery is redirected.

Input from Task 3: 7,356 duplicate groups and 14,896 flagged emails. The notification
target of a group is its latest duplicate (on a timestamp tie, the highest id).

## How it works

```
python -m enron_pipeline.notify                      dry run (default): one .eml draft per group
python -m enron_pipeline.notify --send-live          live: up to 5 notices (hard ceiling 10)
```
- **Dry run** never starts the MCP server. Each draft is addressed to the original Enron
  sender and follows the template exactly.
- **Live mode**:
  1. reads `NOTIFY_RECIPIENT_OVERRIDE` from `.env` and refuses to send if it is missing,
     malformed, an `@enron.com` address, or any address that appears in the dataset
  2. starts the Gmail MCP server and calls its `send_email` tool once per notice
  3. after each **successful** send, appends a row to the send log and sets
     `notification_sent = 1` and `notification_date` (UTC) on the duplicate. The full log
     is `data/send_log.csv` (git-ignored); the committed `output/send_log.csv` is rewritten
     after each run with the recipient address masked (N7f)
  4. on a failed send, logs it as failed and leaves the database unchanged, so the notice
     can be retried

## Decisions

| # | Decision | Reason |
|---|---|---|
| N1 | **MCP server: [ArtyMcLabin/Gmail-MCP-Server](https://github.com/ArtyMcLabin/Gmail-MCP-Server), npm `@artymclabin/gmail-mcp`, pinned to version 1.2.3** (MIT, Node.js). Signed in with **only the `gmail.send` scope** (`auth --scopes=gmail.send`). | It's a maintained fork of the best-known Gmail MCP server ([GongRzhe/Gmail-MCP-Server](https://github.com/GongRzhe/Gmail-MCP-Server), archived on 3 Mar 2026). It has a tool literally named `send_email`, as §5 requires. Its `inReplyTo` parameter writes the `References:` header (checked in `src/utl.ts`). Its scopes can be narrowed (checked in `src/scopes.ts`), and `send_email` accepts `gmail.send`, so the token can send mail but cannot read or change the mailbox. The version is pinned so setup is reproducible. |
| N2 | **The pipeline sends** through the official Python MCP SDK (`mcp`): it starts the server over stdio and calls `send_email`. The same server is **registered in Claude Code with local scope** (private, stored in `~/.claude.json`, not in the repo) for test sends and the prompts recorded in `AI_USAGE.md`. | `--send-live` then really sends, logs and updates the database in one repeatable command (§5 reproducibility). Local scope keeps the registration out of the repo. |
| N3 | **Subject is literal:** `[Duplicate Notice] Re: <original_subject>`, using the original's subject exactly. | Follows the template word for word. Consequence: 1,731 of the 7,356 originals already start with "Re:", which gives "[Duplicate Notice] Re: Re: …", and 144 originals have an empty subject. |
| N4 | **Drafts write `References: <duplicate message_id>` only** (no `In-Reply-To`). **Dates in the body look like `2001-10-25 16:18:00 UTC`.** | The template lists only `References:`. The date format is the database's UTC format with an explicit "UTC". Note: on live sends, the server's `inReplyTo` parameter writes both `In-Reply-To` and `References` with the same value, which the server doesn't allow to be separated. Gmail adds the `Date:` header itself on live sends. |
| N5 | **Live sends only:** the first body line is `[Test delivery: intended for <original sender>]`, followed by the template. **Drafts stay exactly as the template.** | A live notice arrives in the user's inbox, not the Enron sender's; the line makes each received email self-explanatory (and screenshots readable) without changing the drafts. |
| N6 | **One `.eml` draft per duplicate group** (7,356), for the group's notification target. **Repo:** `output/replies.zip` holds all drafts; about 25 sample drafts are also kept unzipped in `output/replies/`. | §4.2 says "for each duplicate group, send a notification", and live mode sends one notice per group. One archive keeps git history small while the deliverable stays complete. **Spec contradiction to document:** the Deliverables table describes `output/replies/` as drafts "for all flagged duplicates" (which would be 14,896 files); this project follows §4.2's per-group rule. |
| N7a | **Send cap:** `--max-sends` defaults to **5** with a **hard ceiling of 10**; a larger number is refused. | CLAUDE.md's small cap, plus protection against a typo such as `--max-sends 1000`. |
| N7b | **Which groups are sent:** the **5 most recent groups with different timestamps** (likely re-sends), by the notification target's date. | These are meaningful examples. The earliest groups in report order are mostly 1998–1999 folder copies. |
| N7c | **`output/send_log.csv` columns:** the spec's five first (`timestamp`, `recipient`, `subject`, `status`, `error`), then `intended_recipient`, `duplicate_message_id`, `gmail_message_id`. | The spec's columns come first, unchanged. The three extra columns trace each live send back to the Enron email it is about and to the message in Gmail. `recipient` is the override address the notice was actually delivered to. |
| N7d | **Never send twice:** a group is skipped if **any** of its emails has `notification_sent = 1`. | This stays safe if Task 3 is rerun and picks a different "latest" copy for a group that was already notified (Task 3 never touches the notification columns, X9). |
| N7e | **Crash window accepted:** if the program stops after Gmail accepted a send but before the log and database were updated, a rerun could send that one notice again. | The window is a single send, the log row and database update are written immediately after success, and at most 10 notices can ever be sent. |
| N7f | **Two send logs.** The full log (`data/send_log.csv`, git-ignored, appended to) has the real recipient address. The committed `output/send_log.csv` is a copy with every recipient address masked (`r***@gmail.com`), including inside error text. Dataset addresses (`intended_recipient`) are not masked. | The spec's `recipient` column is the override address, the user's own Gmail; a public repo must not reveal it, while the local log keeps full traceability. |
| N8 | **Secrets live outside the repo in `~/.gmail-mcp/`:** `gcp-oauth.keys.json` (the OAuth client) and `credentials.json` (the token). `.env` in the repo root (git-ignored) holds only `NOTIFY_RECIPIENT_OVERRIDE`. `gcp-oauth.keys.json` is also added to `.gitignore` in case a copy lands in the repo. Only `mcp_config.json.example` and `.env.example`, with placeholders, are committed. | They can't be committed by accident. It is also the server's default location. |

## OAuth setup facts
- **Client type:** Desktop app. Google allows the server's local callback
  (`http://localhost:3000/oauth2callback`) for Desktop clients without registering it.
- **Consent screen:** in **Testing** mode, with the user's Gmail as a test user, and the
  `gmail.send` scope added.
- **Testing-mode tokens expire after about 7 days.** If live sending later fails with an
  auth error, sign in again with the same `auth` command.

## Results (dry run, after the D10 rebuild)
- **Drafts:** 7,356, one per group (6,852 same-timestamp groups, 504 different-timestamp
  groups), 0 errors. They are in `output/replies.zip` (5.2 MB), plus 25 samples in
  `output/replies/`. The samples are the 5 groups live mode would send first, plus 20
  spread evenly across all groups.
- **Every draft was re-verified against the database and the template:**
  - `To:` is the latest duplicate's sender
  - `Subject:` is `[Duplicate Notice] Re: ` plus the original's subject
  - `References:` is the latest duplicate's message_id
  - no `From:` / `In-Reply-To:`
  - the body is identical to the template
- **Subjects (N3):** 1,731 of the 7,356 originals already start with "Re:" (so the notice
  says "Re: Re: …"), and 144 are empty. 67 stored subjects keep a tab from the original
  folded header; drafts reproduce it exactly.
- **Encoded subjects:** before parse decision D10, 4 drafts had a raw `\x01` control
  character in `Subject:`, because the stored subject was still RFC 2047-encoded and the
  email library decoded it. After the rebuild, 4 notice subjects and 4 body `Subject:`
  lines are readable text, and no draft subject contains a control character.

## Bug found while building
- **Live selection put a sentinel-dated target first.** "Most recent" (N7b) reused Task
  3's ordering key, where sentinel copies sort *last*. Sorting that key in reverse put the
  1980-01-01 copy of tori.wells "Re: Update from cousins in Missouri" *first*. The
  selection now orders real dates newest first and sentinel targets last, with a
  regression test (`test_select_live_puts_sentinel_targets_last`). The same check corrected
  `notes/dedupe_decisions.md`, which had said no group mixes sentinel and real dates; 1 does.


## Live run (2026-10-11)
`python -m enron_pipeline.notify --send-live --max-sends 5`: **5 selected, 5 sent, 0 failed,
0 unconfirmed.** All five were delivered only to the override address through
`@artymclabin/gmail-mcp@1.2.3` (`send_email` with `inReplyTo` set to each duplicate's
message_id).

| Gmail message id | Intended (dataset) sender | Notice subject |
|---|---|---|
| 1a1299fcb7b649f5 | kimberly.watson@enron.com | [Duplicate Notice] Re: FW: TW Question in Amarillo |
| 1a1299fcc850e82f | info@pmaconference.com | [Duplicate Notice] Re: New Power Executive-- Free Sample Issue |
| 1a1299fcf4613b7c | admin.enron@enron.com | [Duplicate Notice] Re: An Inbound Message For You Has Been Quarantined |
| 1a1299fd31a02712 | bruno@eyeforenergy.com | [Duplicate Notice] Re: h: Eyeforenergy Briefing |
| 1a1299fd76184db4 | enron_update@concureworkplace.com | [Duplicate Notice] Re: <<Concur Expense Document>> - Expenses 02/27/02 |

- **Database:** the five notification targets have `notification_sent = 1` and
  `notification_date` from 2026-10-11 06:21:40 to 06:21:44 UTC.
- **Logs:** `output/send_log.csv` (masked) and `data/send_log.csv` (full) each have 5 rows.
- **Rerun check:** selection now skips these 5 groups and would choose 5 different ones.
- **Setup test:** before this, a single test email was sent from Claude Code with the same
  server (Gmail id `1a1298c7c0b1b4da`, subject "[MCP test] Enron pipeline").
