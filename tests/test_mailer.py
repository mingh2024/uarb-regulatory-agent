from email import message_from_bytes, policy
from email.message import EmailMessage

from uarb_agent.config import Config
from uarb_agent.mailer import IncomingEmail, build_reply, extract_text, should_ignore


def incoming(body="Hi Agent, Can you give me Other Documents files from M12205? Thanks!", html=False, **headers):
    msg = EmailMessage()
    msg["From"] = headers.pop("From", "Jane Doe <jane@example.com>")
    msg["To"] = "agent@example.com"
    msg["Subject"] = headers.pop("Subject", "Documents request")
    msg["Message-ID"] = "<abc@example.com>"
    for key, value in headers.items():
        msg[key.replace("_", "-")] = value
    if html:
        msg.set_content("plain fallback")
        msg.add_alternative(body, subtype="html")
    else:
        msg.set_content(body)
    return IncomingEmail(b"1", message_from_bytes(msg.as_bytes(), policy=policy.default))


def test_reply_threads_and_attaches_zip(tmp_path):
    zip_path = tmp_path / "M12205_Other_Documents.zip"
    zip_path.write_bytes(b"PK\x05\x06" + b"\0" * 18)
    reply = build_reply(incoming(), "agent@example.com", "UARB Agent", "Hi Jane", zip_path)
    assert reply["To"] == "Jane Doe <jane@example.com>"
    assert reply["Subject"] == "Re: Documents request"
    assert reply["In-Reply-To"] == "<abc@example.com>"
    assert reply["Auto-Submitted"] == "auto-replied"
    attachments = list(reply.iter_attachments())
    assert [a.get_filename() for a in attachments] == ["M12205_Other_Documents.zip"]
    assert attachments[0].get_content_type() == "application/zip"
    assert reply.get_body(("plain",)).get_content().strip() == "Hi Jane"


def test_reply_without_attachment_keeps_existing_re_prefix():
    reply = build_reply(incoming(Subject="Re: Documents request"), "agent@example.com", "A", "body")
    assert reply["Subject"] == "Re: Documents request"
    assert not list(reply.iter_attachments())


def test_extract_text_prefers_plain_and_strips_html():
    assert "M12205" in incoming().text
    html_only = EmailMessage()
    html_only.set_content("<p>Exhibits for <b>M12205</b></p><p>Thanks&nbsp;!</p>", subtype="html")
    assert extract_text(html_only).split() == ["Exhibits", "for", "M12205", "Thanks", "!"]


def test_should_ignore_loops_and_strangers():
    config = Config(agent_email="agent@example.com")
    assert should_ignore(incoming(), config) is None
    assert should_ignore(incoming(From="agent@example.com"), config)
    assert should_ignore(incoming(Auto_Submitted="auto-replied"), config)
    assert should_ignore(incoming(From="MAILER-DAEMON@example.com"), config)
    assert should_ignore(incoming(From="Google <googlecommunityteam-noreply@google.com>"), config)
    assert should_ignore(incoming(From="no-reply@accounts.google.com"), config)
    assert should_ignore(incoming(Precedence="bulk"), config)
    locked = Config(agent_email="agent@example.com", allowed_senders=frozenset({"boss@example.com"}))
    assert should_ignore(incoming(), locked) == "sender not in ALLOWED_SENDERS"
