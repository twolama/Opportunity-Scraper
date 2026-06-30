import os
import json
import logging
import requests
from typing import Optional
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

from app.config import TELEGRAPH_ACCESS_TOKEN, TELEGRAPH_AUTHOR_NAME, TELEGRAPH_AUTHOR_URL
from app.http_client import http as _http, sanitize as _sanitize

_logger = logging.getLogger(__name__)

_TELEGRAPH_API = "https://api.telegra.ph"


class TelegraphError(Exception):
    pass


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (ConnectionError, requests.ConnectionError)):
        return True
    if isinstance(exc, requests.HTTPError):
        if exc.response is not None:
            return exc.response.status_code in (429, 500, 502, 503, 504)
        return False
    return isinstance(exc, requests.RequestException)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception(_is_retryable),
    reraise=True,
)
def _telegraph_post(method: str, data: dict) -> dict:
    url = f"{_TELEGRAPH_API}/{method}"
    resp = _http.post(url, json=data, timeout=15)
    resp.raise_for_status()
    result = resp.json()
    if not result.get("ok"):
        raise TelegraphError(result.get("error", "Unknown error"))
    return result["result"]


def ensure_account() -> Optional[str]:
    """Get or create a Telegraph account. Returns access_token or None."""
    token = TELEGRAPH_ACCESS_TOKEN
    if token:
        return token

    short_name = os.getenv("TELEGRAPH_SHORT_NAME", "OpportunitySpot")
    author_name = os.getenv("TELEGRAPH_AUTHOR_NAME", "Opportunity Spot")
    author_url = os.getenv("TELEGRAPH_AUTHOR_URL", "https://t.me/opportunityspots")
    try:
        result = _telegraph_post("createAccount", {
            "short_name": short_name,
            "author_name": author_name,
            "author_url": author_url,
        })
        token = result["access_token"]
        _logger.info("Created Telegraph account: short_name=%s", short_name)
        _logger.warning(
            "Set TELEGRAPH_ACCESS_TOKEN=%s in .env to persist this account", token
        )
        return token
    except Exception as e:
        _logger.error("Failed to create Telegraph account: %s", _sanitize(str(e)))
        return None


def create_page(title: str, content: list, *,
                author_name: Optional[str] = None,
                author_url: Optional[str] = None) -> Optional[str]:
    """Create a Telegraph page. Returns the page URL or None on failure."""
    token = ensure_account()
    if not token:
        return None

    data = {
        "access_token": token,
        "title": title[:256],
        "content": json.dumps(content),
        "author_name": author_name or TELEGRAPH_AUTHOR_NAME,
        "author_url": author_url or TELEGRAPH_AUTHOR_URL,
    }

    try:
        result = _telegraph_post("createPage", data)
        page_url = result["url"]
        _logger.info("Created Telegraph page: %s", page_url)
        return page_url
    except Exception as e:
        _logger.error("Failed to create Telegraph page: %s", _sanitize(str(e)))
        return None


def build_telegraph_content(opportunity: dict) -> list:
    """Build Telegraph Node array from opportunity data with rich layout."""
    nodes = []

    # Hero image
    thumbnail = opportunity.get("thumbnail", "")
    if thumbnail:
        nodes.append({"tag": "img", "attrs": {"src": thumbnail}})
        nodes.append({"tag": "p", "children": [""]})

    # Details section
    desc = opportunity.get("description", "")
    if desc:
        nodes.append({"tag": "h4", "children": ["Details"]})
        if "\n\n" in desc:
            paragraphs = desc.split("\n\n")
        else:
            paragraphs = desc.split("\n")
        for para in paragraphs:
            para = para.strip()
            if para:
                nodes.append({"tag": "p", "children": [para]})

    # Deadline
    if opportunity.get("deadline"):
        nodes.append({"tag": "p", "children": [""]})
        nodes.append({"tag": "hr"})
        nodes.append({
            "tag": "blockquote",
            "children": [
                {"tag": "b", "children": [f"📅 Deadline: {opportunity['deadline']}"]}
            ]
        })
        nodes.append({"tag": "hr"})

    # How to Apply section
    link = opportunity.get("link", "")
    if link:
        nodes.append({"tag": "p", "children": [""]})
        nodes.append({"tag": "h4", "children": ["How to Apply"]})
        nodes.append({
            "tag": "p",
            "children": [
                {"tag": "a", "attrs": {"href": link},
                 "children": [{"tag": "b", "children": ["📝 Apply Now"]}]}
            ]
        })

    # Footer
    join_us_url = os.getenv("JOIN_US_URL", "https://t.me/opportunityspots")
    nodes.append({"tag": "p", "children": [""]})
    nodes.append({
        "tag": "p",
        "children": [
            {"tag": "a", "attrs": {"href": join_us_url},
             "children": ["Join Opportunity Spot"]}
        ]
    })

    return nodes
