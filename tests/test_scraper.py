from app.scraper import clean_url, clean_deadline, random_headers


class TestCleanUrl:
    def test_removes_invisible_chars(self):
        assert clean_url("http://example.com\u200B/page") == "http://example.com/page"

    def test_strips_whitespace(self):
        assert clean_url("  http://example.com  ") == "http://example.com"

    def test_clean_url_unchanged(self):
        url = "https://opportunitydesk.org/apply-now"
        assert clean_url(url) == url

    def test_none_returns_empty(self):
        assert clean_url(None) == ""


class TestCleanDeadline:
    def test_none_input(self):
        assert clean_deadline(None) is None

    def test_us_format(self):
        result = clean_deadline("Deadline: March 15, 2025")
        assert "March 15, 2025" in result

    def test_us_format_with_th(self):
        result = clean_deadline("Deadline: March 15th, 2025")
        assert "March 15th, 2025" in result

    def test_date_format(self):
        result = clean_deadline("Deadline: 03/15/2025")
        assert "03/15/2025" in result

    def test_iso_format(self):
        result = clean_deadline("Deadline: 2025-03-15")
        assert "2025-03-15" in result

    def test_unrecognized_format(self):
        assert clean_deadline("rolling deadline") == "rolling deadline"


class TestRandomHeaders:
    def test_returns_dict(self):
        headers = random_headers()
        assert isinstance(headers, dict)
        assert "User-Agent" in headers
        assert "Accept" in headers
        assert "Accept-Language" in headers

    def test_random_user_agent(self):
        agents = set()
        for _ in range(50):
            agents.add(random_headers()["User-Agent"])
        assert len(agents) > 1
