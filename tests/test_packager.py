import csv
import io
import zipfile

from uarb_agent.models import Document
from uarb_agent.packager import archive_name, build_zip, duplicates


def test_archive_name_adds_title_to_numbered_files():
    doc = Document(row=1, doc_id="102674", title="Board Order", filename="102674.pdf")
    assert archive_name(doc) == "102674 - Board Order.pdf"


def test_archive_name_keeps_descriptive_names_and_strips_unsafe_characters():
    rec = Document(row=1, doc_id="09/12/2022", title="Monday", filename="M10431 – NS Power 2022 GRA - Monday.mp3")
    assert archive_name(rec) == "M10431 – NS Power 2022 GRA - Monday.mp3"
    doc = Document(row=1, doc_id="1", title='Letter: re "costs"/budget', filename="1.PDF")
    assert archive_name(doc) == "1 - Letter_ re _costs_budget.pdf"


def test_build_zip_contains_every_file_with_unique_names(tmp_path, make_docs):
    docs = make_docs(3)
    docs[1].doc_id, docs[1].title = docs[0].doc_id, docs[0].title  # force a name clash
    docs[1].filename = docs[0].filename
    path = build_zip(docs, tmp_path / "out" / "bundle.zip")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        assert names == ["100000 - Letter 0.pdf", "100000 - Letter 0 (2).pdf", "100002 - Letter 2.pdf", "manifest.csv"]
        assert archive.read(names[0]) == b"x" * 10


def test_manifest_lists_files_and_flags_identical_ones(tmp_path, make_docs):
    docs = make_docs(3)
    docs[0].sha256, docs[1].sha256, docs[2].sha256 = "aaa", "bbb", "aaa"
    path = build_zip(docs, tmp_path / "bundle.zip", "M12205", "Other Documents")
    with zipfile.ZipFile(path) as archive:
        rows = list(csv.DictReader(io.StringIO(archive.read("manifest.csv").decode())))
    assert [r["file"] for r in rows] == ["100000 - Letter 0.pdf", "100001 - Letter 1.pdf", "100002 - Letter 2.pdf"]
    assert rows[0] == {
        "file": "100000 - Letter 0.pdf", "matter": "M12205", "document_type": "Other Documents",
        "doc_id": "100000", "title": "Letter 0", "date": "06/10/2026", "size_bytes": "10", "sha256": "aaa",
        "identical_to": "",
    }
    assert rows[2]["identical_to"] == "100000 - Letter 0.pdf"
    assert duplicates(docs) == {docs[2].row: docs[0]}
