# Parse decisions (Task 1)

Decisions made while building the extraction pipeline, with the evidence from the real
data (34,222 files across lay-k, skilling-j, kitchen-l, farmer-d, lokay-m). Guiding rule:
**follow `docs/assignment.md` literally, and document the consequences.**

## Mandatory fields and failures

| # | Decision | Effect on the data |
|---|---|---|
| D1 | An email with no `To` header fails as `MISSING_TO`. **No fallback to `X-To`.** | 1,415 failures (4.1%): broadcasts (`All Enron Worldwide@ENRON`), `undisclosed-recipients`, `(Recipient list suppressed)`, Notes items with empty X-To. This excludes broadcast memos from the database. 81 of them have real addresses in X-To (likely BCC copies) but are still excluded, to follow the spec exactly. |
| — | `From` with no valid address fails as `INVALID_FROM`. | 2 failures: `pep <performance.>`, `<"d@piassick".@enron@enron.com>`. |
| D4 | An empty `Subject` is accepted as `""`. Only a missing Subject header fails. | 732 emails have an empty subject. |
| D5 | An empty primary `body` is accepted as `""` (a pure forward or quote). | 1,794 records. **Task 3 note:** compare body + forwarded_content, or empty bodies would look like duplicates. |
| — | `message_id` keeps its angle brackets (`<…@thyme>`), as needed for the `References:` header in Task 4. | All Message-IDs are unique per file. The same email in two folders has **different** IDs, so the unique constraint won't catch folder copies; Task 3's fuzzy match will. |

Final run: 34,222 found, 32,805 parsed (95.86%), 1,417 failed; parsed + failed = found.

## Dates

| # | Decision |
|---|---|
| — | All dates are normalized to UTC. Numeric offsets win; US abbreviations (PST, EDT, ...) are handled by the stdlib, and others (CET, BST, JST, ...) by a small table. No timezone → assume UTC and log `DATE_NO_TIMEZONE`. |
| D3 | The Lotus Notes null date `Mon, 31 Dec 1979 16:00:00 -0800` (= 1980-01-01 00:00 UTC) is kept as parsed, logged as `DATE_SENTINEL`, and flagged with the extra boolean **`date_is_sentinel`** so Task 3 never picks such a copy as the original. 17 emails. |

## Addresses

| # | Decision |
|---|---|
| D9 | Lotus leading dots (`<.palmer@enron.com>`) and stray quotes (`"'thomas.perkins'"@enron.com`) are stripped; addresses are lowercased and de-duplicated in order. Items with no usable address are dropped and logged as `ADDRESS_REJECTED` (14 warnings in total). |
| — | Each list item is parsed on its own. Python 3.13's strict `getaddresses()` returns nothing for a whole header if any part is malformed, which would silently drop valid recipients. |
| D2 | `Bcc` is stored exactly as given. In this dataset it is identical to `Cc` in every one of the 9,037 parsed emails that have it (a known conversion artifact), and `X-bcc` is almost always empty. Reported in stats as `bcc_identical_to_cc`. |

## Body, forwarded_content, quoted_content

Everything before the first forward/reply marker is the sender's own text (`body`).
Everything from that marker to the end, nested chains included, goes to **one** field:

| Marker found | Goes to |
|---|---|
| `----- Forwarded by …` (Lotus), `Forwarded message`, `Begin forwarded message:` | forwarded_content |
| A raw RFC 822 header block pasted into the text (bounces, Netscape `[Fwd: …]`) | forwarded_content |
| `-----Original Message-----`, Outlook `From:/Sent:` block, Lotus reply header (`To:/cc:/Subject:` with its sender/timestamp attribution) | **D6:** forwarded_content if the subject starts with `FW:`/`FWD:`, otherwise quoted_content |
| `… wrote:` | quoted_content |

**The `>` rule wins.** Lines starting with `>` are always quoted_content, even when the
subject says `FW:`. The spec defines quoted_content as "lines beginning with > or similar
markers", and that literal definition takes priority over the subject-based D6 rule.
Example: `farmer-d/all_documents/1911.` (subject "FW: Updated contact sheet"): the
`> -----Original Message-----` chain goes to quoted_content, not forwarded_content.
267 emails have `>` lines split out this way.

Other rules:
- `>` lines interleaved with the sender's answers are moved to quoted_content; the answers stay in body.
- A message that is **entirely** `>`-quoted (forwarded inline as a quote) has one level of `>` removed and is split again, so its text becomes the body (72 emails).
- An SMTP header block that leaked into the start of the body (Return-Path, Received, ...) is removed; its Content-Type still counts for has_attachment (15 emails).
- Quoted-printable is decoded only when declared (1,483 emails); 252 emails declared 7bit keep `=20` artifacts.

## Optional fields

| # | Decision |
|---|---|
| D7 | `has_attachment` counts only file-level evidence: multipart/mixed or related, `Content-Disposition: attachment`, `<<file.ext>>`, `<< File: name >>` (not when the value is a URL), the Lotus attachment line ` - file.ext`, `Embedded Picture/StdOleLink`. The word "attached" and `[IMAGE]` placeholders don't count. True for 12.66%. |
| D9 | `content_type` stores the media type only (`text/plain`), not the charset. |
| — | X-From/X-To/X-cc/X-bcc/X-Folder/X-Origin are stored raw (unfolded); an empty value becomes NULL. |
| — | **Headings** (the spec doesn't define them): short standalone lines formatted as titles: underlined, ALL CAPS, `Title Case Label:`, `*** banner ***`, or `1. Title Case`. Excluded: salutations, signatures, sign-offs, `[IMAGE]`, URLs, phones, clock times, table rows, embedded header labels. Taken from body + forwarded_content, not quoted text. Found in 6.62% of records. Heuristic: precision is good but not perfect (an all-caps name such as `NATSUKO SEGAWA` can slip through). |

## Logs

- `error_log.txt`: one tab-separated line per failed file (`timestamp_utc, source_file, reason, detail`), rewritten each run.
- `output/parse_warnings.txt`: non-fatal problems in the same format (`DATE_SENTINEL`, `ADDRESS_REJECTED`, `HEADER_REPAIRED`, ...).
- `output/extraction_stats.json`: totals, failure reasons, per-mailbox counts, field completeness, data-quality counters, split-marker counts.
