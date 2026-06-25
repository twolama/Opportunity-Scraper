import os
import re
import time
import random
import logging
import requests
from typing import Optional
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

import sentry_sdk
from app.database import update_posted_status, get_unposted_opportunities
from app.utils import format_telegram_message

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

_http = requests.Session()
_logger = logging.getLogger(__name__)

def _sanitize(msg: str) -> str:
    return re.sub(r'bot\d+:[\w-]+', 'bot***REDACTED***', str(msg))


_INVISIBLE_CHARS = re.compile(r'[\u2000-\u200F\u2028-\u202F\u205F-\u206F\uFEFF\u00AD\u061C\u180E]')


def _strip_invisible(text: str) -> str:
    return _INVISIBLE_CHARS.sub('', text)


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


def _telegram_retry() -> bool:
    """Return True to retry on any RequestException."""
    return True

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((requests.RequestException, ConnectionError)),
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


def post_to_telegram(opportunity: dict) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        print("[ERR] Missing Telegram credentials in environment variables.")
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
            "chat_id": TELEGRAM_CHANNEL_ID,
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
            payload["text"] = message

        try:
            response = _post_to_telegram_with_retry(payload, use_photo=use_photo)
        except requests.RequestException:
            if use_photo:
                _logger.warning(f"sendPhoto failed for '{opportunity['title']}', falling back to sendMessage")
                payload.pop("photo", None)
                payload.pop("caption", None)
                payload["text"] = message
                response = _post_to_telegram_with_retry(payload, use_photo=False)
            else:
                raise

        _logger.info(f"Posted to Telegram: {opportunity['title']}")
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


def post_new_opportunities(date_str: Optional[str] = None):
    """Fetch unposted opportunities from DB and post them to Telegram."""
    opportunities = get_unposted_opportunities()
    if not opportunities:
        print("No new opportunities to post.")
        return
    for opp in opportunities:
        posted = post_to_telegram(opp)
        if posted:
            print(f"Posted: {opp['title']}")
        else:
            print(f"Failed to post: {opp['title']}")
