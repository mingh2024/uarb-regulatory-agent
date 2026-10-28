import pytest

from conftest import write_pdf
from uarb_agent.index import DocumentIndex, extract_text
from uarb_agent.models import Document, FetchResult, MatterMetadata


@pytest.fixture
def index(tmp_path):
    return DocumentIndex(tmp_path / "index" / "uarb.sqlite3")


def fetched(tmp_path, matter, title, doc_type, docs):
    meta = MatterMetadata(matter=matter, title=title, counts={doc_type: len(docs)})
    documents = []
    for i, (doc_id, doc_title, text) in enumerate(docs, start=1):
        path = write_pdf(tmp_path / matter / f"{doc_id}.pdf", text) if text is not None else None
        documents.append(Document(row=i, doc_id=doc_id, title=doc_title, date="06/10/2026", path=path,
                                  filename=f"{doc_id}.pdf"))
    return FetchResult(metadata=meta, doc_type=doc_type, documents=documents)


@pytest.fixture
def filled(index, tmp_path):
    index.add(fetched(tmp_path, "M12205", "Halifax Water - Windsor Street Exchange", "Other Documents", [
        ("102674", "Board Order", "The Board approves the capital expenditures for the project"),
        ("102330", "Semi-Annual Update", "Construction schedule and budget update"),
    ]))
    index.add(fetched(tmp_path, "M12383", "Town of Amherst Boundary Change", "Key Documents", [
        ("98759", "Notice of Application", "Public notice of the boundary application"),
    ]))
    return index


def test_finds_words_in_pdf_text_with_stemming_and_highlights(filled):
    hits = filled.search("capital expenditure approved")
    assert [(h.matter, h.doc_id) for h in hits] == [("M12205", "102674")]
    assert "[capital]" in hits[0].snippet and "[expenditures]" in hits[0].snippet


def test_matter_titles_are_searchable(filled):
    hits = filled.search("Amherst")
    assert [h.doc_id for h in hits] == ["98759"]
    assert hits[0].snippet == ""  # matched the matter title, not the PDF text


def test_falls_back_to_any_word_when_no_document_has_all(filled):
    hits = filled.search("boundary budget")
    assert sorted(h.doc_id for h in hits) == ["102330", "98759"]


def test_query_syntax_cannot_break_the_search(filled):
    assert filled.search('"board" AND (order OR -x) NEAR*') != []
    assert filled.search("?!") == []


def test_stats_and_reindexing_keeps_text_without_duplicates(filled, tmp_path):
    assert filled.stats() == (3, 2)
    # Same document seen again but not downloaded (e.g. too big this time): keep its text.
    again = fetched(tmp_path, "M12205", "Halifax Water - Windsor Street Exchange", "Other Documents",
                    [("102674", "Board Order", None)])
    filled.add(again)
    assert filled.stats() == (3, 2)
    assert filled.search("expenditures")[0].doc_id == "102674"


def test_unreadable_files_are_indexed_by_title(index, tmp_path):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf")
    assert extract_text(broken) == ""
    meta = MatterMetadata(matter="M1", title="T")
    index.add(FetchResult(meta, "Exhibits", [Document(row=1, doc_id="1", title="Rate Schedule", path=broken)]))
    assert [h.title for h in index.search("schedule")] == ["Rate Schedule"]
