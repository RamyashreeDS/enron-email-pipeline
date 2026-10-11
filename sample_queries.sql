-- Sample queries for the Enron email database (data/enron.db).
-- Run with:  sqlite3 -header -column data/enron.db < sample_queries.sql
--
-- Expected outputs are from the load of 5 mailboxes (lay-k, skilling-j, kitchen-l,
-- farmer-d, lokay-m): 32,805 emails. Dates are UTC text 'YYYY-MM-DD HH:MM:SS'.


-- 1. Emails per sender (top 10)
-- Shows: the from_address column and its index (idx_emails_from_address).
-- Expected: daren.farmer@enron.com first with 2,812 emails, then louise.kitchen@enron.com
-- (1,156), mjones7@txu.com (1,063), rosalee.fleming@enron.com (866) and
-- enron.announcements@enron.com (833). 4,192 distinct senders in total (query 1b).
SELECT from_address, count(*) AS emails
FROM emails
GROUP BY from_address
ORDER BY emails DESC, from_address
LIMIT 10;

-- 1b. Number of distinct senders. Expected: 4192.
SELECT count(DISTINCT from_address) AS distinct_senders FROM emails;


-- 2. Emails in a date range: the week of Enron's bankruptcy filing (2001-12-01 to 2001-12-07)
-- Shows: UTC date text compares correctly as text and uses the date index (idx_emails_date).
-- The upper bound is exclusive ('< 2001-12-08') so every time on 7 December is included.
-- Expected: 244 emails: kitchen-l 137, lay-k 85, lokay-m 15, farmer-d 7 (skilling-j had left).
SELECT substr(source_file, 9, instr(substr(source_file, 9), '/') - 1) AS mailbox,
       count(*) AS emails,
       min(date) AS first_email,
       max(date) AS last_email
FROM emails
WHERE date >= '2001-12-01' AND date < '2001-12-08'
GROUP BY mailbox
ORDER BY emails DESC;


-- 3. Emails with CC recipients
-- Shows: the normalized linking table (email_recipients) answering a question that would
-- need text parsing if CC lists were stored as comma-separated strings.
-- Expected: 9,037 emails have at least one CC recipient (query 3a); the email with the most
-- is "Program Date Change" (2001-10-05) with 171 CC recipients (first row of query 3b).
-- Rows 2 and 3 of 3b (same sender, subject and 102 CC recipients, 5 hours apart) are
-- the kind of re-send that Task 3's duplicate detection is meant to flag.
-- 3a.
SELECT count(DISTINCT email_id) AS emails_with_cc
FROM email_recipients
WHERE recipient_type = 'cc';

-- 3b. The five emails with the most CC recipients.
SELECT e.date, e.from_address, e.subject, count(*) AS cc_recipients
FROM emails e
JOIN email_recipients r ON r.email_id = e.id AND r.recipient_type = 'cc'
GROUP BY e.id
ORDER BY cc_recipients DESC, e.date
LIMIT 5;


-- 4. Who received the most email (To recipients, top 5)
-- Shows: joining the linking table to addresses. Each address is stored once in `addresses`.
-- Expected: daren.farmer@enron.com 4,849; louise.kitchen@enron.com 3,902;
-- michelle.lokay@enron.com 2,545; kenneth.lay@enron.com 2,432; jeff.skilling@enron.com 2,130.
SELECT a.address, count(*) AS emails_received
FROM email_recipients r
JOIN addresses a ON a.id = r.address_id
WHERE r.recipient_type = 'to'
GROUP BY a.id
ORDER BY emails_received DESC
LIMIT 5;


-- 5. Rebuild one email's recipient lists in their original order
-- Shows: the normalized data round-trips; `position` keeps the order of each list.
-- Expected: 7 rows for "Human Resources Committee Meeting" (maildir/lay-k/all_documents/476.):
-- 3 'to' rows (positions 0-2: babbio@bellatlantic.com, lynnj@iname.com, klay@enron.com),
-- 2 'cc' rows (lois.m.rigby@verizon.com, rflemin@enron.com), and 2 'bcc' rows repeating
-- the cc list: in this dataset Bcc is always a copy of Cc (see notes/parse_decisions.md, D2).
SELECT e.subject, r.recipient_type, r.position, a.address
FROM emails e
JOIN email_recipients r ON r.email_id = e.id
JOIN addresses a ON a.id = r.address_id
WHERE e.message_id = '<7807046.1075840213284.JavaMail.evans@thyme>'
ORDER BY CASE r.recipient_type WHEN 'to' THEN 1 WHEN 'cc' THEN 2 ELSE 3 END, r.position;


-- 6. Data-quality overview
-- Expected: 32805 emails, 4153 with attachments, 17 sentinel dates (the Lotus Notes null
-- date, stored as 1980-01-01 00:00:00), 0 duplicates and 0 notifications until Tasks 3 and 4
-- run, 724 empty subjects and 1794 empty primary bodies (pure forwards).
SELECT count(*)                  AS emails,
       sum(has_attachment)       AS with_attachment,
       sum(date_is_sentinel)     AS sentinel_dates,
       sum(is_duplicate)         AS flagged_duplicates,
       sum(notification_sent)    AS notifications_sent,
       sum(subject = '')         AS empty_subject,
       sum(body = '')            AS empty_body
FROM emails;

-- 6b. Emails per year.
-- Expected: 1980 17 (sentinels) | 1998 3 | 1999 563 | 2000 15989 | 2001 12856 | 2002 3377.
SELECT strftime('%Y', date) AS year, count(*) AS emails
FROM emails
GROUP BY year
ORDER BY year;
