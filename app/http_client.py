import re
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class _TimeoutAdapter(HTTPAdapter):
    def __init__(self, timeout=15, max_retries=2, *args, **kwargs):
        self.timeout = timeout
        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST", "PUT", "DELETE", "HEAD"],
        )
        super().__init__(max_retries=retry_strategy, *args, **kwargs)

    def send(self, request, **kwargs):
        kwargs.setdefault("timeout", self.timeout)
        return super().send(request, **kwargs)


http = requests.Session()
http.mount("https://", _TimeoutAdapter(timeout=15))
http.mount("http://", _TimeoutAdapter(timeout=15))


def make_session() -> requests.Session:
    """Create a new session with the same timeout defaults (thread-safe)."""
    s = requests.Session()
    s.mount("https://", _TimeoutAdapter(timeout=15))
    s.mount("http://", _TimeoutAdapter(timeout=15))
    return s


_SANITIZE_RE = re.compile(r'bot\d+:[\w-]+')


def sanitize(msg: str) -> str:
    return _SANITIZE_RE.sub('bot***REDACTED***', str(msg))


_INVISIBLE_CHARS = re.compile(r'[\u2000-\u200F\u2028-\u202F\u205F-\u206F\uFEFF\u00AD\u061C\u180E]')


def strip_invisible(text: str) -> str:
    return _INVISIBLE_CHARS.sub('', text)
