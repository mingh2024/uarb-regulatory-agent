# UARB Regulatory Document Agent

An email agent for Senpilot's Co-op Technical Assignment (Winter 2027). You email it a matter number
and a document type. It looks the matter up in the
[Nova Scotia UARB Public Documents Database](https://uarb.novascotia.ca/fmi/webd/UARB15), downloads up
to 10 documents of that type, zips them, and replies with the ZIP plus a summary of the matter.

Beyond the brief, it also:

- **remembers the thread.** Reply "now the exhibits please" and it keeps the matter number from before.
- **builds a searchable library.** Every document it fetches goes into a full-text index. Email
  "search: capital expenditure" to find passages across all the matters collected so far.
- **ships a manifest.** Each ZIP includes `manifest.csv` with each file's details and SHA-256 checksum,
  and flags PDFs the site files twice under different numbers.

> **Request:** "Hi Agent, Can you give me Other Documents files from M12205? Thanks!"
>
> **Reply** (live output, October 27, 2026):
>
> Hi Jane,
>
> M12205 is about the Halifax Regional Water Commission - Windsor Street Exchange Redevelopment
> Project - $69,275,000. It relates to Capital Expenditure Approvals within the Water category. The
> matter had an initial filing on April 7, 2025 and a final filing on October 23, 2025. Its status is
> Closed, with the outcome "Directions given". I found 13 Exhibits, 6 Key Documents, 43 Other Documents,
> and no Transcripts or Recordings. I downloaded 10 out of the 43 Other Documents and am attaching them
> as a ZIP here.
>
> Files in the ZIP (manifest.csv lists each one's details and checksum):
>   - 102674 - Board Order.pdf (July 8, 2026)
>   - 102454 - HRWC (Board) Letter - Reply Submission.pdf (June 22, 2026)
>   - …
>   - 102330 - HRWC (Board) Letter - Semi-Annual Update.pdf (June 10, 2026) – identical to 102329 on the site
>   - …
>
> Request: M12205 · Other Documents
>
> 📎 `M12205_Other_Documents.zip` (10 PDFs + manifest.csv, 10 MB)

(The assignment's example shows 5 Key Documents and 21 Other Documents. The matter has had more
filings since then, and the agent reports what the site shows today.)

## Quick start

Requires Python 3.9+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env   # then fill in the mailbox settings
```

**Try it without email.** This runs the whole pipeline on a request typed at the command line, prints
the reply, and saves the ZIP to `output/`:

```bash
python -m uarb_agent ask "Hi Agent, Can you give me Other Documents files from M12205? Thanks!"
```

**Search what it has collected.** This is the same as emailing "search: …":

```bash
python -m uarb_agent search "capital expenditure approval"
```

**Run it as an email agent.** This polls the inbox every 30 seconds and answers each unread request:

```bash
python -m uarb_agent run          # keep watching
python -m uarb_agent run --once   # answer what's waiting, then exit
```

**Demo mode (for recording).** Add `--demo` to either command, or set `DEMO_MODE=true`. The browser
then opens in a visible window sized to the screen. Each action is slowed by 250 ms, and the matter
page and the document list are held on screen for 1.5 s, so a viewer can follow along:

```bash
python -m uarb_agent ask --demo "Hi Agent, Can you give me Other Documents files from M12205? Thanks!"
python -m uarb_agent run --demo
```

### Mailbox setup (Gmail)

1. Create a Gmail account for the agent and turn on 2-Step Verification.
2. Create an App Password (Google Account → Security → App passwords) and put it in `IMAP_PASSWORD`.
3. IMAP is on by default for new Gmail accounts. If it isn't, enable it under Settings → Forwarding
   and POP/IMAP.

Any IMAP/SMTP provider works (Outlook, Fastmail, etc.). Set the hosts and ports in `.env`.

### Claude (optional)

Set `ANTHROPIC_API_KEY` to have Claude read the requests. Without a key, the agent uses keyword rules,
which handle the assignment's request format and common variants. See "Reading the request" below.

## How it works

```
 inbox ──IMAP──▶ mailer.py ──▶ request_parser.py ──▶ scraper.py ──▶ packager.py ──▶ reply.py ──SMTP──▶ sender
                 unread mail    Claude / rules:       Playwright      ZIP, names,     summary
                 loop guards    matter + doc type,    drives the      manifest.csv    email from
                                thread memory         UARB site                       templates
                                      │                   │
                                      │ "search: …"       ▼
                                      └────────────▶ index.py: SQLite + FTS5 over titles and PDF text
```

| Module | Job |
| --- | --- |
| `uarb_agent/mailer.py` | Reads unread mail over IMAP (without marking it read until answered), builds threaded replies, sends over SMTP. |
| `uarb_agent/request_parser.py` | Extracts the matter number and document type from the email. |
| `uarb_agent/scraper.py` | Drives the UARB site in headless Chromium: search, metadata, tab counts, downloads. |
| `uarb_agent/packager.py` | Zips the files, renaming `102674.pdf` to `102674 - Board Order.pdf`, and adds `manifest.csv`. |
| `uarb_agent/index.py` | Searchable record of every matter and document fetched (SQLite FTS5). |
| `uarb_agent/reply.py` | Writes the reply text. |
| `uarb_agent/agent.py` | Ties one request together and decides which reply to send. |
| `uarb_agent/__main__.py` | CLI and the inbox polling loop. |

### Scraping a FileMaker WebDirect site

The UARB database is a FileMaker WebDirect app. There is no API, the pages are not HTML you can
fetch, and the documents have no stable URLs. So the agent drives a real browser with Playwright.
Things I found while exploring the site that shaped the scraper:

- **The search box isn't an input.** It's a div that turns into a `contenteditable` editor when
  clicked. Typing before the editor appears is silently lost. Clicking Search right after typing races
  the field commit and searches for an empty value. The scraper waits for the editor, types, then presses
  **Tab**. Leaving the field commits the value, and the site runs the search itself on field exit. If
  the search doesn't start, the scraper falls back to clicking Search.
- **Unknown matters** show a "No Records Found" dialog. The agent replies saying the matter wasn't found.
- **Tab counts load lazily.** The numbers in "Exhibits - 13" fill in after the page renders, and other
  tabs' numbers disappear when you switch tabs. All five counts are read up front, before opening a tab.
- **Metadata has no stable selectors.** Element ids encode layout object numbers that differ between
  layouts. The header is read by matching each value box to the nearest column label ("Type / Category",
  "Date Received", …).
- **Tabs use different layouts.** Key and Other Documents rows are *doc no, title, extension, date*.
  Exhibits rows are *title, extension, date, exhibit number (A-1, H-12)*. Transcripts and recordings
  rows are *date, title parts*. Rows are grouped by the portal-row part of the element id
  (`…i3i0r1` = row 3), which is the same on every layout.
- **"GO GET IT" doesn't download directly.** It opens a "Download Files" dialog with a button per file.
  Transcripts and recordings add an "Export Field to File" dialog first, with a pre-filled filename.
- **System dialogs follow the browser's language.** On a Chinese-language Mac, a visible Chromium got
  "下载文件 … 关闭" instead of "Download Files … Close", and the scraper couldn't find them. The browser
  is pinned to `en-US`, so it behaves the same on any machine.
- **Commas break transcript downloads in Chrome.** Transcript names look like
  `NSUARB-M10431-September 12, 2022.pdf`. The server puts that name unquoted in `Content-Disposition`,
  and Chrome rejects the response (`ERR_RESPONSE_HEADERS_MULTIPLE_CONTENT_DISPOSITION`), so clicking
  does nothing. The scraper removes commas from the Save As box before clicking OK.
- **The list is a virtual grid.** It keeps only the rows near the visible area in the page, drops
  them as you scroll, and moves with its own scrollbar element rather than page scrolling. The
  headless window is 2400px tall so about 30 rows fit at once. In shorter windows (like demo mode),
  the scraper downloads the rows that are fully visible, then scrolls the grid half a screen at a time,
  so no row is skipped.
- The site is occasionally slow, so a failed attempt retries in a fresh browser session (2 retries).

One data quirk: some rows share the same PDF under two document numbers. For example, 102329 and
102330 on M12205 are byte-identical, even when downloaded separately. The agent passes along what the
site serves, but compares SHA-256 checksums and points the repeats out in the reply and the manifest.

### Reading the request

Claude (`claude-opus-5-5` at low effort) reads the email and returns JSON constrained to a schema:
a matter number and one of the five document types (or `unknown`). Claude handles wording that rules
would miss, like "the hearing audio for matter 12205" or "key docs on m12205". The output is still
validated: the matter must match `M` + 5 digits and the type must be one of the five.

- The request uses the API's server-side refusal fallback (`fallbacks: "default"`), so if the model
  declines a request, the API retries on a fallback model.
- If Claude is unavailable, errors, or can't find a field, keyword rules fill in. The agent never stops
  because of the model.
- Email text is untrusted input. The model only fills two fields from a fixed set, and the system prompt
  tells it to treat the email as data. That leaves little room for prompt injection to do anything.

### Remembering the thread

Only the new part of an email is read for the request. Quoted history (Gmail's "On … wrote:", `>`
lines, Outlook's "From: / Sent:" block) is split off first. The body outranks the subject, since a
reply's subject is left over from the first email.

If the matter or type is still missing, the agent looks at the quoted history. Every reply it sends
ends with a footer like `Request: M12205 · Other Documents`. When a footer is quoted back, the agent
trusts it and ignores the rest of its own earlier text. That text names every document type in its
counts, plus a sample matter in its examples, which would otherwise be misread as a request. This makes
these follow-ups work:

| Agent said | User replies | Agent fetches |
| --- | --- | --- |
| Here are the Other Documents for M12205 | "Now the exhibits please" | M12205 Exhibits |
| Which type do you want for M12383? | "Key Documents" | M12383 Key Documents |
| I need a matter number for those Exhibits | "M12205" | M12205 Exhibits |
| I couldn't find M99999 (Exhibits) | "Sorry, M12205" | M12205 Exhibits |

### Searching collected documents

After each fetch, `index.py` writes the matter's metadata and its documents to SQLite (`data/` by
default). Titles, the matter title and the text of each PDF (via `pypdf`) go into an FTS5 index with
Porter stemming, so "approval" also finds "approved". Search ranks with BM25, weighting document
titles above matter titles above body text. A query first requires all its words, then falls back to
any of them. Each word is quoted, so user input can't break FTS5 query syntax.

> I searched the 14 documents I've collected so far, from 2 matters, for "capital expenditure approval".
>
> 1. M12205 · Other Documents · 102329 – Board (HRWC) Compliance Filing - Construction Costs (June 10, 2026)
>    "…Director, Engineering & [Capital] Infrastructure Attachments: M12205 Attachment 1 – WSE [Capital] Budget Summary…"
> 4. M12205 · Other Documents · 102674 – Board Order (July 8, 2026)
>    "…In its Decision dated October 23, 2025, the Board [approved] the project with the…"

The index only covers documents the agent has fetched, not the whole UARB database. Scanned PDFs have
no text layer, so they're searchable by title only.

### Writing the reply

The reply comes from templates, not a model. Every number, date and file name in it comes from the
site, so the summary can't drift from what was actually found and attached. The agent also replies
sensibly when a request can't be fulfilled as asked:

| Situation | Reply |
| --- | --- |
| Matter and type found | Summary, counts, ZIP attached, list of files. |
| Fewer than 10 documents | "I downloaded all 6 Key Documents…" |
| No documents of that type | Summary and counts, nothing attached. |
| No document type given | Summary and counts, asks which type to send. |
| No matter number | Explains the format and gives an example request. |
| Matter doesn't exist | Says so and asks them to check the number. |
| Files too big to email | Attaches what fits under `MAX_ATTACHMENT_MB` and lists the rest (recordings are large MP3s). |
| Site down or changed | Apologizes and asks them to try later. Details go to the log. |

### Email safety

- Messages stay unread until the reply is sent, so a crash or SMTP failure means a retry, not a lost
  request.
- To avoid mail loops, the agent ignores its own mail, auto-replies (`Auto-Submitted`), bulk mail and
  `mailer-daemon` / `no-reply` senders. It marks its own replies `Auto-Submitted: auto-replied`
  (RFC 3834).
- `ALLOWED_SENDERS` restricts who can use the agent.
- Replies thread under the request (`In-Reply-To` / `References`).

## Configuration

All settings come from environment variables or `.env`. See [.env.example](.env.example).

| Variable | Default | Meaning |
| --- | --- | --- |
| `IMAP_HOST`, `IMAP_USER`, `IMAP_PASSWORD` | (required) | Inbox the agent watches. |
| `SMTP_HOST` | (required) | Outgoing server. `SMTP_USER`/`SMTP_PASSWORD` default to the IMAP login. |
| `SMTP_PORT` | `465` | 465 uses implicit TLS; other ports use STARTTLS. |
| `ALLOWED_SENDERS` | (anyone) | Comma-separated allowlist. |
| `ANTHROPIC_API_KEY` | (none) | Turns on Claude request parsing. |
| `CLAUDE_MODEL` | `claude-opus-5-5` | Model used to read requests. |
| `MAX_DOCUMENTS` | `10` | Documents per request. |
| `MAX_ATTACHMENT_MB` | `18` | Total file size to attach. Gmail's 25 MB limit applies after base64 encoding. |
| `HEADLESS` | `true` | Set `false` to watch the browser. |
| `DEMO_MODE` | `false` | Visible screen-sized browser, slowed down for recording (same as `--demo`). |
| `INDEX_PATH` | `data/uarb_index.sqlite3` | Search index file. Empty turns search off. |

## Tests

```bash
python -m pytest
```

The 62 unit tests cover:

- request parsing: rules, Claude result handling and fallbacks, and thread memory with Gmail and
  Outlook quoting
- reply wording, including the assignment's example word for word
- ZIP naming and the manifest
- search: stemming, ranking, fallback, and query-syntax safety
- the email headers and loop guards
- the agent's decisions

A fake scraper and a fake Claude client keep them offline and fast.

These were also checked against the live site (October 27, 2026):

| Request | Result |
| --- | --- |
| M12205 Other Documents | 10 of 43 attached, metadata matches the site, about 16 s |
| M12383 Key Documents | "all 4 Key Documents" attached |
| Follow-up "Now the exhibits please" under an M12383 reply | matter taken from the thread: all 6 M12383 Exhibits attached |
| `search "capital expenditure approval"` after 3 requests | 8 ranked hits across 2 matters with highlighted PDF passages |
| M10431 Transcripts | date-first layout, export dialog, comma filenames: all 10 attached, about 28 s |
| M12205 Other Documents, `--demo` | visible 1470×860 window, grid scrolled past row 8, 10 of 43 attached, about 24 s |
| M10431 Recordings | 326 MB and 34 MB MP3s, both reported as too large to email |
| M12205 Transcripts | zero documents, nothing attached |
| "What is in matter 12383?" | summary and counts, asks which type |
| M99999 | "couldn't find matter M99999" |

I also tested the email round trip end to end: a fake inbox holding the assignment's example email,
the live scrape, and a real SMTP send to a local test server. I inspected the delivered message:
threading headers, a 10 MB ZIP attachment, and 14 MB total. The one piece not run here is logging in
to a real IMAP/SMTP provider, since that needs the agent's own mailbox credentials.

## Limitations and next steps

- **One request at a time.** A request takes 15–30 s for PDFs. For volume, run a queue with several
  workers, each with its own browser.
- **Polling, not push.** IMAP IDLE or a Gmail push subscription would reply faster.
- **Big files.** Recordings are often bigger than any email limit (one M10431 hearing day is 326 MB).
  The site doesn't report sizes before download, so the agent downloads each file, then sets aside the
  ones that don't fit. A recordings request can take several minutes this way. Uploading the ZIP to
  object storage and emailing a link would remove the cap.
- **Growing the searchable database.** The index fills only as requests come in. Next steps for the
  Regulatory Agent's research side:
  - a background crawler that walks every matter
  - OCR for scanned PDFs
  - embeddings next to FTS5 for semantic search ("cases where a rate increase was denied")
  - a shared database such as Postgres instead of a local SQLite file
- **Site changes.** The scraper relies on visible text ("Go Directly to Matter", "GO GET IT", tab
  names) and layout geometry rather than generated ids. A redesign would still need updates, and a
  scheduled smoke test against a known matter would catch breakage early.
