import os
import re
import time
import random
import logging
import requests
from typing import Optional
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

import sentry_sdk
from app.database import update_posted_status, get_unposted_opportunities
from app.utils import format_telegram_message

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

from app.http_client import http as _http, sanitize as _sanitize, strip_invisible as _strip_invisible
_logger = logging.getLogger(__name__)


def _close_html_tags(text: str) -> str:
    """Close any unclosed HTML tags after truncation."""
    tags = []
    i = 0
    while i < len(text):
        if text[i] == '<':
            close = text.find('>', i)
            if close == -1:
                text = text[:i]
                break
            tag = text[i+1:close]
            if tag.startswith('/'):
                if tags and tags[-1] == tag[1:]:
                    tags.pop()
            elif not tag.endswith('/') and tag[0] != '/' and ' ' not in tag and tag not in ('br', 'hr'):
                tags.append(tag.split()[0])
            i = close + 1
        else:
            i += 1
    for t in reversed(tags):
        text += f'</{t}>'
    return text


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (ConnectionError, requests.ConnectionError)):
        return True
    if isinstance(exc, requests.HTTPError):
        if exc.response is not None:
            return exc.response.status_code == 429 or exc.response.status_code >= 500
        return False
    return isinstance(exc, requests.RequestException)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception(_is_retryable),
    reraise=True,
)
def _post_to_telegram_with_retry(payload: dict, use_photo: bool = False) -> requests.Response:
    if use_photo:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    else:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = _http.post(url, json=payload, timeout=15)
    resp.raise_for_status()
    return resp


def post_to_telegram(opportunity: dict, chat_id: Optional[str] = None) -> bool:
    target = chat_id or TELEGRAM_CHANNEL_ID
    if not TELEGRAM_BOT_TOKEN or not target:
        _logger.error("Missing Telegram credentials in environment variables.")
        return False

    message = format_telegram_message(opportunity)

    # Prepare inline button
    link = _strip_invisible(opportunity.get("link", "https://fallback-link.com")).strip()
    reply_markup = {
        "inline_keyboard": [
            [
                {
                    "text": "Apply Now",
                    "url": link if link else "https://fallback-link.com"
                }
            ]
        ]
    }

    try:
        payload = {
            "chat_id": target,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
            "reply_markup": reply_markup
        }
        thumbnail = _strip_invisible(opportunity.get("thumbnail", ""))
        use_photo = bool(thumbnail)

        if use_photo:
            caption = _close_html_tags(message[:1024])
            payload["photo"] = thumbnail
            payload["caption"] = caption
        else:
            payload["text"] = _close_html_tags(message[:4096])

        try:
            response = _post_to_telegram_with_retry(payload, use_photo=use_photo)
        except requests.RequestException:
            if use_photo:
                _logger.warning(f"sendPhoto failed for '{opportunity['title']}', falling back to sendMessage")
                payload.pop("photo", None)
                payload.pop("caption", None)
                payload["text"] = _close_html_tags(message[:4096])
                response = _post_to_telegram_with_retry(payload, use_photo=False)
            else:
                raise

        _logger.info(f"Posted to Telegram: {opportunity['title']} -> {target}")
        update_posted_status(opportunity["id"])
        return True

    except requests.RequestException as e:
        _logger.error(f"Telegram API error for '{opportunity['title']}': {_sanitize(str(e))}")
        sentry_sdk.capture_exception(e)
        return False
    except Exception as e:
        _logger.error(f"Unexpected error posting '{opportunity['title']}': {_sanitize(str(e))}")
        sentry_sdk.capture_exception(e)
        return False


def post_to_all_channels(opportunity: dict) -> bool:
    """Post an opportunity to all active channels. Returns True if at least one succeeded."""
    from app.database import get_active_channels
    channels = get_active_channels()
    if not channels:
        if TELEGRAM_CHANNEL_ID:
            channels = [{"chat_id": TELEGRAM_CHANNEL_ID, "title": "default"}]
        else:
            _logger.warning("No channels configured and TELEGRAM_CHANNEL_ID not set")
            return False
    any_success = False
    for ch in channels:
        ok = post_to_telegram(opportunity, chat_id=str(ch["chat_id"]))
        if ok:
            any_success = True
    return any_success


def post_new_opportunities(date_str: Optional[str] = None):
    """Fetch unposted opportunities from DB and post them to Telegram."""
    opportunities = get_unposted_opportunities()
    if not opportunities:
        _logger.info("No new opportunities to post.")
        return
    for opp in opportunities:
        posted = post_to_telegram(opp)
        if posted:
            _logger.info("Posted: %s", opp["title"])
        else:
            _logger.warning("Failed to post: %s", opp["title"])
