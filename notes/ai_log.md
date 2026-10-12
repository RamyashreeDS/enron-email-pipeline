# AI usage log (raw notes)

## Setup
- Installed Claude Code 2.1.296. Issue: "command not found" after install,
  because ~/.local/bin wasn't on PATH. Fixed by adding it to ~/.zshrc.
- Using Opus 5.5 on Northeastern's Claude Enterprise plan. Using Claude
  chat as a step-by-step guide and for explaining concepts.

## Step 3: Dataset and mailbox counts
- Asked Claude Code to move the archive into data/, gitignore it, and
  count files per mailbox without extracting.
- Result: 150 mailboxes, 517,401 files; it verified the total matched the
  archive. It noted on its own that the same email appears in several
  folders (all_documents, inbox, discussion_threads).

## Step 4: Mailbox selection and verification
- Chose lay-k, skilling-j, kitchen-l, farmer-d, lokay-m (34,222 files),
  based on a suggestion from Claude chat (mix of executive, trading,
  operations and pipeline roles).
- Asked Claude Code to verify each owner's role from X-From headers and
  signatures. This corrected the assumed roles: Skilling was President &
  COO (later CEO); Kitchen was COO of Enron Americas.
- It found that assistants sent many emails on behalf of Lay and Skilling,
  and that names vary (Lay, Kenneth / Kenneth Lay; Daren / Darren).

## Step 5: Foundation
- Gave Claude Code the assignment spec and asked for a deliverables
  checklist; it matched the assignment.
- It enforced the no-credentials rule in .gitignore, and made live
  sending refuse to run without an override address (cap of 5).
- It flagged errors in the assignment: a missing MCP server list and a
  wrong section reference.

## Step 6: Parser plan (plan mode, effort high)
- Asked for a plan grounded in real samples before any code.
- It scanned all 34,222 files and found concrete edge cases: CRLF headers
  vs LF bodies, a bare LF in Subject breaking the stdlib parser, non-ASCII
  headers, 1,415 emails missing To, Bcc identical to Cc in all 9,173
  cases, 17 sentinel 1979 dates.
- Following Claude chat's suggestions, approved decisions D1-D9, added a
  date_is_sentinel flag, and split the build into two stages.

## Step 7: Stage 1 build (core parser)
- Random 500-email sample of lay-k: 477 parsed, 23 failed (all MISSING_TO),
  0 crashes. Full 34,222-file run: 6.6 s, 1,417 failures.
- DEBUGGING STORY 1: On Python 3.13, getaddresses() runs in strict mode
  and returns ('', '') for the whole header if one item is malformed,
  silently wiping out lists of 100+ valid recipients. Fixed by parsing
  each address separately; regression test added.
- DEBUGGING STORY 2: the header-repair counter never counted bare-LF
  repairs, so the HEADER_REPAIRED warning could never fire. Fixed.

## Turning point: slowing down to understand
- Realized I was approving Claude Code's actions and passing on
  suggestions without understanding them. Paused the build.
- Opened a raw email (lay-k/inbox/1.) and went through it line by line:
  headers vs body, To vs X-To, the date conversion to UTC (19:36 PDT on
  May 14 becomes 02:36 UTC on May 15), and the wrapped subject line.
- Lesson: an AI agent can do the work fast, but I need to understand the
  data first, or I can't judge whether its decisions are right.

## My decisions after understanding
- X-To fallback: chose Option B (strict). If an email has no To header,
  it fails, even if X-To has an address. Reason: it's only 81 emails, and
  I prefer following the specification exactly.
- has_attachment: kept Claude Code's rule (D7), true only with file-level
  evidence like <<bio.doc>>. Example that made me think about it:
  lay-k/inbox/1. says "I'm attaching my bio" but has no file marker. I
  first said it should be true, then agreed with the stricter rule after
  thinking about precision vs recall: the word "attach" appears in 6,594
  files and is often misleading (future tense, quoted text, other
  meanings).

## Step 8: Stage 2 (body splitting, attachments, headings)
- Full run: 34,222 files in ~11 s; 32,805 parsed, 1,417 failed.
  148 tests pass.
