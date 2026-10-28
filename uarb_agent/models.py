"""Plain data types shared by the scraper, the reply composer and the agent."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# The five document tabs on a matter page, in the order the site shows them.
DOC_TYPES = ("Exhibits", "Key Documents", "Other Documents", "Transcripts", "Recordings")

SINGULAR = {
    "Exhibits": "Exhibit",
    "Key Documents": "Key Document",
    "Other Documents": "Other Document",
    "Transcripts": "Transcript",
    "Recordings": "Recording",
}


def reference_line(matter: Optional[str], doc_type: Optional[str]) -> str:
    """The "Request: M12205 · Other Documents" footer on every reply.

    When the user answers, their mail client quotes it back, which is how the agent
    remembers what the thread is about (see request_parser.from_thread).
    """
    return f"Request: {matter or 'no matter number yet'} · {doc_type or 'no document type yet'}"


@dataclass
class DocumentRequest:
    """What the user asked for. Either field may be missing if the email was unclear."""

    matter: Optional[str]
    doc_type: Optional[str]
    parsed_by: str = "rules"  # "llm", "rules", or "thread" (read from quoted earlier emails)
    from_thread: Tuple[str, ...] = ()  # fields recovered from earlier emails in the thread


@dataclass
class MatterMetadata:
    matter: str
    title: str = ""
    description: str = ""
    status: str = ""
    type: str = ""
    category: str = ""
    date_received: str = ""  # MM/DD/YYYY, as shown on the site
    date_final: str = ""  # labelled "Date Final Submissions" or "Decision Date"
    outcome: str = ""
    # Document count per tab; None when the site never rendered the number.
    counts: Dict[str, Optional[int]] = field(default_factory=dict)


@dataclass
class Document:
    """One row of a document tab."""

    row: int
    doc_id: str  # the "Doc No" for exhibits/documents, the date for transcripts/recordings
    title: str
    date: str = ""
    path: Optional[Path] = None  # local file once downloaded
    filename: str = ""  # the name the site gave the download
    size: int = 0
    sha256: str = ""

    @property
    def label(self) -> str:
        return f"{self.doc_id} - {self.title}" if self.title else self.doc_id


@dataclass
class FetchResult:
    metadata: MatterMetadata
    doc_type: Optional[str]
    documents: List[Document] = field(default_factory=list)  # downloaded and attachable
    oversized: List[Document] = field(default_factory=list)  # downloaded but over the email size budget
    failed: List[Document] = field(default_factory=list)  # the site would not hand over the file

    @property
    def total(self) -> Optional[int]:
        return self.metadata.counts.get(self.doc_type) if self.doc_type else None
