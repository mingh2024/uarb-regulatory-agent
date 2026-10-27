import zipfile

from uarb_agent.agent import RegulatoryAgent
from uarb_agent.config import Config
from uarb_agent.models import FetchResult
from uarb_agent.request_parser import RequestParser
from uarb_agent.scraper import MatterNotFound, ScrapeError, _describe_row


class FakeScraper:
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error
        self.calls = []

    def fetch(self, matter, doc_type, dest_dir, limit, max_total_bytes):
        self.calls.append((matter, doc_type, limit, max_total_bytes))
        if self.error:
            raise self.error
        self.result.doc_type = doc_type
        return self.result


def make_agent(scraper):
    return RegulatoryAgent(Config(max_documents=10), scraper=scraper, parser=RequestParser(use_llm=False))


def test_happy_path_returns_zip(tmp_path, result):
    scraper = FakeScraper(result)
    reply = make_agent(scraper).handle("", "Other Documents for M12205", "Jane Doe", tmp_path)
    assert scraper.calls == [("M12205", "Other Documents", 10, 18 * 1024 * 1024)]
    assert reply.attachment.name == "M12205_Other_Documents.zip"
    assert len(zipfile.ZipFile(reply.attachment).namelist()) == 10
    assert reply.body.startswith("Hi Jane,")


def test_missing_matter_does_not_scrape(tmp_path):
    scraper = FakeScraper()
    reply = make_agent(scraper).handle("", "Can I have some exhibits?", "", tmp_path)
    assert scraper.calls == []
    assert reply.attachment is None
    assert "couldn't find a matter number" in reply.body


def test_missing_type_still_reports_the_matter(tmp_path, metadata):
    scraper = FakeScraper(FetchResult(metadata=metadata, doc_type=None))
    reply = make_agent(scraper).handle("", "What is in M12205?", "", tmp_path)
    assert scraper.calls[0][1] is None
    assert "I found 13 Exhibits" in reply.body
    assert "didn't say which type" in reply.body
    assert reply.attachment is None


def test_not_found_and_site_errors(tmp_path):
    reply = make_agent(FakeScraper(error=MatterNotFound("x"))).handle("", "Exhibits M99999", "", tmp_path)
    assert "couldn't find matter M99999" in reply.body
    reply = make_agent(FakeScraper(error=ScrapeError("x"))).handle("", "Exhibits M12205", "", tmp_path)
    assert "Something went wrong" in reply.body


def test_demo_mode_shows_the_browser(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    config = Config.from_env(env_file=None)
    scraper = RegulatoryAgent(config).scraper
    assert (scraper.demo, scraper.headless) == (True, False)
    assert RegulatoryAgent(Config()).scraper.headless is True


def test_describe_row_handles_both_layouts():
    exhibit = _describe_row({"row": 1, "fields": ["102674", "Board Order", ".pdf", "07/08/2026", "Public"]})
    assert (exhibit.doc_id, exhibit.title, exhibit.date) == ("102674", "Board Order", "07/08/2026")
    transcript = _describe_row(
        {"row": 2, "fields": ["09/12/2022", "September 12, 2022", "Evening Session", "Pdf", "Public"]}
    )
    assert (transcript.doc_id, transcript.title) == ("09/12/2022", "September 12, 2022 - Evening Session")
