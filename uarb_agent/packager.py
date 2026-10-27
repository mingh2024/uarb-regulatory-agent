"""Bundle downloaded documents into a ZIP with readable file names."""
from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import List

from .models import Document

MAX_NAME_LENGTH = 120


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


def build_zip(documents: List[Document], zip_path: Path) -> Path:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    used = set()
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
            archive.write(doc.path, arcname=name)
    return zip_path
