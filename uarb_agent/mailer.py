"""Read requests over IMAP and send replies over SMTP (standard library only)."""
from __future__ import annotations

import html
import imaplib
import logging
import re
import smtplib
import ssl
from dataclasses import dataclass
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid, parseaddr
from pathlib import Path
from typing import List, Optional

from .config import Config

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 60
SYSTEM_SENDER_RE = re.compile(r"^(mailer-daemon|postmaster)@|no-?reply[^@]*@|do-?not-?reply[^@]*@")


@dataclass
class IncomingEmail:
    uid: bytes
    message: EmailMessage

    @property
    def sender_name(self) -> str:
        return parseaddr(self.message.get("From", ""))[0]

    @property
    def sender_address(self) -> str:
        return parseaddr(self.message.get("From", ""))[1].lower()

    @property
    def reply_address(self) -> str:
        return self.message.get("Reply-To") or self.message.get("From", "")

    @property
    def subject(self) -> str:
        return str(self.message.get("Subject", "")).strip()

    @property
    def text(self) -> str:
        return extract_text(self.message)


def extract_text(message: EmailMessage) -> str:
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    content = part.get_content()
    if part.get_content_type() == "text/html":
        content = re.sub(r"(?is)<(script|style).*?</\1>", "", content)
        content = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", content)
        content = html.unescape(re.sub(r"<[^>]+>", "", content))
    return content


def should_ignore(mail: IncomingEmail, config: Config) -> Optional[str]:
    """Return why a message should not get a reply, or None if it should."""
    sender = mail.sender_address
    headers = mail.message
    if not sender:
        return "no sender"
    if sender == config.agent_email.lower():
        return "sent by the agent itself"
    if config.allowed_senders and sender not in config.allowed_senders:
        return "sender not in ALLOWED_SENDERS"
    if headers.get("Auto-Submitted", "no").lower() != "no":
        return "automatic message"
    if headers.get("Precedence", "").lower() in ("bulk", "junk", "list"):
        return "bulk or list mail"
    if SYSTEM_SENDER_RE.search(sender):
        return "system address"
    return None


def build_reply(
    original: IncomingEmail,
    from_address: str,
    from_name: str,
    body: str,
    attachment: Optional[Path] = None,
) -> EmailMessage:
    reply = EmailMessage()
    reply["From"] = formataddr((from_name, from_address))
    reply["To"] = original.reply_address
    subject = original.subject or "Your UARB document request"
    reply["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    reply["Date"] = formatdate(localtime=True)
    reply["Message-ID"] = make_msgid(domain=from_address.split("@")[-1] or None)
    message_id = original.message.get("Message-ID")
    if message_id:
        reply["In-Reply-To"] = message_id
        reply["References"] = f"{original.message.get('References', '')} {message_id}".strip()
    # RFC 3834: marks this as an automatic reply so other auto-responders don't answer it.
    reply["Auto-Submitted"] = "auto-replied"
    reply.set_content(body)
    if attachment is not None:
        reply.add_attachment(
            attachment.read_bytes(), maintype="application", subtype="zip", filename=attachment.name
        )
    return reply


class Mailbox:
    def __init__(self, config: Config):
        self.config = config

    def _imap(self) -> imaplib.IMAP4_SSL:
        imap = imaplib.IMAP4_SSL(self.config.imap_host, self.config.imap_port, timeout=TIMEOUT_SECONDS)
        imap.login(self.config.imap_user, self.config.imap_password)
        imap.select(self.config.imap_folder)
        return imap

    def unread(self) -> List[IncomingEmail]:
        """Fetch unread messages without marking them read (we mark them once answered)."""
        imap = self._imap()
        try:
            status, data = imap.uid("search", None, "UNSEEN")
            if status != "OK":
                raise RuntimeError(f"IMAP search failed: {data}")
            messages = []
            for uid in data[0].split():
                status, parts = imap.uid("fetch", uid, "(BODY.PEEK[])")
                if status != "OK" or not parts or not isinstance(parts[0], tuple):
                    log.warning("Could not fetch message %s", uid)
                    continue
                messages.append(IncomingEmail(uid, message_from_bytes(parts[0][1], policy=policy.default)))
            return messages
        finally:
            imap.logout()

    def mark_read(self, uid: bytes) -> None:
        imap = self._imap()
        try:
            imap.uid("store", uid, "+FLAGS", "(\\Seen)")
        finally:
            imap.logout()

    def send(self, message: EmailMessage) -> None:
        context = ssl.create_default_context()
        if self.config.smtp_port == 465:
            server = smtplib.SMTP_SSL(
                self.config.smtp_host, self.config.smtp_port, context=context, timeout=TIMEOUT_SECONDS
            )
        else:
            server = smtplib.SMTP(self.config.smtp_host, self.config.smtp_port, timeout=TIMEOUT_SECONDS)
            server.starttls(context=context)
        with server:
            if self.config.smtp_user:
                server.login(self.config.smtp_user, self.config.smtp_password)
            server.send_message(message)
