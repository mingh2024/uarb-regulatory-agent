from uarb_agent.models import Document, FetchResult
from uarb_agent.reply import (
    compose_missing_matter,
    compose_summary,
    counts_sentence,
    format_date,
    greeting_name,
    matter_summary,
)


def test_summary_matches_the_assignment_example(result):
    body = compose_summary("User", result, "UARB Regulatory Agent", 18)
    assert body.startswith("Hi User,\n\n")
    assert (
        "M12205 is about the Halifax Regional Water Commission - Windsor Street Exchange Redevelopment "
        "Project - $69,270,000. It relates to Capital Expenditure within the Water category. The matter "
        "had an initial filing on April 7, 2025 and a final filing on October 23, 2025."
    ) in body
    assert "I found 13 Exhibits, 5 Key Documents, 21 Other Documents, and no Transcripts or Recordings." in body
    assert "I downloaded 10 out of the 21 Other Documents and am attaching them as a ZIP here." in body
    assert "  - 100000 - Letter 0.pdf (June 10, 2026)" in body


def test_all_documents_downloaded(metadata, make_docs):
    metadata.counts["Key Documents"] = 5
    result = FetchResult(metadata=metadata, doc_type="Key Documents", documents=make_docs(5))
    assert "I downloaded all 5 Key Documents and am attaching them" in compose_summary("A", result, "S", 18)


def test_single_document(metadata, make_docs):
    metadata.counts["Exhibits"] = 1
    result = FetchResult(metadata=metadata, doc_type="Exhibits", documents=make_docs(1))
    assert "I downloaded the only Exhibit and am attaching it" in compose_summary("A", result, "S", 18)


def test_no_documents_of_requested_type(metadata):
    result = FetchResult(metadata=metadata, doc_type="Transcripts")
    body = compose_summary("A", result, "S", 18)
    assert "There are no Transcripts in this matter, so there is nothing to attach." in body
    assert "Files in the ZIP" not in body


def test_oversized_files_are_listed(metadata, make_docs):
    metadata.counts["Recordings"] = 23
    big = [Document(row=i, doc_id="09/12/2022", title=f"Session {i}", date="09/12/2022") for i in range(3)]
    result = FetchResult(metadata=metadata, doc_type="Recordings", oversized=big)
    body = compose_summary("A", result, "S", 18)
    assert "I tried 3 of the 23 Recordings, but none of them could be attached." in body
    assert "too large to fit under the 18 MB email attachment limit" in body
    assert "  - 09/12/2022 - Session 0" in body


def test_partial_attachment_with_failures(metadata, make_docs):
    failed = [Document(row=9, doc_id="999", title="Broken")]
    result = FetchResult(metadata=metadata, doc_type="Other Documents", documents=make_docs(2), failed=failed)
    body = compose_summary("A", result, "S", 18)
    assert "I am attaching 2 out of the 21 Other Documents as a ZIP here." in body
    assert "would not let me download this file:\n  - 999 - Broken" in body


def test_counts_sentence_variants(metadata):
    metadata.counts = {"Exhibits": 1, "Key Documents": 0, "Other Documents": 0, "Transcripts": 0, "Recordings": 0}
    assert counts_sentence(metadata) == "I found 1 Exhibit and no Key Documents, Other Documents, Transcripts, or Recordings."
    metadata.counts = {t: 0 for t in metadata.counts}
    assert counts_sentence(metadata) == "I found no Exhibits, Key Documents, Other Documents, Transcripts, or Recordings."


def test_matter_summary_with_generic_category(metadata):
    metadata.type, metadata.category = "Municipal Boundaries", "Other"
    assert "It falls under the Municipal Boundaries category." in matter_summary(metadata)


def test_matter_summary_without_final_date(metadata):
    metadata.date_final = ""
    assert "initial filing on April 7, 2025; no final filing date is listed yet." in matter_summary(metadata)


def test_helpers():
    assert format_date("04/07/2025") == "April 7, 2025"
    assert format_date("soon") == "soon"
    assert greeting_name("Jane Doe") == "Jane"
    assert greeting_name("") == "there"
    assert greeting_name("jane@example.com") == "there"


def test_missing_matter_reply_explains_format():
    body = compose_missing_matter("A", "S", "Exhibits")
    assert "fetch the Exhibits" in body and "M12205" in body
