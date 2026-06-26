from app.http_client import sanitize, strip_invisible, _TimeoutAdapter, make_session


class TestSanitize:
    def test_redacts_bot_token(self):
        result = sanitize("token bot12345:ABCdefGHIJklmno")
        assert "bot***REDACTED***" in result
        assert "bot12345:ABCdefGHIJklmno" not in result

    def test_empty_string(self):
        assert sanitize("") == ""

    def test_non_string(self):
        assert "error" in sanitize(Exception("test error"))


class TestStripInvisible:
    def test_removes_zero_width_spaces(self):
        assert strip_invisible("hello\u200Bworld") == "helloworld"

    def test_removes_mixed_invisible_chars(self):
        result = strip_invisible("a\u200Fb\u202Ec")
        assert result == "abc"

    def test_plain_text_unchanged(self):
        assert strip_invisible("hello world") == "hello world"


class TestTimeoutAdapter:
    def test_sets_default_timeout(self):
        adapter = _TimeoutAdapter(timeout=42)
        assert adapter.timeout == 42


class TestMakeSession:
    def test_returns_new_session(self):
        s1 = make_session()
        s2 = make_session()
        assert s1 is not s2
        s1.close()
        s2.close()
