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
- Committed as e5e192f. Claude Code also wrote notes/parse_decisions.md
  listing every decision with its effect on the data.

## Step 9: Task 2 plan (in progress)
- Asked Claude Code to plan the database in plan mode, explain it simply
  as if I'm new to databases, and give me options instead of
  recommendations for each decision.
- It explained tables, keys, normalization, indexes and transactions, and
  listed 8 decisions (T1-T8) with pros and cons. Still to decide.