- Asked Claude Code to explain results simply and show 3 real examples
  before committing, so I could check the splitting myself.
- DEBUGGING STORY 3: 819 replies weren't split at all, because the first
  version missed two Lotus Notes reply layouts. Found on the real data.
- DEBUGGING STORY 4: the JSONL writer wrote control characters (\x85,
  \x1c) raw, so some readers split one record across two lines. Fixed by
  writing ASCII-escaped JSON.
- Also fixed: a ?-----Original Message----- marker hidden by a replaced
  character, and heading false positives like [IMAGE] (209 times).
- Plan correction: only 15 emails had leaked SMTP headers, not ~105.
- DECISION (example 3): when the subject says FW: but the earlier
  message's lines start with >, it goes into quoted_content. Reason: the
  assignment defines quoted_content as "lines beginning with >", and I'm
  following the spec's wording consistently. Affects 267 emails; the body
  is the same either way.
- Committed as d58d9f6. Claude Code also wrote notes/parse_decisions.md
  listing every decision with its effect on the data.

## Step 9: Task 2 plan (in progress)
- Asked Claude Code to plan the database in plan mode, explain it simply
  as if I'm new to databases, and give me options instead of
  recommendations for each decision.
- It explained tables, keys, normalization, indexes and transactions, and
  listed 8 decisions (T1-T8) with pros and cons. Still to decide.

## Step 9-10: Task 2 (database)
- Asked Claude Code to plan the database in plan mode, explain it simply
  as if I'm new to databases, and give me options for each decision.
- Decisions T1-T8: sender as a text column (matches the spec's
  from_address), integer id plus unique message_id, skip existing
  message_ids on rerun (safe for notification history), readable UTC
  text dates, only date_is_sentinel added beyond the spec.
- Load: 32,805 emails, 328,828 recipient rows, 0 errors, ~16-19 s.
  Second run: 0 inserted, 32,805 skipped, reported as one count.
- Checks: SQLite integrity and foreign-key checks pass; 200-email round
  trip from the database matched the parser with 0 mismatches.
- Consequence of T1: addresses table has 14,815 (recipients only), since
  2,327 addresses only appear as senders.

## Step 11-12: Task 3 (duplicate detection)
- Claude Code measured the data before planning: 7,238 candidate groups;
  in 5,429 (75%), all copies share the same timestamp, meaning one email
  stored in several folders, not re-sends.
- Caught a number that didn't make sense: Claude Code wrote "74,900 out
  of 7,238 candidate groups". I asked; it gave the correct figure, 5,429
  of 7,238, with a breakdown that adds up. But it also claimed it had
  never written 74,900, even though the number was visible on screen in
  its own message. Lesson: verify against what's actually on screen,
  not against the AI's account of its own output.
- Biggest decision (X1): following the spec literally flags ~14,900
  emails, but ~93% of groups are folder copies; merging copies first
  would flag only ~700. I chose to follow the spec literally but be
  transparent: the stats split groups into folder copies vs likely
  re-sends, and the README explains it.
- Other decisions: lowest id breaks ties, empty bodies compare the
  forwarded part instead, sentinel-dated copies can't be the original,
  compare to the group's original, rapidfuzz with whitespace collapsed.
- Before building, Claude Code tested rapidfuzz itself: two empty strings
  score 100, so it checked how often "nothing vs nothing" could happen.
- DEBUGGING STORY 5: 2 tests failed at first because of the test code:
  sqlite3.Row objects don't compare equal to plain tuples. Fixed.
- Claude Code found 2 false re-sends caused by my empty-body rule (empty
  replies to different messages matched as "" vs ""), and asked me
  instead of changing the rule. I extended the rule: if body and
  forwarded part are both empty, compare the quoted part. Fixed both,
  kept the 33 correct folder-copy groups.
- Final: 7,356 groups, 14,896 flagged, ~1.5 s (vs ~4.5 min with difflib).
- Known limit: template emails ("Deal # 361155 has been created" vs
  "Deal # 362921") are 95.5% alike but are different deals.

