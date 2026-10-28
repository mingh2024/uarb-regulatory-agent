"""Browser automation for the UARB Public Documents Database.

The site is a FileMaker WebDirect app: everything is rendered client-side, the
"text boxes" are contenteditable divs, and the document links only exist as
"GO GET IT" buttons that open a download dialog. There is no API or static HTML
to scrape, so we drive a real (headless) Chromium with Playwright.

Element ids look like ``b0p1o299i3i0r1``: ``p1`` is the layout part, ``o299``
the layout object, ``i3`` the portal row and ``r1`` the repetition. Object ids
differ between tabs, so we only rely on the row/prefix structure and otherwise
find things by their text and position.
"""
from __future__ import annotations

import hashlib
import logging
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Set

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from .models import DOC_TYPES, Document, FetchResult, MatterMetadata

log = logging.getLogger(__name__)

UARB_URL = "https://uarb.novascotia.ca/fmi/webd/UARB15"
MATTER_RE = re.compile(r"^M\d{5}$")
TAB_RE = re.compile(r"^(%s)\s*-\s*(\d*)$" % "|".join(DOC_TYPES))

# A tall window makes the document list render ~30 rows up front, so we rarely need to scroll.
VIEWPORT = {"width": 1440, "height": 2400}
# Demo mode: delay before each browser action, and how long to hold key screens.
DEMO_SLOW_MO_MS = 250
DEMO_PAUSE_MS = 1500


class ScrapeError(RuntimeError):
    """The site misbehaved or changed in a way we could not recover from."""


class MatterNotFound(ScrapeError):
    pass


SEARCH_RESULT_JS = """() => {
  if ([...document.querySelectorAll('.v-window')].some(w => /No Records Found/i.test(w.innerText))) return 'not_found';
  if ([...document.querySelectorAll('button')].some(b => /^Exhibits\\b/.test(b.innerText.trim()))) return 'found';
  return null;
}"""

# Reads the matter header by matching each value box to the column label above it.
METADATA_JS = """() => {
  const visible = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const box = e => { const r = e.getBoundingClientRect(); return {x: r.left, y: r.top, b: r.bottom}; };
  const tab = [...document.querySelectorAll('button')].find(b => /^Exhibits\\b/.test(b.innerText.trim()));
  const tabTop = tab ? tab.getBoundingClientRect().top : Infinity;
  const columns = {matter: /^Matter No/, title: /^Title/, type: /^Type/, received: /^Date Received/,
                   final: /^(Date Final|Decision Date)/, outcome: /^Outcome/};
  const labels = {};
  for (const el of document.querySelectorAll('.v-label')) {
    if (!visible(el)) continue;
    const text = el.innerText.trim();
    for (const [key, re] of Object.entries(columns)) {
      if (re.test(text) && !(key in labels)) labels[key] = {el, ...box(el)};
    }
  }
  if (!labels.matter) return null;
  const top = Math.max(...Object.values(labels).map(l => l.b)) - 2;
  const valueCols = ['matter', 'type', 'received', 'final', 'outcome'].filter(k => labels[k]);
  const fields = {};
  for (const el of document.querySelectorAll('.fm-textarea')) {
    if (!visible(el)) continue;
    const r = box(el);
    if (r.y < top || r.y >= tabTop) continue;
    const key = valueCols.reduce((best, k) =>
      Math.abs(labels[k].x - r.x) < Math.abs(labels[best].x - r.x) ? k : best, valueCols[0]);
    (fields[key] = fields[key] || []).push({y: r.y, text: el.innerText.trim()});
  }
  for (const k in fields) fields[k] = fields[k].sort((a, b) => a.y - b.y).map(f => f.text);
  let title = '';
  if (labels.title) {
    const own = new Set(Object.values(labels).map(l => l.el));
    title = [...document.querySelectorAll('.v-label')]
      .filter(el => visible(el) && !own.has(el))
      .filter(el => { const r = box(el); return r.y >= top && r.y < tabTop && Math.abs(r.x - labels.title.x) < 60; })
      .map(el => el.innerText.trim()).filter(Boolean).join('\\n');
  }
  return {fields, title};
}"""

