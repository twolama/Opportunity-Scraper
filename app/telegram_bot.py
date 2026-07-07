import re
import time
import random
import logging
import requests
from typing import Optional
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

import sentry_sdk
from app.config import TELEGRAM_API_URL, TELEGRAM_CHANNEL_ID, TELEGRAM_BOT_TOKEN
from app.database import update_posted_status, get_unposted_opportunities
from app.utils import format_telegram_message, format_condensed_post, _close_html_tags, split_html_message
from app.telegraph import create_page, build_telegraph_content

from app.http_client import http as _http, sanitize as _sanitize, strip_invisible as _strip_invisible
_logger = logging.getLogger(__name__)


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
        url = f"{TELEGRAM_API_URL}/sendPhoto"
    else:
        url = f"{TELEGRAM_API_URL}/sendMessage"
    resp = _http.post(url, json=payload, timeout=15)
    resp.raise_for_status()
    return resp


def post_to_telegram(opportunity: dict, chat_id: Optional[str] = None) -> bool:
    target = chat_id or TELEGRAM_CHANNEL_ID
    if not TELEGRAM_BOT_TOKEN or not target:
        _logger.error("Missing Telegram credentials in environment variables.")
        return False

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
        thumbnail = _strip_invisible(opportunity.get("thumbnail", ""))
        use_photo = bool(thumbnail)

        # Determine limits
        text_limit = 1024 if use_photo else 4096

        # Try full message first
        message = format_telegram_message(opportunity)
        fits_inline = len(message) <= text_limit

        if fits_inline:
            # Short enough — send directly (existing behavior)
            payload = {
                "chat_id": target,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
                "reply_markup": reply_markup
            }
            if use_photo:
                payload["photo"] = thumbnail
                payload["caption"] = message
            else:
                payload["text"] = message

            try:
                response = _post_to_telegram_with_retry(payload, use_photo=use_photo)
            except requests.RequestException:
                if use_photo:
                    _logger.warning(
                        f"sendPhoto failed for '{opportunity['title']}', falling back to sendMessage"
                    )
                    text_limit = 4096
                    fits_inline = len(message) <= text_limit
                    if fits_inline:
                        payload.pop("photo", None)
                        payload.pop("caption", None)
                        payload["text"] = message
                        response = _post_to_telegram_with_retry(payload, use_photo=False)
                        use_photo = False
                    else:
                        raise
                else:
                    raise

            _logger.info(f"Posted directly to Telegram: {opportunity['title']} -> {target}")
            opp_id = opportunity.get("id")
            if opp_id:
                update_posted_status(opp_id)
            return True

        # Message too long — try Telegraph
        telegraph_url = create_page(
            title=opportunity.get("title", "Opportunity"),
            content=build_telegraph_content(opportunity),
        )

        if telegraph_url:
            # Success — send condensed post with Telegraph link
            condensed = format_condensed_post(opportunity, telegraph_url)

            # Try photo+caption if thumbnail available and condensed fits
            if use_photo and len(condensed) <= 1024:
                payload = {
                    "chat_id": target,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": False,
                    "reply_markup": reply_markup,
                    "photo": thumbnail,
                    "caption": condensed,
                }
                try:
                    response = _post_to_telegram_with_retry(payload, use_photo=True)
                except requests.RequestException:
                    _logger.warning(
                        f"sendPhoto failed for '{opportunity['title']}', falling back to sendMessage"
                    )
                    payload.pop("photo", None)
                    payload.pop("caption", None)
                    payload["text"] = condensed
                    response = _post_to_telegram_with_retry(payload, use_photo=False)
            else:
                payload = {
                    "chat_id": target,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": False,
                    "reply_markup": reply_markup,
                    "text": condensed,
                }
                response = _post_to_telegram_with_retry(payload, use_photo=False)

            _logger.info(f"Posted via Telegraph: {opportunity['title']} -> {target}")
            opp_id = opportunity.get("id")
            if opp_id:
                update_posted_status(opp_id)
            return True

        # Telegraph failed — fall back to splitting
        _logger.warning(
            f"Telegraph unavailable for '{opportunity['title']}', falling back to split message"
        )
        if use_photo and text_limit == 1024:
            use_photo = False
            text_limit = 4096

        chunks = split_html_message(message, max_length=text_limit)

        payload = {
            "chat_id": target,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
            "reply_markup": reply_markup
        }

        if use_photo:
            payload["photo"] = thumbnail
            payload["caption"] = chunks[0]
        else:
            payload["text"] = chunks[0]

        try:
            response = _post_to_telegram_with_retry(payload, use_photo=use_photo)
        except requests.RequestException:
            if use_photo:
                _logger.warning(
                    f"sendPhoto failed for '{opportunity['title']}', falling back to sendMessage"
                )
                chunks = split_html_message(message, max_length=4096)
                payload.pop("photo", None)
                payload.pop("caption", None)
                payload["text"] = chunks[0]
                response = _post_to_telegram_with_retry(payload, use_photo=False)
            else:
                raise

        first_message_id = response.json()["result"]["message_id"]

        for chunk in chunks[1:]:
            reply_payload = {
                "chat_id": target,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "text": chunk,
                "reply_to_message_id": first_message_id,
            }
            try:
                _post_to_telegram_with_retry(reply_payload, use_photo=False)
            except requests.RequestException as e:
                _logger.warning(f"Failed to send continuation chunk: {_sanitize(str(e))}")

        _logger.info(
            f"Posted to Telegram (split fallback): {opportunity['title']} -> {target} ({len(chunks)} chunk(s))"
        )
        opp_id = opportunity.get("id")
        if opp_id:
            update_posted_status(opp_id)
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
