from pathlib import Path

import pytest

from uarb_agent.models import Document, FetchResult, MatterMetadata


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
