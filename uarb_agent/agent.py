"""The agent: read a request, fetch the documents, write the reply."""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import Config
from .index import DocumentIndex
from .models import DocumentRequest, FetchResult
from .packager import build_zip
from .reply import (
    compose_failure,
    compose_missing_doc_type,
    compose_missing_matter,
    compose_not_found,
    compose_search_results,
    compose_search_unavailable,
    compose_summary,
    greeting_name,
)
from .request_parser import RequestParser, search_query
from .scraper import MatterNotFound, ScrapeError, UARBScraper

log = logging.getLogger(__name__)


@dataclass
class AgentReply:
    body: str
    attachment: Optional[Path] = None
    request: Optional[DocumentRequest] = None


class RegulatoryAgent:
    def __init__(
        self,
        config: Config,
        scraper: Optional[UARBScraper] = None,
        parser: Optional[RequestParser] = None,
        index: Optional[DocumentIndex] = None,
    ):
        self.config = config
        self.scraper = scraper or UARBScraper(headless=config.headless, demo=config.demo)
        self.parser = parser or RequestParser(use_llm=config.use_llm, model=config.claude_model)
        self.index = index or (DocumentIndex(config.index_path) if config.index_path else None)

    def handle(self, subject: str, body: str, sender_name: str, workdir: Path) -> AgentReply:
        """Answer one request. ``workdir`` holds the downloads and the ZIP until the reply is sent."""
        name = greeting_name(sender_name)
        signature = self.config.agent_name

        query = search_query(subject, body)
        if query:
            return AgentReply(self._search(name, query, signature))

        request = self.parser.parse(subject, body)
        log.info(
            "Request: matter=%s, type=%s (read by %s%s)",
            request.matter,
            request.doc_type,
            request.parsed_by,
            f"; {' and '.join(request.from_thread)} from earlier in the thread" if request.from_thread else "",
        )

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
            return AgentReply(compose_not_found(name, request.matter, signature, request.doc_type), request=request)
        except ScrapeError:
            log.exception("Scraping %s failed", request.matter)
            return AgentReply(compose_failure(name, request.matter, signature, request.doc_type), request=request)

        self._remember(result)
        if request.doc_type is None:
            body = compose_missing_doc_type(name, result.metadata, signature, self.config.max_documents)
            return AgentReply(body, request=request)

        attachment = None
        if result.documents:
            zip_name = f"{request.matter}_{request.doc_type.replace(' ', '_')}.zip"
            attachment = build_zip(result.documents, workdir / zip_name, request.matter, request.doc_type)
        max_mb = self.config.max_attachment_bytes / (1024 * 1024)
        return AgentReply(compose_summary(name, result, signature, max_mb), attachment, request)

    def _remember(self, result: FetchResult) -> None:
        """Add what was fetched to the search index. Never fails the request."""
        if self.index is None:
            return
        try:
            count = self.index.add(result)
            log.info("Indexed %s and %d document(s)", result.metadata.matter, count)
        except (sqlite3.Error, OSError) as e:
            log.warning("Could not update the search index: %s", e)

    def _search(self, name: str, query: str, signature: str) -> str:
        if self.index is None:
            return compose_search_unavailable(name, signature)
        hits = self.index.search(query)
        documents, matters = self.index.stats()
        log.info("Search %r: %d hit(s) in %d documents", query, len(hits), documents)
        return compose_search_results(name, query, hits, documents, matters, signature)