## Step 13-15: Google Cloud and MCP setup (manual steps)
- Created a Google Cloud project, enabled the Gmail API, configured the
  OAuth consent screen (External, Testing), added my Gmail as the only
  test user, created a Desktop-app OAuth client, allowed only the
  gmail.send scope, moved the client file to ~/.gmail-mcp/ (locked to
  my user), installed Node.js, signed in once (send-only), created .env
  with my address, and registered the server in Claude Code (local scope).
- Claude Code compared 4 MCP servers by checking each project's code and
  registry: GongRzhe's original is archived and needs broad mailbox
  access; google_workspace_mcp's tool isn't named send_email. Chose the
  maintained ArtyMcLabin fork, pinned at 1.2.3, send-only permission.
- Found a contradiction in the spec: Task 4 says one notice per group
  (7,356); the deliverables table says drafts for all flagged
  duplicates (14,896). Chose per group; will document it.
- MCP ISSUE 1: `claude mcp add` with --env before the server name failed
  ("Invalid environment variable format: gmail"); --env takes several
  values and swallowed the name. Claude Code recognized its own mistake
  and fixed the command.
- MCP ISSUE 2: `claude mcp get gmail` said "No MCP server named gmail"
  because the corrected add hadn't been run yet. After running it:
  Connected.
- FIRST MCP SEND, my prompt: "Use the gmail MCP send_email tool to send
  one test email to the address in NOTIFY_RECIPIENT_OVERRIDE in my .env
  file. Subject: '[MCP test] Enron pipeline'. Body: one line saying this
  is a setup test for my duplicate notification pipeline. Don't send to
  any other address." Result: "Email sent successfully with ID:
  1a1298c7c0b1b4da". It arrived; screenshot saved.

## Step 16-17: Task 4 build, parser fix, live send
- Dry run: 7,356 drafts in output/replies.zip (5.2 MB) plus 25 samples;
  every draft checked against the database and the template.
- Claude Code caught its own problems: a selection bug (the first live
  candidate had the 1980 sentinel date), a wrong claim in its own notes
  (one group does mix sentinel and real dates), and an MCP SDK API
  change it checked in the installed version instead of trusting memory.
- Found a Task 1 parser gap: 21 subjects still MIME-encoded (RFC 2047).
  DECISION: fix the parser at the source and rebuild everything, before
  any live send so no notification history was lost. All totals stayed
  the same; only the 21 subject texts changed. 270 tests pass.
- DECISION: commit a masked send log (r***@gmail.com) so the deliverable
  is complete but my address stays off GitHub.
- LIVE SEND: python -m enron_pipeline.notify --send-live --max-sends 5.
  Sent: 5, Failed: 0. All 5 [Duplicate Notice] emails arrived, each
  starting with "[Test delivery: intended for ...]". Database marks
  exactly those 5; a rerun would skip them.
- Claude Code caught a .gitignore problem: the .env.* pattern also
  excluded .env.example. Fixed with an exception.
- Screenshots: inbox, one full notice, and Show original (References:).

## Step 18: main.py and fresh-clone test
- Asked Claude Code to build main.py and test it as a reviewer would:
  fresh clone, new venv, install, run, tests.
- Results: setup ~4 s; without data it stops with download instructions;
  full pipeline 21 s with the same numbers; rerun safe; 276 tests pass.
- The fresh-clone test found 8 setup problems for the README, e.g. macOS's
  built-in Python 3.9 gives a misleading pip error; installs fail outside
  the repo root; a fresh clone's empty database would let --send-live
  resend the same 5 notices.

## Step 19: README
- Claude Code drafted the README from the notes and checked every
  documented command exists and every number comes from the notes. It
  corrected its own wording about Louise Kitchen to match the evidence.
- I reviewed it and asked for two additions: CLAUDE.md in the layout
  table, and a "What I'd do with more time" section.
- I accidentally sent a prompt with a leftover placeholder ("[paste the
  items you chose]"). Claude Code noticed and refused to invent the
  items itself, since they're my own judgment in an evaluated document.
  I chose: detect folder copies explicitly, catch template emails, learn
  heading detection from labelled examples, handle CONFIRM replies,
  scale up to all 150 mailboxes.