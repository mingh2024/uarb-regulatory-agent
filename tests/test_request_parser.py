import json
from types import SimpleNamespace

import anthropic
import pytest

from uarb_agent.request_parser import RequestParser, clean_email_text, normalize_matter, parse_with_rules


@pytest.mark.parametrize(
    "text, matter, doc_type",
    [
        ("Hi Agent, Can you give me Other Documents files from M12205? Thanks!", "M12205", "Other Documents"),
        ("exhibits for m12383 please", "M12383", "Exhibits"),
        ("Key docs on matter 12205", "M12205", "Key Documents"),
        ("Could you send the hearing audio for M-10431?", "M10431", "Recordings"),
        ("I need the transcript for Matter No. 10431", "M10431", "Transcripts"),
        ("What's in M12205?", "M12205", None),
        ("Please send the exhibits", None, "Exhibits"),
    ],
)
def test_rules(text, matter, doc_type):
    request = parse_with_rules(text)
    assert (request.matter, request.doc_type) == (matter, doc_type)


def test_rules_take_first_mentioned_type():
    assert parse_with_rules("Transcripts (not the exhibits) for M12205").doc_type == "Transcripts"


def test_clean_email_text_drops_quoted_history():
    body = "Recordings for M10431 please\n\nOn Mon, Oct 27, 2026 at 9:00 AM Agent wrote:\n> Exhibits for M12205"
    assert clean_email_text(body) == "Recordings for M10431 please"


def test_normalize_matter():
    assert normalize_matter("m12205") == "M12205"
    assert normalize_matter("12205") == "M12205"
    assert normalize_matter("M1220") is None
    assert normalize_matter("") is None


class FakeMessages:
    def __init__(self, reply=None, error=None, stop_reason="end_turn"):
        self.reply, self.error, self.stop_reason = reply, error, stop_reason
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        block = SimpleNamespace(type="text", text=json.dumps(self.reply))
        return SimpleNamespace(stop_reason=self.stop_reason, content=[block])


def fake_client(**kwargs):
    messages = FakeMessages(**kwargs)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


def test_llm_result_is_used_and_normalized():
    client, messages = fake_client(reply={"matter_number": "m12205", "document_type": "Recordings"})
    request = RequestParser(use_llm=True, client=client).parse("", "the audio from the hearing on 12205")
    assert (request.matter, request.doc_type, request.parsed_by) == ("M12205", "Recordings", "llm")
    call = messages.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"]["format"]["type"] == "json_schema"


def test_llm_unknown_fields_fall_back_to_rules():
    client, _ = fake_client(reply={"matter_number": "", "document_type": "unknown"})
    request = RequestParser(use_llm=True, client=client).parse("Exhibits", "for M12383")
    assert (request.matter, request.doc_type) == ("M12383", "Exhibits")


def test_api_error_falls_back_to_rules():
    error = anthropic.APIConnectionError(request=None)
    client, _ = fake_client(error=error)
    request = RequestParser(use_llm=True, client=client).parse("", "Exhibits for M12205")
    assert (request.matter, request.doc_type, request.parsed_by) == ("M12205", "Exhibits", "rules")


def test_refusal_falls_back_to_rules():
    client, _ = fake_client(reply={}, stop_reason="refusal")
    request = RequestParser(use_llm=True, client=client).parse("", "Exhibits for M12205")
    assert request.parsed_by == "rules"
