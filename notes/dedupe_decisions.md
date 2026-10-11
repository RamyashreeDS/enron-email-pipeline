# Duplicate detection decisions (Task 3)

Decisions for duplicate detection (`docs/assignment.md` Task 3, items 11–15), with the
reason for each. Same guiding rule as the other notes: follow the spec literally, and be
transparent about the consequences.

## The spec's definition
Two or more emails are duplicates when they have:
- the same `from_address`
- the same subject after normalizing Re:/Fwd: prefixes
- bodies that are at least 90% similar (fuzzy match)

In each group the earliest email is the original. All the others get `is_duplicate = 1`
and `duplicate_of = <original's message_id>`. The latest one is the Task 4 notification
target.

## How it runs
1. Bucket every email by (`from_address`, normalized subject). Only emails in the same
   bucket can be duplicates.
2. Within a bucket, go through the emails in order (X2, X4). Each email is compared with
   the original of each existing group (X5). It joins the first group it matches at
   90% or more; otherwise it starts a new group as that group's original.
3. Clear the old flags, write the new ones (X9), write `duplicates_report.csv`, and print
   and save the statistics.

Command: `python -m enron_pipeline.dedupe --db data/enron.db`.

## Decisions

| # | Decision | Reason |
|---|---|---|
| X1 | **Folder copies count as duplicates**, as the spec defines them. The stats are split into two categories: **same timestamp** (every copy in the group has the same date: one email filed in several Lotus folders) and **different timestamps** (likely real re-sends). Each category reports its groups and flagged emails. | Follows the definition literally. Most flags in this dataset are folder copies (the plan estimated about 95%), and the split makes that visible for the README instead of hiding it. |
| X2 | **Ties are broken by the lowest database id**: among copies with the same date, the lowest `emails.id` comes first (the original) and the highest comes last (the notification target). | Simple and deterministic. Ids follow load order, which is the sorted file path. |
| X3 | **Compare bodies; if both bodies are empty, compare `forwarded_content` instead. Extension: if both forwarded parts are empty too, compare `quoted_content`.** | The spec compares body content, and that is kept for normal emails. Two empty bodies would otherwise always be 100% alike, which merged different forwards (15 buckets in the plan's measurement). The extension was added after the first run: with only the forwarded fallback, two replies with no text of their own compared "" with "" and matched at 100%, which created 2 false re-sends (see "Results"). |
| X4 | **A sentinel-dated copy (1980-01-01, `date_is_sentinel = 1`) is never the original.** Real-dated copies come first; sentinel copies come after them. A group of only sentinel copies uses the X2 tie-break. | The 1980 date is Lotus Notes' "unknown date", not a real early date. In this data no group mixes sentinel and real dates, so the rule protects against future data. |
| X5 | **Each email is compared with the group's original**, not with every member and not in a chain. | Every flagged email is then at least 90% like its original, and the report's `similarity_score` has one clear meaning: duplicate vs original. |
| X6 | **`rapidfuzz` `fuzz.ratio`** (MIT licence, v3.14.6), after **collapsing whitespace** (every run of spaces, tabs and newlines becomes one space; case is kept). | A recognized, fast library. `fuzz.ratio` is a normalized edit-based similarity from 0 to 100. Collapsing whitespace stops copies that Lotus and Outlook wrapped differently from looking different. |
| X7 | **The subject is matched exactly after removing leading `Re:` / `Fw:` / `Fwd:` prefixes** (any number of them, any case, including forms like `RE[2]:`). Empty subjects are treated like any other subject. | Literal: the spec only asks to normalize the prefixes, so "Q4 Plan" and "Q4 plan" stay different subjects. |
| X8 | **Comparisons run across all mailboxes.** | The spec sets no mailbox limit. An email received by two people (in two mailboxes) counts as duplicate copies. |
| X9 | **Each run clears `is_duplicate` and `duplicate_of` on every email, then recomputes.** `notification_sent` and `notification_date` are never touched. | The flags always match the current data and rules. The notification history is kept even if an email is no longer flagged. |
| X10 | **Report format:** `similarity_score` is a percentage with one decimal (`95.5`); `subject` is the duplicate's own subject; exactly the 7 spec columns. | Matches the notice template's "<similarity_percentage>%" and the spec's column list. |

## Consequences to document
- **Threshold:** "at least 90%" means a score ≥ 90.0 before rounding. A pair at 89.96 is
  not a match, even though it would display as 90.0.
- **Completely empty emails (X3):** if the body, forwarded and quoted parts are all
  empty on both sides, "" vs "" still scores 100%. This happens in 1 group (3 emails in
  the data have no text at all).
- **Templates:** emails built from the same template can pass 90% while being about
  different things (e.g. robert.cotten's "Deal # 361155 has been created…" vs "Deal #
  362921…", 95.5% in the plan's measurement). That is a limit of the spec's definition.
- **Sentinel order (X4):** because sentinel copies sort after real-dated ones, a sentinel
  copy in a mixed group would be the "latest" and so the notification target. No such
  group exists in this data.

## Results (final run, 32,805 emails)

| | Groups | Emails flagged | Avg group size |
|---|--:|--:|--:|
| **All duplicates** | **7,356** | **14,896** | 3.03 |
| Same timestamp (folder copies of one email) | 6,852 | 13,371 | 2.95 |
| Different timestamps (likely re-sends) | 504 | 1,525 | 4.03 |

- **Most flags are folder copies:** 93.1% of groups and 89.8% of flagged emails are one
  email stored in several Lotus folders (`sent`, `_sent_mail`, `all_documents`,
  `discussion_threads`), each with its own message_id.
- **Re-send groups are mixed:** they also contain folder copies of the original. Of the
  1,525 emails flagged there, 668 have the same timestamp as their original and 857 are
  later copies.
- **Similarity:** 14,506 flagged emails are identical to their original after
  whitespace is collapsed; 390 are 90–99.9% alike; the lowest score is 90.0.
- **Rules used:**

  | Rule | Groups |
  |---|--:|
  | compared on `forwarded_content` (X3) | 417 |
  | compared on `quoted_content` (X3 extension), all same-timestamp folder copies with identical quoted text | 33 |
  | contain sentinel-dated copies (X4) | 7 |
  | span 2+ mailboxes (X8) | 175 |

- **The X3 extension fixed 2 false re-sends.** Before it, these were flagged because
  both emails had no own text and no forwarded part, so "" vs "" scored 100%:
  - j..farmer "Remove": `farmer-d/sent_items/302.` and `398.`, replies to two
    different senders a month apart.
  - michelle.lokay "Job for Kathi?": `lokay-m/sent_items/42.` and `77.`, replies to two
    different messages.

  Totals moved from 7,358 groups / 14,898 flagged to 7,356 / 14,896.
- **Checks:**
  - every one of the 14,896 report rows was re-verified against the database: flag and
    `duplicate_of` set, the original unflagged, same sender and normalized subject,
    order rules, score ≥ 90 and equal to the CSV
  - a rerun gives byte-identical results
  - the notification columns are untouched
- Runtime: about 1.5 s with rapidfuzz (the planning measurement with `difflib` took
  about 4.5 minutes per pass).
