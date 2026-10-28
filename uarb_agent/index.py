"""A local, searchable record of every matter and document the agent has collected.

Each request adds the matter's metadata and its documents to a SQLite database. An FTS5
full-text index covers document titles, matter titles and the text of each PDF, so
"search: capital expenditure" finds filings across every matter fetched so far.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple

from .models import Document, FetchResult

log = logging.getLogger(__name__)
logging.getLogger("pypdf").setLevel(logging.ERROR)  # it warns about every slightly malformed PDF

MAX_TEXT_CHARS = 500_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS matters (
    matter TEXT PRIMARY KEY,
    title TEXT, description TEXT, status TEXT, type TEXT, category TEXT,
    date_received TEXT, date_final TEXT, outcome TEXT,
    counts TEXT,  -- JSON: documents per tab
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    matter TEXT NOT NULL, doc_type TEXT NOT NULL, doc_id TEXT NOT NULL, title TEXT NOT NULL,
    date TEXT, filename TEXT, size INTEGER, sha256 TEXT,
    has_text INTEGER NOT NULL DEFAULT 0,
    indexed_at TEXT,
    UNIQUE (matter, doc_type, doc_id, title)
);
-- rowid = documents.id
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    title, matter_title, body, tokenize = 'porter unicode61'
);
"""


@dataclass
class SearchHit:
    matter: str
    doc_type: str
    doc_id: str
    title: str
    date: str
    snippet: str  # matching passage with [highlighted] terms, or "" if only the titles matched


def extract_text(path: Path) -> str:
    """Plain text of a PDF ("" for other files and for scanned PDFs with no text layer)."""
    if path.suffix.lower() != ".pdf":
        return ""
    from pypdf import PdfReader

    try:
        parts, total = [], 0
        for page in PdfReader(str(path)).pages:
            text = page.extract_text() or ""
            parts.append(text)
            total += len(text)
            if total > MAX_TEXT_CHARS:
                break
    except Exception as e:  # pypdf raises many different errors on damaged files; indexing must go on
        log.warning("Could not read text from %s: %s", path.name, e)
        return ""
    return re.sub(r"\s+", " ", " ".join(parts)).strip()[:MAX_TEXT_CHARS]


class DocumentIndex:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db:
            db.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return closing(sqlite3.connect(self.path))

    def add(self, result: FetchResult) -> int:
        """Record a matter and the documents fetched for it. Returns how many documents were indexed."""
        meta = result.metadata
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        documents = result.documents + result.oversized + result.failed
        with self._connect() as db, db:
            db.execute(
                "INSERT OR REPLACE INTO matters VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (meta.matter, meta.title, meta.description, meta.status, meta.type, meta.category,
                 meta.date_received, meta.date_final, meta.outcome, json.dumps(meta.counts), now),
            )
            for doc in documents:
                self._add_document(db, meta.matter, meta.title, result.doc_type or "", doc, now)
        return len(documents)

    def _add_document(self, db, matter: str, matter_title: str, doc_type: str, doc: Document, now: str) -> None:
        text = extract_text(doc.path) if doc.path else ""
        key = (matter, doc_type, doc.doc_id, doc.title)
        row = db.execute(
            "SELECT id FROM documents WHERE matter = ? AND doc_type = ? AND doc_id = ? AND title = ?", key
        ).fetchone()
        if row:
            doc_pk = row[0]
            if not text:  # e.g. too big to download this time: keep the text we already have
                old = db.execute("SELECT body FROM documents_fts WHERE rowid = ?", (doc_pk,)).fetchone()
                text = old[0] if old else ""
            db.execute(
                "UPDATE documents SET date = ?, filename = ?, size = ?, sha256 = ?, has_text = ?, indexed_at = ? "
                "WHERE id = ?",
                (doc.date, doc.filename, doc.size, doc.sha256, int(bool(text)), now, doc_pk),
            )
            db.execute("DELETE FROM documents_fts WHERE rowid = ?", (doc_pk,))
        else:
            doc_pk = db.execute(
                "INSERT INTO documents (matter, doc_type, doc_id, title, date, filename, size, sha256, has_text, "
                "indexed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                key + (doc.date, doc.filename, doc.size, doc.sha256, int(bool(text)), now),
            ).lastrowid
        db.execute(
            "INSERT INTO documents_fts (rowid, title, matter_title, body) VALUES (?, ?, ?, ?)",
            (doc_pk, doc.title, matter_title, text),
        )

    def search(self, query: str, limit: int = 8) -> List[SearchHit]:
        """Best matches for all the query's words; if nothing has them all, for any of them."""
        words = re.findall(r"\w+", query.lower())
        if not words:
            return []
        with self._connect() as db:
            for joiner in (" ", " OR "):
                # Each word quoted, so user input can't use (or break) FTS5 query syntax.
                fts_query = joiner.join(f'"{w}"' for w in words)
                rows = db.execute(
                    """
                    SELECT d.matter, d.doc_type, d.doc_id, d.title, d.date,
                           snippet(documents_fts, 2, '[', ']', '…', 14)
                    FROM documents_fts JOIN documents d ON d.id = documents_fts.rowid
                    WHERE documents_fts MATCH ?
                    ORDER BY bm25(documents_fts, 5.0, 2.0, 1.0)
                    LIMIT ?
                    """,
                    (fts_query, limit),
                ).fetchall()
                if rows:
                    return [
                        SearchHit(matter, doc_type, doc_id, title, date or "", snippet if "[" in snippet else "")
                        for matter, doc_type, doc_id, title, date, snippet in rows
                    ]
        return []

    def stats(self) -> Tuple[int, int]:
        """(documents, matters) in the index."""
        with self._connect() as db:
            return db.execute("SELECT COUNT(*), COUNT(DISTINCT matter) FROM documents").fetchone()
