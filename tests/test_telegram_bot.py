import requests
from app.telegram_bot import _close_html_tags, _is_retryable


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
