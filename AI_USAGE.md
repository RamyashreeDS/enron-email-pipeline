# AI Usage

This file describes how I built this project with AI tools: which tools, how I prompted
them, where their work failed and how it was fixed, and how the Gmail MCP integration was
set up and used. Everything here comes from my working log (`notes/ai_log.md`), the
decision notes in `notes/`, and the Claude Code session itself.

---

## 1. Tools used

| Tool | Version | What I used it for |
|---|---|---|
| **Claude Code** (CLI) | 2.1.296, model Claude Opus 5.5, through Northeastern's Claude Enterprise plan | Writing and running all the code: exploring the data, planning (in plan mode), building each module, running the pipeline and tests, git commits, and the Gmail MCP integration |
| **Claude chat** (maybe around 30% for rewriting the prompt in better english) | Same plan | A step-by-step guide alongside Claude Code: explaining concepts I didn't know (email headers, database design, OAuth), and suggesting options for the decisions Claude Code put in front of me |

**Getting started:** after installing Claude Code, `claude` gave "command not found",
because `~/.local/bin` wasn't on my `PATH`. I fixed it by adding it to `~/.zshrc`.

**Project rules for the AI:** before any code, I gave Claude Code a `CLAUDE.md` file
with the project rules:
- the requirements live in `docs/assignment.md`
- never crash on a malformed email
- never send email to a real Enron address; live sends go only to my own Gmail, through
  an override, with a send limit
- never commit data, the assignment document, or credentials
- small, testable modules
- explain the plan before large changes

---

## 2. Prompting strategy

### Task by task, not the whole spec at once
I gave Claude Code the full assignment early, as `docs/assignment.md`, and asked it to
summarize the deliverables as a checklist so I could confirm it had understood them. The
checklist matched the assignment. Claude Code also pointed out two errors in the
assignment itself: a missing list of MCP servers, and a wrong section reference.

After that I worked **one task at a time**, and for each task I used the same pattern:

1. **Plan first, with no code.** I used plan mode and asked for a plan grounded in the
   real data, not in assumptions.
2. **Make Claude Code measure the data before deciding.** For the parser it scanned all
   34,222 files for edge cases; for duplicates it measured how many groups were folder
   copies.
3. **Make the decisions myself.** I asked for options with pros and cons rather than a
   single recommendation, then chose (decisions D1–D10, T1–T8, X1–X10, N1–N8 in `notes/`).
4. **Build in small stages, and see results before committing.** I asked for statistics,
   failures and real examples I could check myself before anything was committed.
5. **Record every decision** with its reason and measured effect in `notes/`, so the
   README could be written from facts.

### The turning point: slowing down to understand
Partway through Task 1, I realized I was approving Claude Code's actions and passing on
Claude chat's suggestions **without really understanding them**. So I paused the build.

I opened one raw email (`maildir/lay-k/inbox/1.`) and went through it line by line:
- which part is the header and which is the body
- the difference between `To` and `X-To`
- how the date converts to UTC (19:36 PDT on 14 May becomes 02:36 UTC on 15 May)
- why the subject line was wrapped onto two lines

After that, my decisions were my own. For example:
- **The X-To fallback.** I chose the strict rule: an email with no `To` header fails,
  even when `X-To` has an address. It's only 81 emails, and I prefer following the
  specification exactly.
- **`has_attachment`.** At first I thought `lay-k/inbox/1.` ("I'm attaching my bio")
  should count as having an attachment, but it has no file marker. After thinking about
  precision vs recall, I agreed with the stricter rule: the word "attach" appears in
  6,594 files and is often misleading (future tense, quoted text, other meanings).

The lesson: an AI agent can do the work fast, but I need to understand the data first,
or I can't judge whether its decisions are right. From then on I asked for explanations
"in simple words, as if I'm new to this" and for real examples with every result.

### Example prompts, and why I wrote them that way

**1. Exploring before building** (the first prompt):
> "Please move it into a data/ folder in this project, add data/ to .gitignore so it's
> never committed, and then count how many email files each employee mailbox contains,
> without extracting the archive."

*Why:* small, checkable steps. Protect the data from being committed first, then look at
it without changing anything. The counts (150 mailboxes, 517,401 files) were verified
against the archive, and they shaped which mailboxes I chose.

