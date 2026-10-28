"""Bundle downloaded documents into a ZIP with readable file names and a manifest."""
from __future__ import annotations

import csv
import io
import re
import zipfile
from pathlib import Path
from typing import Dict, List

from .models import Document

MAX_NAME_LENGTH = 120
MANIFEST_NAME = "manifest.csv"
MANIFEST_COLUMNS = [
    "file", "matter", "document_type", "doc_id", "title", "date", "size_bytes", "sha256", "identical_to",
]


def archive_name(doc: Document) -> str:
    """Name a file inside the ZIP.

    Exhibits and documents download as "102674.pdf", so we add the title:
    "102674 - Board Order.pdf". Recordings already come with descriptive names.
    """
    path = Path(doc.filename)
    if doc.doc_id and path.stem == doc.doc_id and doc.title:
        name = f"{doc.doc_id} - {doc.title}"
    else:
        name = path.stem
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")[:MAX_NAME_LENGTH].rstrip(" .")
    return f"{name}{path.suffix.lower()}"


def duplicates(documents: List[Document]) -> Dict[int, Document]:
    """Map each document whose bytes repeat an earlier one (by row) to that earlier document.

    The site sometimes files the same PDF under two document numbers.
    """
    first_by_hash: Dict[str, Document] = {}
    repeats: Dict[int, Document] = {}
    for doc in documents:
        if not doc.sha256:
            continue
        if doc.sha256 in first_by_hash:
            repeats[doc.row] = first_by_hash[doc.sha256]
        else:
            first_by_hash[doc.sha256] = doc
    return repeats


def build_zip(documents: List[Document], zip_path: Path, matter: str = "", doc_type: str = "") -> Path:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    used = {MANIFEST_NAME}
    names: Dict[int, str] = {}
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for doc in documents:
            if doc.path is None:
                continue
            name = archive_name(doc)
            stem, suffix = Path(name).stem, Path(name).suffix
            n = 2
            while name.lower() in used:
                name = f"{stem} ({n}){suffix}"
                n += 1
            used.add(name.lower())
            names[doc.row] = name
            archive.write(doc.path, arcname=name)
        archive.writestr(MANIFEST_NAME, _manifest(documents, names, matter, doc_type))
    return zip_path


def _manifest(documents: List[Document], names: Dict[int, str], matter: str, doc_type: str) -> str:
    repeats = duplicates(documents)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(MANIFEST_COLUMNS)
    for doc in documents:
        if doc.row not in names:
            continue
        original = repeats.get(doc.row)
        writer.writerow([
            names[doc.row], matter, doc_type, doc.doc_id, doc.title, doc.date, doc.size, doc.sha256,
            names.get(original.row, "") if original else "",
        ])
    return out.getvalue()
