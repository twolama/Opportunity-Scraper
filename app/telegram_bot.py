import os
import re
import requests
from typing import Optional
from dotenv import load_dotenv

from app.database import update_posted_status, get_unposted_opportunities
from app.utils import format_telegram_message

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

_http = requests.Session()

def _sanitize(msg: str) -> str:
    return re.sub(r'bot\d+:[\w-]+', 'bot***REDACTED***', str(msg))


def post_to_telegram(opportunity: dict) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        print("[ERR] Missing Telegram credentials in environment variables.")
        return False

    message = format_telegram_message(opportunity)

    # Prepare inline button
    link = re.sub(r'[\u2000-\u200F\u2028-\u202F\u205F-\u206F\uFEFF]', '', opportunity.get("link", "https://fallback-link.com")).strip()
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
        if opportunity.get("thumbnail"):
            # Send message with thumbnail and button
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            payload = {
                "chat_id": TELEGRAM_CHANNEL_ID,
                "photo": opportunity["thumbnail"],
                "caption": message[:1024],
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
                "reply_markup": reply_markup
            }
            response = _http.post(url, json=payload)  # use json for buttons to work
        else:
            # Send plain message with inline button
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": TELEGRAM_CHANNEL_ID,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
                "reply_markup": reply_markup
            }
            response = _http.post(url, json=payload)

        if response.ok:
            print(f"[OK] Posted to Telegram: {opportunity['title']}")
            update_posted_status(opportunity["id"])
            return True
        else:
            print(f"[ERR] Telegram API Error: {response.status_code} - {_sanitize(response.text)}")
            return False

    except Exception as e:
        print(f"[ERR] Exception while posting to Telegram: {_sanitize(e)}")
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
