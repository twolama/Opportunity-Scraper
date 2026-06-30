import requests
from app.utils import _close_html_tags, split_html_message
from app.telegram_bot import _is_retryable


class TestCloseHtmlTags:
    def test_no_tags_unchanged(self):
        assert _close_html_tags("hello world") == "hello world"

    def test_closes_unclosed_tag(self):
        assert _close_html_tags("<b>hello") == "<b>hello</b>"

    def test_nested_unclosed(self):
        assert _close_html_tags("<b><i>deep") == "<b><i>deep</i></b>"

    def test_self_closing_ignored(self):
        assert _close_html_tags("line<br>more") == "line<br>more"

    def test_already_closed(self):
        assert _close_html_tags("<b>hello</b>") == "<b>hello</b>"

    def test_truncates_at_unclosed_angle(self):
        assert _close_html_tags("hello<world") == "hello"


class TestIsRetryable:
    def test_connection_error_is_retryable(self):
        assert _is_retryable(ConnectionError("conn reset"))

    def test_requests_connection_error_is_retryable(self):
        assert _is_retryable(requests.ConnectionError("conn reset"))

    def test_500_is_retryable(self):
        resp = requests.Response()
        resp.status_code = 500
        exc = requests.HTTPError(response=resp)
        assert _is_retryable(exc)

    def test_400_is_not_retryable(self):
        resp = requests.Response()
        resp.status_code = 400
        exc = requests.HTTPError(response=resp)
        assert not _is_retryable(exc)

    def test_404_is_not_retryable(self):
        resp = requests.Response()
        resp.status_code = 404
        exc = requests.HTTPError(response=resp)
        assert not _is_retryable(exc)

    def test_generic_request_exception_is_retryable(self):
        assert _is_retryable(requests.RequestException("timeout"))


class TestSplitHtmlMessage:
    def test_short_message_unchanged(self):
        msg = "<b>Title</b>\n\nShort description"
        result = split_html_message(msg, max_length=4096)
        assert result == [msg]

    def test_splits_at_paragraph_boundary(self):
        short = "<b>Title</b>\n\n"
        long_para = "A" * 3000
        rest = "\n\n<b>Deadline:</b> Jan 1"
        msg = short + long_para + rest
        result = split_html_message(msg, max_length=2000)
        assert len(result) >= 2
        assert result[0].startswith("<b>Title</b>")
        assert "<b>Deadline:</b> Jan 1" in result[-1]

    def test_no_message_loss(self):
        long_para = "Hello " * 2000
        msg = f"<b>Test</b>\n\n{long_para}\n\n<b>End</b>"
        result = split_html_message(msg, max_length=2000)
        joined = "".join(result)
        for word in ("Hello", "<b>Test</b>", "<b>End</b>"):
            assert word in joined

    def test_single_long_paragraph_split(self):
        para = "word " * 3000
        chunks = split_html_message(para, max_length=2000)
        assert len(chunks) > 1
        assert all(len(c) <= 2000 for c in chunks)
        assert chunks[0].endswith("</b>") == False  # no unclosed tags

    def test_under_limit_returns_single_chunk(self):
        msg = "Short message"
        assert split_html_message(msg, max_length=100) == [msg]

    def test_exact_limit_single_chunk(self):
        msg = "A" * 100
        assert split_html_message(msg, max_length=100) == [msg]

    def test_over_limit_by_one(self):
        msg = "A" * 101
        chunks = split_html_message(msg, max_length=100)
        assert len(chunks) == 2
        assert len(chunks[0]) == 100
        assert len(chunks[1]) == 1