# One entry per document row that has a GO GET IT button, with the row's field texts in reading order.
# "visible" is whether the button sits inside the list's visible area (the grid also renders a few
# rows above and below it).
ROWS_JS = """() => {
  const out = [];
  for (const btn of document.querySelectorAll('button')) {
    if (!/GO GET IT/i.test(btn.innerText)) continue;
    const m = /^(b\\d+p\\d+)o\\d+i(\\d+)i0r\\d+$/.exec(btn.id);
    if (!m) continue;
    const [, prefix, row] = m;
    const sameRow = new RegExp('^' + prefix + 'o\\\\d+i' + row + 'i0r\\\\d+$');
    const fields = [...document.querySelectorAll('.fm-textarea[id^="' + prefix + 'o"]')]
      .filter(el => sameRow.test(el.id))
      .map(el => { const r = el.getBoundingClientRect(); return {text: el.innerText.trim(), x: r.left, y: r.top}; })
      .filter(f => f.text)
      .sort((a, b) => (a.y - b.y) || (a.x - b.x));
    const area = btn.closest('.v-grid') && btn.closest('.v-grid').querySelector('.v-grid-tablewrapper');
    const b = btn.getBoundingClientRect();
    const a = area ? area.getBoundingClientRect() : {top: -Infinity, bottom: Infinity};
    const visible = b.height > 0 && b.top >= a.top - 1 && b.bottom <= a.bottom + 1;
    out.push({row: Number(row), buttonId: btn.id, visible, fields: fields.map(f => f.text)});
  }
  return out.sort((a, b) => a.row - b.row);
}"""

# The document list is a Vaadin grid with its own scrollbar element; moving it renders the next rows.
# Half a screen per step, so every row passes through the visible area.
SCROLL_GRID_JS = """() => {
  const scroller = document.querySelector('.v-grid-scroller-vertical');
  if (!scroller) return false;
  const before = scroller.scrollTop;
  scroller.scrollTop = before + Math.max(60, scroller.clientHeight / 2);
  return scroller.scrollTop > before;
}"""


