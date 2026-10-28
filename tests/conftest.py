from pathlib import Path

import pytest

from uarb_agent.models import Document, FetchResult, MatterMetadata


def write_pdf(path: Path, text: str) -> Path:
    """A minimal one-page PDF with a real text layer."""
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(out)
    return path


@pytest.fixture
def metadata():
    return MatterMetadata(
        matter="M12205",
        title="Halifax Regional Water Commission - Windsor Street Exchange Redevelopment Project - $69,270,000",
        status="Closed",
        type="Water",
        category="Capital Expenditure",
        date_received="04/07/2025",
        date_final="10/23/2025",
        outcome="Directions given",
        counts={"Exhibits": 13, "Key Documents": 5, "Other Documents": 21, "Transcripts": 0, "Recordings": 0},
    )


@pytest.fixture
def make_docs(tmp_path: Path):
    def make(n, sizes=None):
        docs = []
        for i in range(n):
            doc_id = str(100000 + i)
            path = tmp_path / f"{i:02d}" / f"{doc_id}.pdf"
            path.parent.mkdir(parents=True, exist_ok=True)
            size = sizes[i] if sizes else 10
            path.write_bytes(b"x" * size)
            docs.append(
                Document(row=i + 1, doc_id=doc_id, title=f"Letter {i}", date="06/10/2026",
                         path=path, filename=path.name, size=size)
            )
        return docs

    return make


@pytest.fixture
def result(metadata, make_docs):
    return FetchResult(metadata=metadata, doc_type="Other Documents", documents=make_docs(10))
