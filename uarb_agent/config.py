"""Settings, read from environment variables (and a .env file if present)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import FrozenSet, Optional

from dotenv import load_dotenv


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw and raw.strip() else default


@dataclass
class Config:
    # Email (IMAP to read requests, SMTP to reply). Works with Gmail/Outlook app passwords.
    imap_host: str = ""
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = ""
    imap_folder: str = "INBOX"
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    agent_email: str = ""
    agent_name: str = "UARB Regulatory Agent"
    allowed_senders: FrozenSet[str] = field(default_factory=frozenset)
    poll_interval: int = 30

    # Claude, used to read free-form requests. Falls back to keyword rules when disabled.
    use_llm: bool = False
    claude_model: str = "claude-opus-5-5"

    # Scraping and packaging.
    max_documents: int = 10
    max_attachment_bytes: int = 18 * 1024 * 1024
    headless: bool = True
    demo: bool = False  # visible, screen-sized, slowed-down browser for recordings
    output_dir: Path = Path("output")

    @classmethod
    def from_env(cls, env_file: Optional[str] = ".env") -> "Config":
        if env_file:
            load_dotenv(env_file)
        imap_user = os.getenv("IMAP_USER", "")
        imap_password = os.getenv("IMAP_PASSWORD", "")
        has_claude_credentials = bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))
        allowed = os.getenv("ALLOWED_SENDERS", "")
        return cls(
            imap_host=os.getenv("IMAP_HOST", ""),
            imap_port=_int("IMAP_PORT", 993),
            imap_user=imap_user,
            imap_password=imap_password,
            imap_folder=os.getenv("IMAP_FOLDER", "INBOX"),
            smtp_host=os.getenv("SMTP_HOST", ""),
            smtp_port=_int("SMTP_PORT", 465),
            smtp_user=os.getenv("SMTP_USER", imap_user),
            smtp_password=os.getenv("SMTP_PASSWORD", imap_password),
            agent_email=os.getenv("AGENT_EMAIL", imap_user),
            agent_name=os.getenv("AGENT_NAME", "UARB Regulatory Agent"),
            allowed_senders=frozenset(s.strip().lower() for s in allowed.split(",") if s.strip()),
            poll_interval=_int("POLL_INTERVAL_SECONDS", 30),
            use_llm=_bool("USE_LLM", has_claude_credentials),
            claude_model=os.getenv("CLAUDE_MODEL", "claude-opus-5-5"),
            max_documents=_int("MAX_DOCUMENTS", 10),
            max_attachment_bytes=int(float(os.getenv("MAX_ATTACHMENT_MB", "18")) * 1024 * 1024),
            headless=_bool("HEADLESS", True),
            demo=_bool("DEMO_MODE", False),
            output_dir=Path(os.getenv("OUTPUT_DIR", "output")),
        )

    def missing_email_settings(self) -> list:
        required = {
            "IMAP_HOST": self.imap_host,
            "IMAP_USER": self.imap_user,
            "IMAP_PASSWORD": self.imap_password,
            "SMTP_HOST": self.smtp_host,
        }
        return [name for name, value in required.items() if not value]