class UARBScraper:
    def __init__(
        self,
        headless: bool = True,
        timeout_ms: int = 60_000,
        download_timeout_ms: int = 60_000,
        retries: int = 2,
        demo: bool = False,
    ):
        # download_timeout_ms bounds how long a download takes to *start*; finishing a big file is not timed.
        # demo shows the browser filling the screen and slows it down enough to follow on a recording.
        self.demo = demo
        self.headless = headless and not demo
        self.timeout_ms = timeout_ms
        self.download_timeout_ms = download_timeout_ms
        self.retries = retries

    def fetch(
        self,
        matter: str,
        doc_type: Optional[str],
        dest_dir: Path,
        limit: int = 10,
        max_total_bytes: Optional[int] = None,
    ) -> FetchResult:
        """Look up a matter and download up to ``limit`` documents of ``doc_type``.

        With ``doc_type=None`` only the matter metadata and counts are read.
        Files that would push the total past ``max_total_bytes`` are discarded
        and reported in ``FetchResult.oversized``.
        """
        matter = matter.strip().upper()
        if not MATTER_RE.match(matter):
            raise ValueError(f"Matter numbers look like M12205, got {matter!r}")
        if doc_type is not None and doc_type not in DOC_TYPES:
            raise ValueError(f"Unknown document type {doc_type!r}")

        last_error: Optional[Exception] = None
        for attempt in range(1, self.retries + 2):
            if dest_dir.exists():
                shutil.rmtree(dest_dir)
            dest_dir.mkdir(parents=True)
            try:
                return self._fetch_once(matter, doc_type, dest_dir, limit, max_total_bytes)
            except MatterNotFound:
                raise
            except (PlaywrightError, ScrapeError) as e:
                last_error = e
                log.warning("Attempt %d to fetch %s failed: %s", attempt, matter, e)
        raise ScrapeError(f"Could not read {matter} from the UARB site: {last_error}") from last_error

    def _fetch_once(
        self, matter: str, doc_type: Optional[str], dest_dir: Path, limit: int, max_total_bytes: Optional[int]
    ) -> FetchResult:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless, slow_mo=DEMO_SLOW_MO_MS if self.demo else 0)
            try:
                # WebDirect translates its own dialogs ("Download Files", "No Records Found", ...) into
                # the browser's language, and we find them by their English text, so pin English.
                if self.demo:
                    # No fixed viewport: the page takes the size of the window, which we fit to the screen.
                    context = browser.new_context(no_viewport=True, accept_downloads=True, locale="en-US")
                    page = context.new_page()
                    _fit_window_to_screen(page)
                else:
                    context = browser.new_context(viewport=VIEWPORT, accept_downloads=True, locale="en-US")
                    page = context.new_page()
                page.set_default_timeout(self.timeout_ms)
                self._open_matter(page, matter)
                self._pause(page)
                metadata = self._read_metadata(page, matter)
                result = FetchResult(metadata=metadata, doc_type=doc_type)
                if doc_type and metadata.counts.get(doc_type) != 0:
                    self._download_documents(page, result, dest_dir, limit, max_total_bytes)
                return result
            finally:
                browser.close()

    def _pause(self, page: Page) -> None:
        """Hold the current screen for a moment in demo mode so viewers can read it."""
        if self.demo:
            page.wait_for_timeout(DEMO_PAUSE_MS)

    # -- search -----------------------------------------------------------

    def _open_matter(self, page: Page, matter: str) -> None:
        log.info("Opening %s", matter)
        page.goto(UARB_URL, wait_until="domcontentloaded")
        page.get_by_text("Go Directly to Matter").wait_for()
        matter_box = page.locator(".fm-textarea", has=page.locator(".placeholder", has_text="M01234"))
        box_position = matter_box.bounding_box()
        matter_box.click()
        # WebDirect swaps in a contenteditable editor on click; typing before it exists is lost.
        page.wait_for_function("() => document.activeElement && document.activeElement.isContentEditable")
        page.keyboard.type(matter, delay=40)
        # Leaving the field commits the value, and the site runs its search on field exit.
        page.keyboard.press("Tab")
        outcome = self._wait_for_search(page, 15_000)
        if outcome is None:
            log.info("No search result yet; clicking Search")
            self._click_search_button_near(page, box_position)
            outcome = self._wait_for_search(page, self.timeout_ms)
        if outcome == "not_found":
            raise MatterNotFound(f"{matter} was not found on the UARB site")
        if outcome != "found":
            raise ScrapeError(f"Search for {matter} never returned a result")

    def _wait_for_search(self, page: Page, timeout_ms: int) -> Optional[str]:
        try:
            return page.wait_for_function(SEARCH_RESULT_JS, timeout=timeout_ms).json_value()
        except PlaywrightTimeout:
            return None

    def _click_search_button_near(self, page: Page, box_position) -> None:
        """Click the Search button on the same row as the matter box, if it is still on screen.

        If it isn't, the page is most likely already moving to the result, so we just keep waiting.
        """
        buttons = page.locator("button", has_text=re.compile(r"^\s*Search\s*$"))
        best, best_distance = None, float("inf")
        for i in range(buttons.count()):
            position = buttons.nth(i).bounding_box()
            if position and box_position:
                distance = abs(position["y"] - box_position["y"])
                if distance < best_distance:
                    best, best_distance = buttons.nth(i), distance
        if best is None:
            log.info("Search button not found; waiting for the result instead")
            return
        best.click()

    # -- metadata ---------------------------------------------------------

    def _read_metadata(self, page: Page, matter: str) -> MatterMetadata:
        counts = self._read_counts(page)
        data = page.evaluate(METADATA_JS)
        if not data:
            raise ScrapeError("Matter page loaded without its header")
        fields: Dict[str, List[str]] = data["fields"]

        def value(column: str, index: int) -> str:
            values = fields.get(column, [])
            return values[index] if len(values) > index else ""

        lines = [line.strip() for line in data["title"].splitlines() if line.strip()]
        title = lines[0] if lines else ""
        description = " ".join(lines[1:])
        if description == title:
            description = ""
        return MatterMetadata(
            matter=matter,
            title=title,
            description=description,
            status=value("matter", 1),
            type=value("type", 0),
            category=value("type", 1),
            date_received=value("received", 0),
            date_final=value("final", 0),
            outcome=value("outcome", 0),
            counts=counts,
        )

    def _read_counts(self, page: Page) -> Dict[str, Optional[int]]:
        """Read "Exhibits - 13" style tab labels. The numbers fill in a moment after the page loads."""
        counts: Dict[str, Optional[int]] = {}
        for _ in range(30):
            counts = {doc_type: None for doc_type in DOC_TYPES}
            for text in page.locator("button").all_inner_texts():
                match = TAB_RE.match(text.strip())
                if match and match.group(2):
                    counts[match.group(1)] = int(match.group(2))
            if all(c is not None for c in counts.values()):
                break
            page.wait_for_timeout(500)
        return counts

    # -- documents --------------------------------------------------------

    def _download_documents(
        self, page: Page, result: FetchResult, dest_dir: Path, limit: int, max_total_bytes: Optional[int]
    ) -> None:
        doc_type = result.doc_type
        log.info("Opening the %s tab", doc_type)
        page.locator("button", has_text=re.compile(r"^\s*%s\s*-" % re.escape(doc_type))).first.click()
        try:
            page.locator("button", has_text="GO GET IT").first.wait_for()
        except PlaywrightTimeout:
            raise ScrapeError(f"The {doc_type} tab did not list any downloadable documents")
        self._pause(page)

        # The list is a virtual grid that only keeps a few rows in the page and drops them as it
        # scrolls, so download what is rendered (in order), then scroll for the next batch.
        seen: Set[int] = set()
        used = 0
        while len(seen) < limit:
            rows = self._unseen_rows(page, seen)
            if not rows:
                break
            for row in rows:
                if len(seen) >= limit:
                    break
                seen.add(row["row"])
                doc = _describe_row(row)
                path = self._download_row_with_retry(page, row["buttonId"], dest_dir / f"{len(seen):02d}", doc)
                if path is None:
                    result.failed.append(doc)
                    continue
                doc.path, doc.filename, doc.size = path, path.name, path.stat().st_size
                doc.sha256 = _sha256(path)
                if max_total_bytes is not None and used + doc.size > max_total_bytes:
                    log.info("%s (%d bytes) does not fit in the attachment budget", doc.label, doc.size)
                    path.unlink()
                    doc.path = None
                    result.oversized.append(doc)
                    continue
                used += doc.size
                result.documents.append(doc)
                log.info("Downloaded %s (%d bytes)", doc.filename, doc.size)

    def _unseen_rows(self, page: Page, seen: Set[int]) -> List[dict]:
        """Visible rows not handled yet, scrolling the grid down to reveal more when needed."""

        def visible_unseen() -> List[dict]:
            return [row for row in page.evaluate(ROWS_JS) if row["visible"] and row["row"] not in seen]

        rows = visible_unseen()
        for _ in range(15):
            if rows:
                return rows
            if not page.evaluate(SCROLL_GRID_JS):
                return []  # already at the bottom
            # The grid fetches the newly revealed rows from the server; give it a moment.
            for _ in range(10):
                page.wait_for_timeout(500)
                rows = visible_unseen()
                if rows:
                    break
        return rows

    def _download_row_with_retry(self, page: Page, button_id: str, dest_dir: Path, doc: Document) -> Optional[Path]:
        """Download one row, trying a second time if the site drops it. Returns None if both fail."""
        for attempt in (1, 2):
            try:
                return self._download_row(page, button_id, dest_dir)
            except (PlaywrightError, ScrapeError) as e:
                log.warning("Could not download %s (attempt %d): %s", doc.label, attempt, e)
                self._close_dialogs(page)
        return None

    def _download_row(self, page: Page, button_id: str, dest_dir: Path) -> Path:
        page.locator(f"#{button_id}").click()
        dialogs = page.locator(".v-window")
        dialogs.filter(has_text=re.compile("Download Files|Export Field to File")).first.wait_for()
        # Transcripts and recordings first ask for a filename (pre-filled, e.g.
        # "NSUARB-M10431-September 12, 2022.pdf"). Chrome refuses downloads whose
        # Content-Disposition filename has a comma (ERR_RESPONSE_HEADERS_MULTIPLE_CONTENT_DISPOSITION),
        # so strip commas and semicolons before accepting.
        export = dialogs.filter(has_text="Export Field to File")
        if export.count():
            name_box = export.locator("input")
            name = name_box.input_value()
            clean = re.sub(r"\s*[,;]\s*", " ", name).strip()
            if clean and clean != name:
                name_box.fill(clean)
            export.locator(".v-button", has_text=re.compile(r"^\s*OK\s*$")).click()
        download_dialog = dialogs.filter(has_text="Download Files")
        download_dialog.wait_for()
        buttons = download_dialog.locator(".fm-download-button")
        try:
            # On a slow server the dialog appears a moment before its file button.
            buttons.first.wait_for(timeout=15_000)
        except PlaywrightTimeout:
            raise ScrapeError("Download dialog had no files")
        # One GO GET IT normally yields one file; keep only the first if the site ever offers more.
        with page.expect_download(timeout=self.download_timeout_ms) as info:
            buttons.first.click()
        download = info.value
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / _safe_filename(download.suggested_filename)
        download.save_as(path)
        self._close_dialogs(page)
        return path

    def _close_dialogs(self, page: Page) -> None:
        for _ in range(3):
            dialogs = page.locator(".v-window")
            if dialogs.count() == 0:
                return
            button = dialogs.first.locator(".v-button", has_text=re.compile(r"^\s*(Close|Cancel|OK)\s*$"))
            try:
                if button.count():
                    button.first.click()
                else:
                    page.keyboard.press("Escape")
                dialogs.first.wait_for(state="detached", timeout=5_000)
            except PlaywrightError:
                pass


