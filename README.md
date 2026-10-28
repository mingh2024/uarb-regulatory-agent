# UARB Regulatory Document Agent

An email agent for the [Nova Scotia UARB Public Documents Database](https://uarb.novascotia.ca/fmi/webd/UARB15).
Email it a matter number and a document type. It looks the matter up, downloads up to 10 documents of
that type, and replies with a ZIP plus a summary of the matter.

> **Request:** "Hi Agent, Can you give me Other Documents files from M12205? Thanks!"
>
> **Reply:**
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
>   - …
>   - 102330 - HRWC (Board) Letter - Semi-Annual Update.pdf (June 10, 2026) – identical to 102329 on the site
>
> Request: M12205 · Other Documents
>
> 📎 `M12205_Other_Documents.zip`

Counts differ from the assignment's example because the matter has had filings since; the agent reports
what the site shows today.

Beyond the brief:

- **Thread memory.** Reply "now the exhibits please" and the agent keeps the matter number from before.
- **Manifest.** Each ZIP includes `manifest.csv` (details and SHA-256 per file) and flags PDFs the site
  stores twice under different numbers.
- **Demo mode.** `--demo` shows the browser full-screen and slows it down for screen recordings.

## Setup

Requires Python 3.9+.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env
```

Give the agent its own mailbox. For Gmail: turn on 2-Step Verification, create an App Password, and put
the address and App Password in `IMAP_USER` / `IMAP_PASSWORD`. Send requests from a different address
(the agent ignores its own mail), and set `ALLOWED_SENDERS` to the addresses allowed to use it.

## Usage

```bash
python -m uarb_agent run           # watch the inbox and answer each unread request
python -m uarb_agent run --once    # answer what's waiting, then exit
python -m uarb_agent ask "Other Documents for M12205"   # no email: print the reply, save the ZIP to output/
```

Add `--demo` to either command for a visible, slowed-down browser.

## How it works

```
inbox ─IMAP─▶ mailer ─▶ request_parser ─▶ scraper ─▶ packager ─▶ reply ─SMTP─▶ sender
              loop      matter + type,    Playwright  ZIP and      templated
              guards    thread memory     on the site manifest     summary
```

| Module | Job |
| --- | --- |
| `mailer.py` | Reads unread mail over IMAP, sends threaded replies over SMTP, filters loops and strangers. |
| `request_parser.py` | Finds the matter number and document type (Claude, with keyword rules as fallback). |
| `scraper.py` | Drives the UARB site in headless Chromium: search, metadata, tab counts, downloads. |
| `packager.py` | Builds the ZIP with readable file names and `manifest.csv`. |
| `reply.py` | Writes the reply from templates. |
| `agent.py` | Handles one request end to end. `__main__.py` is the CLI and polling loop. |

**Scraping.** The site is a FileMaker WebDirect app with no API or stable URLs, so the agent drives a
real browser. The parts that needed care:

- The matter box is a `contenteditable` editor that appears on click. The scraper waits for it, types,
  and presses Tab; the site searches on field exit. Clicking Search too early searches an empty value.
- Element ids change between layouts, so metadata is read by matching values to the nearest column
  label, and table rows by the portal-row part of their ids. Each tab orders its columns differently.
- The document list is a virtual grid with its own scrollbar. The scraper downloads the visible rows,
  then scrolls the grid half a screen at a time until it has 10.
- "GO GET IT" opens a download dialog, not a file. Transcript filenames contain commas, which Chrome
  rejects in `Content-Disposition`, so the scraper strips them in the Save As dialog.
- Dialogs follow the browser's language, so the browser is pinned to `en-US`.
- A failed download is retried once; a failed page load retries the whole fetch in a fresh browser.

**Reading the request.** With `ANTHROPIC_API_KEY` set, Claude (`claude-opus-5-5`, low effort) returns a
schema-constrained matter number and document type, so wording like "the hearing audio for matter 12205"
works. Without a key, or if the call fails, keyword rules take over. The result is validated either way.
Only the new text of an email is read; quoted history is split off first. Each reply ends with a
`Request: M12205 · Other Documents` line, and when a follow-up quotes it back, the agent fills in
whatever the new message leaves out.

**Replying.** Replies are templates filled with what the site returned, so the numbers can't drift from
what was found and attached:

| Situation | Reply |
| --- | --- |
| Matter and type found | Summary, counts, ZIP, file list. |
| No documents of that type | Summary and counts, nothing attached. |
| No document type given | Summary and counts, asks which type. |
| No matter number, or matter not found | Explains the format and asks again. |
| Files over the size limit | Attaches what fits and lists the rest. |
| Site down | Asks the sender to try again later. |

**Email safety.** A request stays unread until its reply is sent, so failures retry instead of
dropping it. The agent ignores its own mail, auto-replies, bulk mail and no-reply senders, and marks
its replies `Auto-Submitted: auto-replied` (RFC 3834).

## Configuration

Set in `.env` (see [.env.example](.env.example)).

| Variable | Default | Meaning |
| --- | --- | --- |
| `IMAP_HOST`, `IMAP_USER`, `IMAP_PASSWORD`, `SMTP_HOST` | required | Mailbox. SMTP login defaults to the IMAP one. |
| `SMTP_PORT` | `465` | 465 uses implicit TLS; other ports use STARTTLS. |
| `ALLOWED_SENDERS` | anyone | Comma-separated allowlist. |
| `POLL_INTERVAL_SECONDS` | `30` | How often to check the inbox. |
| `ANTHROPIC_API_KEY` | none | Turns on Claude request parsing. |
| `MAX_DOCUMENTS` | `10` | Documents per request. |
| `MAX_ATTACHMENT_MB` | `18` | Attachment budget (Gmail's 25 MB limit applies after base64). |
| `DEMO_MODE` | `false` | Same as `--demo`. |

## Tests

```bash
python -m pytest
```

52 offline tests cover request parsing and thread memory, reply wording (including the assignment's
example), ZIP naming and the manifest, email headers and loop guards, and the agent's decisions. A fake
scraper and a fake Claude client keep them fast.

Checked live (October 2026):

| Request | Result |
| --- | --- |
| M12205 Other Documents | 10 of 43 attached, about 16 s |
| M10431 Transcripts | all 10 attached despite comma filenames, about 28 s |
| M10431 Recordings | 326 MB and 34 MB MP3s reported as too large to email |
| M12205 Transcripts | none exist, nothing attached |
| "What is in matter 12383?" | summary and counts, asks which type |
| M99999 | "couldn't find matter M99999" |
| Follow-up "Now the exhibits please" | matter taken from the quoted reply |
| Real Gmail round trip, M12205 Key Documents | threaded reply with all 6 files in 23 s |

## Limitations

- One request at a time, 15–30 s each. Volume would need a queue and several browsers.
- Polling, not push. IMAP IDLE would reply faster.
- Recordings are usually too big for email. Uploading to storage and sending a link would fix that.
- The scraper depends on the site's visible text and layout, so a redesign would need updates.