**2. Grounding the parser plan in real data** (Task 1):
> "Now plan Task 1, the extraction pipeline. Don't write code yet. First, look at a
> sample of real emails from all five mailboxes (including some from sent, inbox, and
> all_documents folders) to find the actual edge cases in this data: date formats,
> multi-line To headers, forwarded and quoted content markers, attachment references,
> missing fields, encoding problems. Then give me a plan covering: … a list of
> assumptions or decisions you need me to approve"

*Why:* without the "look at real emails first" step, the plan would have been based on
what emails usually look like, not on this dataset. The scan found problems I would
never have guessed:
- headers with CRLF line endings but bodies with plain LF
- a stray line break in a `Subject` that breaks Python's email parser
- 1,415 emails with no `To` header
- `Bcc` identical to `Cc` in every case
- 17 emails dated 1979

**3. Asking to understand, and to decide myself** (Task 2):
> "Important: I want to understand this. Explain the design in simple terms, as if I'm
> new to databases. For each decision I need to make, explain it plainly, give me the
> options with their pros and cons, and don't just recommend one; I'll decide."

*Why:* this came straight from the turning point. Claude Code explained tables, keys,
normalization, indexes and transactions, then gave me 8 decisions (T1–T8), each with
options. When it then opened a form to collect my answers, I interrupted it and gave my
decisions in my own words, with my own conditions (for example, report reruns as a single
count instead of thousands of log lines).

**4. Building in stages, with a checkpoint** (Task 1):
> "One change to the process: build in two stages. Stage 1: models, discovery, rawfile,
> headers, addresses, dates, parser (mandatory fields only), stats, extract.py with
> error_log.txt, plus unit tests for dates and addresses. Then run the 500-email lay-k
> sample and show me the stats and failures before continuing."

*Why:* a smaller first stage meant I could check the results before more code was built
on top of them. The checkpoint paid off: the 500-email sample showed that some emails
without a `To` header had real addresses in `X-To`. That led to the X-To decision above.

**5. Testing like a reviewer** (`main.py`):
> "Then test it the way a reviewer would: make a fresh clone of the repo in a temporary
> folder, create a new virtual environment there, install requirements.txt, point it at
> my extracted data, run python main.py, and run the tests. Tell me anything that failed
> or was unclear in that fresh setup, since those are things the README must explain."

*Why:* code that works on my machine can still fail for someone else. The fresh clone
found 8 setup problems that went into the README. The worst was that macOS's built-in
Python 3.9 gives a misleading pip error ("setup.py not found") rather than saying the
version is too old.

---

## 3. Iterations and debugging