EXHIBIT_NO_RE = re.compile(r"[A-Z]{1,4}-\d+(?:\.\d+)?")


def _describe_row(row: dict) -> Document:
    """Turn a row's field texts into a Document.

    Key / Other Documents rows hold: doc number, title, extension, date, security.
    Exhibits rows hold: title, extension, date, exhibit number (A-1, H-12), security.
    Transcripts / Recordings rows hold: date, title parts, (format), security.
    """
    fields = row["fields"]
    # The exhibit number comes after the title, so take the last match ("RIR-1" can be a title).
    exhibit_nos = [i for i, text in enumerate(fields) if EXHIBIT_NO_RE.fullmatch(text)]
    exhibit_at = exhibit_nos[-1] if exhibit_nos else -1
    doc_no = date = ""
    title_parts: List[str] = []
    for i, text in enumerate(fields):
        if i == exhibit_at:
            continue
        if not doc_no and re.fullmatch(r"\d{3,}", text):
            doc_no = text
        elif not date and re.fullmatch(r"\d{2}/\d{2}/\d{4}", text):
            date = text
        elif re.fullmatch(r"\.\w{1,5}", text) or text.lower() in ("pdf", "public", "confidential"):
            continue
        elif text not in title_parts:
            title_parts.append(text)
    exhibit_no = fields[exhibit_at] if exhibit_at >= 0 else ""
    if doc_no and exhibit_no:
        title_parts.insert(0, exhibit_no)
    return Document(row=row["row"], doc_id=doc_no or exhibit_no or date, title=" - ".join(title_parts), date=date)


def _fit_window_to_screen(page: Page) -> None:
    """Resize the visible browser window to the screen's usable area (demo mode, Chromium only)."""
    try:
        cdp = page.context.new_cdp_session(page)
        window_id = cdp.send("Browser.getWindowForTarget")["windowId"]
        bounds = page.evaluate(
            "() => ({left: screen.availLeft, top: screen.availTop,"
            " width: screen.availWidth, height: screen.availHeight})"
        )
        cdp.send("Browser.setWindowBounds", {"windowId": window_id, "bounds": bounds})
        log.info("Demo window sized to %dx%d", bounds["width"], bounds["height"])
    except PlaywrightError as e:
        log.warning("Could not resize the demo window: %s", e)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", name).strip(" .")
    return name or "document"
