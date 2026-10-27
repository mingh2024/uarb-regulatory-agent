"""The agent: read a request, fetch the documents, write the reply."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import Config
from .models import DocumentRequest
from .packager import build_zip
from .reply import (
    compose_failure,
    compose_missing_doc_type,
    compose_missing_matter,
    compose_not_found,
    compose_summary,
    greeting_name,
)
from .request_parser import RequestParser
from .scraper import MatterNotFound, ScrapeError, UARBScraper

log = logging.getLogger(__name__)


@dataclass
class AgentReply:
    body: str
    attachment: Optional[Path] = None
    request: Optional[DocumentRequest] = None


class RegulatoryAgent:
    def __init__(self, config: Config, scraper: Optional[UARBScraper] = None, parser: Optional[RequestParser] = None):
        self.config = config
        self.scraper = scraper or UARBScraper(headless=config.headless, demo=config.demo)
        self.parser = parser or RequestParser(use_llm=config.use_llm, model=config.claude_model)

    def handle(self, subject: str, body: str, sender_name: str, workdir: Path) -> AgentReply:
        """Answer one request. ``workdir`` holds the downloads and the ZIP until the reply is sent."""
        name = greeting_name(sender_name)
        signature = self.config.agent_name
        request = self.parser.parse(subject, body)
        log.info("Request: matter=%s, type=%s (read by %s)", request.matter, request.doc_type, request.parsed_by)

        if not request.matter:
            return AgentReply(compose_missing_matter(name, signature, request.doc_type), request=request)

        try:
            result = self.scraper.fetch(
                request.matter,
                request.doc_type,
                workdir / "downloads",
                limit=self.config.max_documents,
                max_total_bytes=self.config.max_attachment_bytes,
            )
        except MatterNotFound:
            return AgentReply(compose_not_found(name, request.matter, signature), request=request)
        except ScrapeError:
            log.exception("Scraping %s failed", request.matter)
            return AgentReply(compose_failure(name, request.matter, signature), request=request)

        if request.doc_type is None:
            body = compose_missing_doc_type(name, result.metadata, signature, self.config.max_documents)
            return AgentReply(body, request=request)

        attachment = None
        if result.documents:
            zip_name = f"{request.matter}_{request.doc_type.replace(' ', '_')}.zip"
            attachment = build_zip(result.documents, workdir / zip_name)
        max_mb = self.config.max_attachment_bytes / (1024 * 1024)
        return AgentReply(compose_summary(name, result, signature, max_mb), attachment, request)
