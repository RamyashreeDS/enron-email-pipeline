# Mailbox selection

Five mailboxes from the Enron email dataset (`enron_mail_20150507.tar.gz`) were
extracted for this project. Ownership and roles were confirmed from each
mailbox's sent folders (`sent`, `_sent`, `_sent_mail`, `sent_items`): the
`X-From` header counts and the sender's own signatures, ignoring quoted or
forwarded text. Counts were produced by `exploration/sent_identity.py`.

| Mailbox | Owner | Role | Total files | Sent emails |
|---|---|---|--:|--:|
| `lay-k` | Kenneth Lay | Chairman of the Board, Enron | 5,937 | 540 |
| `skilling-j` | Jeff Skilling | President & COO, Enron Corp. (later President & CEO) | 4,139 | 605 |
| `kitchen-l` | Louise Kitchen | Chief Operating Officer, Enron Americas | 5,546 | 1,132 |
| `farmer-d` | Daren J. Farmer | Logistics manager, Texas Logistics team (gas) | 13,032 | 1,715 |
| `lokay-m` | Michelle Lokay | Account Director, Transwestern Commercial Group | 5,568 | 331 |

File counts are raw files. The same message often appears in several folders
(e.g. `all_documents`, `inbox`, `discussion_threads`), so unique emails will
be fewer.

## Evidence per mailbox

### lay-k: Kenneth Lay
- X-From in sent folders: Rosalee Fleming 497, Tori L Wells 28,
  "Lay, Kenneth" 13, "Kenneth Lay" 2.
- Signature: *Kenneth L. Lay · Chairman of the Board · ENRON*.
- Assistants: *Tori L. Wells · Executive Secretary* (also signs as
  *Executive Assistant*). Rosalee Fleming signs as "Rosie" or by name and
  refers to him in the third person ("Ken is very willing to go…",
  "Dr. Lay is unable to participate…").

### skilling-j: Jeff Skilling
- X-From in sent folders: Sherri Sera 380, Joannie Williamson 86,
  Jeff Skilling 70, Sherri Reinartz 33, Katherine Brown 28,
  "Skilling, Jeff" 6.
- Signature: *Jeffrey K. Skilling · President & Chief Operating Officer ·
  Enron Corp.*
- Assistants: *Sherri Sera · Assistant to Jeff Skilling, President & COO*,
  later *Assistant to the President & CEO* (reflecting his promotion);
  *Katherine Brown · Executive Assistant · Office of the President & COO*.

### kitchen-l: Louise Kitchen
- X-From in sent folders: "Kitchen, Louise" on all 1,132 emails
  (`sent_items` only).
- Signature: *Louise Kitchen · Chief Operating Officer · Enron Americas ·
  Tel: 713 853 3488*.

### farmer-d: Daren J. Farmer
- X-From in sent folders: "Daren J Farmer" 1,498, "Farmer, Daren J." 217.
- No title in his signature. Role comes from the rest of the mailbox:
  - The "Logistics Commercial Realignment" announcement of 26 Nov 2001
    (`deleted_items/405.`): *"Darren Farmer will manage the Texas Logistics
    team and will report directly to Tom Martin."*
  - Other emails refer to "Logistics – Pat Clynes/Daren Farmer" and ask him
    to pass items to "your gas schedulers".
  - His largest work folders are `logistics`, `wellhead`, `tufco` and
    `pan_energy_swap`.

### lokay-m: Michelle Lokay
- X-From in sent folders: "Michelle Lokay" 169, "Lokay, Michelle" 161,
  "Michele Lokay" 1.
- Signature: *Michelle Lokay · Account Director · Transwestern Commercial
  Group · 713-345-7932*. Transwestern was Enron's pipeline company.

## Caveats for the pipeline

### Assistants sent most executive mail
For `lay-k` and `skilling-j`, "sent from this mailbox" does not mean
"written by the mailbox owner." About 15 of Lay's 540 sent emails and about
76 of Skilling's 605 carry their own name in X-From; the rest were sent by
their assistants (Rosalee Fleming, Tori Wells, Sherri Sera, Joannie
Williamson, Sherri Reinartz, Katherine Brown). Any analysis of what an
executive personally wrote must filter on X-From, not on the folder.

### Sender names vary
The same person appears under several forms and needs normalizing:
- Display-name order and Exchange format: "Kenneth Lay" vs.
  "Lay, Kenneth </O=ENRON/OU=NA/CN=RECIPIENTS/CN=KLAY>"; likewise for
  Skilling, Farmer and Lokay.
- Misspellings: the original Exchange folder names spell Farmer as
  "Darren", while he signs as "Daren"; one Lokay email has "Michele".
- Nicknames: Rosalee Fleming signs as "Rosie".
