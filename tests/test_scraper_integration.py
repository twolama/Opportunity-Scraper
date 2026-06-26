"""Integration tests for scraper with real HTML fixtures."""

from unittest.mock import MagicMock, patch
from bs4 import BeautifulSoup
import pytest
import os

from app.scraper import (
    extract_detail_info,
    _fetch_article,
    fetch_opportunities_by_date,
)
from app.http_client import make_session

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "html_fixtures")


def _load_fixture(name):
    with open(os.path.join(FIXTURE_DIR, name), encoding="utf-8") as f:
        return f.read()


def _fake_response(html, status=200):
    resp = MagicMock()
    resp.text = html
    resp.status_code = status
    resp.raise_for_status.return_value = None
    return resp


class TestExtractDetailInfo:
    """Tests for extract_detail_info with a real HTML detail page."""

    def test_extracts_all_fields(self):
        html = _load_fixture("detail_page.html")
        session = MagicMock()

        with patch("app.scraper.safe_get", return_value=_fake_response(html)):
            link, deadline, thumb, desc, tags = extract_detail_info(session, "https://example.com/opp")

        assert link == "https://apply.example.com/123"
        assert deadline == "June 30, 2026"
        assert thumb == "https://example.com/thumb.jpg"
        assert "This is the first paragraph" in desc
        assert "Second paragraph" not in desc
        assert tags == ["Scholarship", "Fully Funded"]

    def test_missing_link_falls_back_to_detail_url(self):
        html = "<html><body><div class='entry-content'><p>No link here</p></div></body></html>"
        session = MagicMock()
        detail_url = "https://opportunitydesk.org/2026/06/25/test-opp/"

        with patch("app.scraper.safe_get", return_value=_fake_response(html)):
            link, deadline, thumb, desc, tags = extract_detail_info(session, detail_url)

        assert link == detail_url
        assert deadline is None
        assert thumb is None
        assert desc == "No link here"

    def test_extracts_deadline_from_strong_tag(self):
        html = """
        <html><body>
        <div class='entry-content'>
        <p><strong>Deadline:</strong> July 15, 2026</p>
        <p>Description line 1.</p></div>
        </body></html>
        """
        session = MagicMock()
        with patch("app.scraper.safe_get", return_value=_fake_response(html)):
            _, deadline, _, _, _ = extract_detail_info(session, "https://example.com/opp")
        assert deadline == "July 15, 2026"


class TestFetchArticle:
    """Tests for _fetch_article end-to-end with real HTML."""

    def test_returns_opportunity_dict(self):
        detail_html = _load_fixture("detail_page.html")
        listing_html = '<article><a href="https://opportunitydesk.org/2026/06/25/test-opp/">Test Opp</a></article>'

        article = BeautifulSoup(listing_html, "html.parser").find("article")

        with patch("app.scraper.safe_get", return_value=_fake_response(detail_html)), \
             patch("app.scraper.make_session", return_value=MagicMock()):
            opp = _fetch_article(article)

        assert opp is not None
        assert opp["title"] == "Test Opp"
        assert opp["link"] == "https://apply.example.com/123"
        assert opp["deadline"] == "June 30, 2026"
        assert opp["thumbnail"] == "https://example.com/thumb.jpg"
        assert opp["tags"] == ["Scholarship", "Fully Funded"]

    def test_relative_link_resolved_and_skipped(self):
        detail_html = """
        <html><body><div class='entry-content'>
        <p>For more information <a href="/apply/456">apply here</a>.</p>
        </div></body></html>
        """
        listing_html = '<article><a href="https://opportunitydesk.org/2026/06/25/test-opp/">Test Opp</a></article>'
        article = BeautifulSoup(listing_html, "html.parser").find("article")

        with patch("app.scraper.safe_get", return_value=_fake_response(detail_html)), \
             patch("app.scraper.make_session", return_value=MagicMock()):
            opp = _fetch_article(article)

        assert opp is None

    def test_no_title_link_returns_none(self):
        article = BeautifulSoup("<article><p>No anchor</p></article>", "html.parser").find("article")
        assert _fetch_article(article) is None


@patch("app.scraper.bulk_save_opportunities", return_value=2)
@patch("app.scraper.opportunities_exist", return_value=set())
class TestFetchOpportunitiesByDate:
    """End-to-end test for the main fetch function with mocked HTTP."""

    def test_fetch_and_save(self, mock_exist, mock_save):
        detail_html = _load_fixture("detail_page.html")
        listing_html = _load_fixture("listing_page.html")
        call_count = 0

        def side_effect(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _fake_response(listing_html)
            return _fake_response(detail_html)

        with patch("app.scraper.safe_get", side_effect=side_effect):
            result = fetch_opportunities_by_date("2026/06/25")

        assert len(result) == 2
        assert result[0]["title"] == "Test Opportunity 1"
        assert result[1]["title"] == "Test Opportunity 2"
        mock_save.assert_called_once()
        args = mock_save.call_args[0][0]
        assert len(args) == 2

    def test_all_existing_returns_empty(self, mock_exist, mock_save):
        mock_exist.return_value = {
            "https://opportunitydesk.org/2026/06/25/test-opp-1/",
            "https://opportunitydesk.org/2026/06/25/test-opp-2/",
        }

        with patch("app.scraper.safe_get", return_value=_fake_response(_load_fixture("listing_page.html"))):
            result = fetch_opportunities_by_date("2026/06/25")

        assert result == []
        mock_save.assert_not_called()

    def test_http_failure_returns_empty(self, mock_exist, mock_save):
        with patch("app.scraper.safe_get", return_value=None):
            result = fetch_opportunities_by_date("2026/06/25")
        assert result == []
        mock_save.assert_not_called()
