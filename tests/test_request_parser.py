import json
from types import SimpleNamespace

import anthropic
import pytest

from uarb_agent.request_parser import (
    RequestParser,
    normalize_matter,
    parse_with_rules,
    split_email,
)


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


def test_split_email_drops_quoted_history():
    body = "Recordings for M10431 please\n\nOn Mon, Oct 27, 2026 at 9:00 AM Agent wrote:\n> Exhibits for M12205"
    new, quoted = split_email(body)
    assert new == "Recordings for M10431 please"
    assert quoted.endswith("Exhibits for M12205")


AGENT_REPLY_GMAIL = """Now the exhibits please

On Mon, Oct 27, 2026 at 10:00 AM UARB Regulatory Agent <
agent@example.com> wrote:
> Hi Jane,
>
> M12205 is about ... I found 13 Exhibits, 6 Key Documents, 43 Other Documents.
>
> Best,
> UARB Regulatory Agent
>
> Request: M12205 · Other Documents
"""

AGENT_REPLY_OUTLOOK = """M12383 please

From: UARB Regulatory Agent <agent@example.com>
Sent: Monday, October 27, 2026 10:00 AM
Subject: RE: Request

I'd be glad to fetch the Exhibits, but I couldn't find a matter number in your email.
For example: “Can you give me Other Documents files from M12205?”

Request: no matter number yet · Exhibits
"""


def test_split_email_separates_new_text_from_history():
    new, quoted = split_email(AGENT_REPLY_GMAIL)
    assert new == "Now the exhibits please"
    assert quoted.splitlines()[-1] == "Request: M12205 · Other Documents"
    new, quoted = split_email(AGENT_REPLY_OUTLOOK)
    assert new == "M12383 please"
    assert quoted.startswith("From: UARB Regulatory Agent")


def test_follow_up_takes_missing_matter_from_the_thread():
    request = RequestParser().parse("Re: Documents please", AGENT_REPLY_GMAIL)
    assert (request.matter, request.doc_type, request.from_thread) == ("M12205", "Exhibits", ("matter",))


def test_follow_up_takes_missing_type_but_not_the_agents_example_matter():
    request = RequestParser().parse("RE: Request", AGENT_REPLY_OUTLOOK)
    assert (request.matter, request.doc_type, request.from_thread) == ("M12383", "Exhibits", ("doc_type",))
    unanswered = "thanks!\n\n> For example: Other Documents files from M12205\n> " \
                 "Request: no matter number yet · no document type yet"
    request = RequestParser().parse("Re: Request", unanswered)
    assert (request.matter, request.doc_type) == (None, None)


def test_quoted_history_without_agent_footer_is_read_like_a_request():
    body = "Same as before but key docs\n\n> Can I get the exhibits for M12205?"
    request = RequestParser().parse("", body)
    assert (request.matter, request.doc_type, request.from_thread) == ("M12205", "Key Documents", ("matter",))


@pytest.mark.parametrize(
    "footer, answer, matter, doc_type",
    [
        ("Request: M12205 · Other Documents", "Now the exhibits please", "M12205", "Exhibits"),
        ("Request: M12383 · no document type yet", "Key Documents", "M12383", "Key Documents"),
        ("Request: no matter number yet · Exhibits", "M12205", "M12205", "Exhibits"),
        ("Request: M99999 · Exhibits", "Sorry, M12205", "M12205", "Exhibits"),
    ],
)
def test_readme_follow_up_table(footer, answer, matter, doc_type):
    body = f"{answer}\n\nOn Mon, Oct 27, 2026 at 10:00 AM Agent wrote:\n> Hi,\n> ...\n> {footer}"
    request = RequestParser().parse("Re: request", body)
    assert (request.matter, request.doc_type) == (matter, doc_type)


def test_body_wins_over_a_subject_left_over_from_the_thread():
    request = RequestParser().parse("Re: key docs for M12383", "Now the exhibits please")
    assert (request.matter, request.doc_type) == ("M12383", "Exhibits")


def test_new_text_wins_over_the_thread():
    request = RequestParser().parse("", "Transcripts for M10431\n\n> Request: M12205 · Exhibits")
    assert (request.matter, request.doc_type, request.from_thread) == ("M10431", "Transcripts", ())


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