These are cases where the AI-generated code (or the AI's own numbers) were wrong at
first, how the problem was found, and how it was fixed.

**1. A whole recipient list silently disappeared** (Task 1)
- **What went wrong:** on Python 3.13, `email.utils.getaddresses()` runs in strict mode.
  If one item in a header is malformed, it returns `('', '')` for the **whole** header.
  So one bad address could wipe out a list of more than 100 valid recipients without any
  error.
- **How it was found:** before using `getaddresses()`, Claude Code tested it on real
  address forms from the data instead of assuming it behaved like older versions.
- **How it was fixed:** each address in the list is now parsed separately, so a bad item
  only drops itself. A regression test was added.

**2. A warning that could never fire** (Task 1)
- **What went wrong:** the header-repair code fixed broken `Subject` lines correctly, but
  its counter never counted that kind of repair, so the `HEADER_REPAIRED` warning could
  never appear.
- **How it was found:** checking the two real broken emails showed the repair working
  but the counter at 0.
- **How it was fixed:** the counter now counts both kinds of repair.

**3. 819 replies weren't split at all** (Task 1, stage 2)
- **What went wrong:** the first version of the body splitter missed two Lotus Notes
  reply layouts: sender and time on one line ("From: … 03/09/2001 01:02 PM"), and name,
  time and `To:` separated by blank lines. Those replies stayed in the body whole.
- **How it was found:** after the first full run, Claude Code searched the bodies for
  leftover markers and found 819.
- **How it was fixed:** the splitter now detects the very distinctive `To:` → `cc:` →
  `Subject:` sequence and works back to the sender line. That brought the 819 down to 22,
  and those 22 turned out to be memo headers that really belong in the body.
- **Related fixes in the same round:** fully quoted emails weren't re-split after
  removing the `>` marks; a `?-----Original Message-----` marker was hidden by a replaced
  character; and the headings rules produced false headings such as `[IMAGE]` (209
  times).

**4. One record split across two lines** (Task 1, stage 2)
- **What went wrong:** the JSONL output wrote control characters such as `\x85` and
  `\x1c` as raw characters. Python's `splitlines()`, and some JSONL readers, treat those
  as line breaks, so one record looked like two.
- **How it was found:** a robustness test failed: 13 lines instead of 3.
- **How it was fixed:** the JSONL is now written as ASCII-escaped JSON.

**5. A number I caught on screen** (Task 3 planning)
- **What went wrong:** Claude Code's message showed "74,900 out of 7,238 candidate
  groups", which can't be right.
- **How it was handled:** I asked about it. It gave the correct figure, 5,429 of 7,238,
  with a breakdown that adds up. But it also said it had never written 74,900, even
  though the number was visible in its message on my screen.
- **What I took from it:** verify against what is actually on screen, not against the
  AI's account of its own output. I told it to be careful with numbers in the plan; from
  then on it copied every number straight from the measurement output and re-checked the
  ones it had worked out rather than measured.

**6. My own rule created two false re-sends** (Task 3)
- **What went wrong:** my rule for empty bodies said to compare the forwarded part when
  both bodies are empty. But two replies with no text of their own and no forwarded part
  then compared "" with "", scored 100%, and were flagged as re-sends. One was a
  "Remove" reply to two different senders a month apart; the other, two replies to
  different messages.
- **How it was found:** Claude Code checked every group matched this way and found 2
  that were wrong. It asked me rather than changing my rule.
- **How it was fixed:** I extended the rule: if the forwarded parts are empty too,
  compare the quoted parts. Both false re-sends disappeared, and the 33 correct
  folder-copy groups stayed flagged.

**7. Smaller test-code mistakes**
Several first test runs failed because of the tests, not the code:
- `sqlite3.Row` objects don't compare equal to plain tuples
- a wrong count of the fixture files
- an assumption that database ids matched message numbers

Each time, Claude Code explained why the test was wrong before fixing it.

**8. Bugs found during Task 4**
- **A selection bug:** the first email chosen for a live send had the 1980 "unknown"
  date. The "most recent first" sort had reversed a key that put those dates last.
- **A wrong claim in the notes:** they said no duplicate group mixes 1980 and real dates;
  one does.
- **A parser gap from Task 1:** 21 subjects were still stored MIME-encoded
  (`=?ANSI_X3.4-1968?Q?…?=`), and in 4 drafts they produced a control character in the
  `Subject:` header. I chose to fix the parser at the source and rebuild everything from
  scratch, before any live send so no notification history was lost. Every total stayed
  the same; only the 21 subject texts changed.
- **A `.gitignore` pattern:** `.env.*` also excluded `.env.example`, so the template
  would never have been committed. Fixed with an exception.

---

## 4. What I wrote vs. what the AI wrote

**Code: about 95% of the code, tests and SQL was generated by Claude Code.** I didn't
write modules by hand. My part was everything around the code:

- **Direction.**
  - I broke the assignment into stages.
  - I always asked for a plan before code.
  - I asked Claude Code to show me real examples and results before every commit.
  - Many of my prompts were drafted using Claude chat's(maybe around 30% for rewriting the prompt in better english) help, then adapted by me.
- **Decisions.** I made the final call on every design decision, often after weighing
  options that Claude chat or Claude Code laid out with pros and cons. For example:
  - rejecting emails without a `To` header instead of using `X-To`
  - following the spec literally for duplicates while reporting the 93% folder copies
    separately
  - extending my empty-body rule when it produced two false matches
  - fixing the parser at the source before the live send
- **Verification.** I checked the AI's work against what I could actually see:
  - I read a raw email line by line.
  - I confirmed each mailbox owner's role from the data.
  - I caught a number that couldn't be right (74,900 of 7,238).
  - I checked the drafts, the live emails and the screenshots myself.
- **Setup.** I did the whole Google Cloud and Gmail setup by hand.
- **Documentation.** The decision notes and README were drafted by Claude Code from my
  decisions, and I reviewed them and asked for changes. My working log was drafted with
  Claude chat from what I reported, and I reviewed it.

---

## 5. Lessons learned

**What worked well:**
- Asking for a plan and real examples before every commit, and checking results against
  what I could see.
- **An AI can be confidently wrong about itself.** Claude Code wrote "74,900 of 7,238",
  then claimed it never wrote that, while the number was on screen. I learned to trust
  what I can see over what the AI says about its own output.

**What was harder than expected:**
- Understanding the work well enough to judge it. At first I approved steps without
  understanding them, and I had to slow down and study a raw email line by line before I
  could make my own decisions.
- Understanding the dataset took me little more than what i expected.

---

## 6. MCP integration

### 6.1 Which MCP server I chose, and why
**[@artymclabin/gmail-mcp](https://github.com/ArtyMcLabin/Gmail-MCP-Server), version 1.2.3
(pinned), MIT licence, run with Node.js.**

Claude Code compared four options by checking each project's own code and package
registry, not only its README:

| Option | Why not / why |
|---|---|
| GongRzhe/Gmail-MCP-Server (the best-known one) | **Archived on 3 March 2026** (no more fixes), and it always asks for broad read/modify access to the mailbox |
| taylorwilsdon/google_workspace_mcp | Very active, but its send tool is called `send_gmail_message`, while the assignment requires a `send_email` tool |
| A small custom server | Allowed by the assignment, but I preferred configuring a known open-source server |
| **ArtyMcLabin fork (chosen)** | A maintained fork of the best-known server, with a tool literally named `send_email`. It can be **limited to the send-only Gmail permission**, so the token can't read or change my mailbox. Its `inReplyTo` parameter writes the `References:` header the template needs |

The version is pinned so the setup is reproducible.

### 6.2 Setup, step by step
I did these steps by hand, one at a time, with Claude Code checking each one before the
next. For example, it confirmed that the downloaded file existed and that the token's
scope was send-only, without ever displaying the secret values.

1. **Google Cloud:** I created a project and enabled the Gmail API. I configured the
   OAuth consent screen (External, **Testing** mode) with my Gmail as the only test user.
2. **Scope:** on the consent screen's Data access page, I allowed only
   **`.../auth/gmail.send`**.
3. **OAuth client:** under Credentials → OAuth client ID, I chose application type
   **Desktop app** and downloaded the JSON file.
4. **Store it outside the repo:**
   `~/.gmail-mcp/gcp-oauth.keys.json`, with the folder and file readable only by my user.
5. **Install Node.js:** `brew install node`.
6. **Sign in once, send-only:**
   `npx -y @artymclabin/gmail-mcp@1.2.3 auth --scopes=gmail.send`. Claude Code then
   confirmed that the saved token's only scope is `https://www.googleapis.com/auth/gmail.send`.
7. **`.env`** (git-ignored): `NOTIFY_RECIPIENT_OVERRIDE=<my Gmail>`. I created it myself,
   so the address didn't appear in the conversation.
8. **Register with Claude Code** (local scope, private to me and not in the repo):
   ```bash
   claude mcp add gmail --scope local \
     -e GMAIL_OAUTH_PATH=$HOME/.gmail-mcp/gcp-oauth.keys.json \
     -e GMAIL_CREDENTIALS_PATH=$HOME/.gmail-mcp/credentials.json \
     -- npx -y @artymclabin/gmail-mcp@1.2.3
   ```
   `claude mcp get gmail` then showed **✔ Connected**.

The full instructions for someone else are in [`README.md` §4](README.md#4-gmail-mcp-setup-only-for-live-sending),
and the structure is in `mcp_config.json.example`.

### 6.3 How I prompted the AI to use `send_email`
**A first test send from Claude Code**, using the MCP tool directly:
> "Use the gmail MCP send_email tool to send one test email to the address in
> NOTIFY_RECIPIENT_OVERRIDE in my .env file. Subject: '[MCP test] Enron pipeline'. Body:
> one line saying this is a setup test for my duplicate notification pipeline. Don't send
> to any other address."

Claude Code read the address from `.env`, sent exactly one plain-text email with no
cc or bcc, and got back `Email sent successfully with ID: 1a1298c7c0b1b4da`.

**The pipeline's live run.** For the real notices I didn't ask the AI to send emails one
by one. The pipeline calls the same `send_email` tool itself, through the official Python
MCP SDK, so a single command is reproducible. Before that I asked to see the 5 notices
first:
> "After that, show me the 5 notices the live run would send, and wait for my go before
> sending."

Then, after reading them:
> "go, send the 5 notices"

Claude Code ran `python -m enron_pipeline.notify --send-live --max-sends 5`.

### 6.4 Issues during setup and sending, and how they were resolved
1. **`claude mcp add` rejected the command:** "Invalid environment variable format:
   gmail". The `--env` option takes several values in a row, so it swallowed the server
   name. Claude Code recognized this as its own mistake in the command it had given me;
   putting the name first (`claude mcp add gmail --scope local -e … -e … -- npx …`) fixed it.
2. **"No MCP server named gmail".** I ran `claude mcp get gmail` before the corrected
   `add`. Claude Code checked its config file to confirm nothing had been registered,
   then I ran the `add`, and `get` showed Connected.
3. **The MCP Python SDK had changed.** Version 2.x renamed things compared with the 1.x
   API Claude Code knew: the result field is now `is_error`, and the server class
   `FastMCP` is now `MCPServer`. Claude Code checked the installed version before writing
   the client instead of trusting its memory, and proved one real round trip against a
   fake server first.
4. **The fake test server hid real errors.** It only returned "Error executing tool
   send_email"; it now raises the SDK's `ToolError`, so the real message comes through.
5. **Header limits of the server.** Its `inReplyTo` writes both `In-Reply-To` and
   `References` with the same value, and they can't be separated. Gmail also adds its own
   `Date:` and `Message-ID:`. The drafts show the template's headers exactly; the live
   emails have these extra headers.
6. **Privacy.** The spec's send log has a `recipient` column, which is my Gmail. I decided
   to commit a masked copy (`r***@gmail.com`) and keep the full log git-ignored in `data/`.
7. **Token lifetime.** In Google's Testing mode the sign-in expires after about 7 days.
   It didn't affect this project, but the README warns about it.

### 6.5 Proof of successful live sends
**Log excerpt**, `output/send_log.csv` (committed, address masked):
```
timestamp,recipient,subject,status,error,intended_recipient,duplicate_message_id,gmail_message_id
2026-10-11 06:21:40,r***@gmail.com,[Duplicate Notice] Re: FW: TW Question in Amarillo,sent,,kimberly.watson@enron.com,<15839506.1075860954472.JavaMail.evans@thyme>,1a1299fcb7b649f5
2026-10-11 06:21:41,r***@gmail.com,[Duplicate Notice] Re: New Power Executive-- Free Sample Issue,sent,,info@pmaconference.com,<5475776.1075860956010.JavaMail.evans@thyme>,1a1299fcc850e82f
2026-10-11 06:21:42,r***@gmail.com,[Duplicate Notice] Re: An Inbound Message For You Has Been Quarantined,sent,,admin.enron@enron.com,<23371658.1075860963842.JavaMail.evans@thyme>,1a1299fcf4613b7c
2026-10-11 06:21:43,r***@gmail.com,[Duplicate Notice] Re: h: Eyeforenergy Briefing,sent,,bruno@eyeforenergy.com,<19691112.1075860955703.JavaMail.evans@thyme>,1a1299fd31a02712
2026-10-11 06:21:44,r***@gmail.com,[Duplicate Notice] Re: <<Concur Expense Document>> - Expenses 02/27/02,sent,,enron_update@concureworkplace.com,<33450125.1075860985605.JavaMail.evans@thyme>,1a1299fd76184db4
```
Result: 5 sent, 0 failed. The database marks exactly those 5 duplicates as notified, and
a rerun would skip them.

**Screenshots** (`screenshots/`):

| File | Shows |
|---|---|
| [`mcp_test_send.png`](screenshots/mcp_test_send.png) | The first test email sent from Claude Code with the MCP tool ("[MCP test] Enron pipeline") |
| [`live_inbox.png`](screenshots/live_inbox.png) | My inbox with the 5 `[Duplicate Notice]` emails from the live run, each starting with "[Test delivery: intended for …]" |
| [`live_notice.png`](screenshots/live_notice.png) | One full notice ("Re: h: Eyeforenergy Briefing", similarity 95.1%), following the template |
| [`show_original.png`](screenshots/show_original.png) | Gmail's "Show original" for that notice, with `References: <19691112.1075860955703.JavaMail.evans@thyme>` (the flagged duplicate's message_id) |
