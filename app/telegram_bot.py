import os
from dotenv import load_dotenv
import requests
from app.database import update_posted_status  # helper to mark opportunities as posted
from app.utils import format_telegram_message  # helper for clean message formatting

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

def post_to_telegram(opportunity: dict) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        print("❌ Missing Telegram credentials in environment variables.")
        return False

    message = format_telegram_message(opportunity)

    try:
        if opportunity.get("thumbnail"):
            # Send with thumbnail
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            payload = {
                "chat_id": TELEGRAM_CHANNEL_ID,
                "photo": opportunity["thumbnail"],
                "caption": message[:1024],  # Telegram max caption length
                "parse_mode": "HTML",
                "disable_web_page_preview": False
            }
            response = requests.post(url, data=payload)
        else:
            # Send plain message
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": TELEGRAM_CHANNEL_ID,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": False
            }
            response = requests.post(url, json=payload)

        if response.ok:
            print(f"✅ Posted to Telegram: {opportunity['title']}")
            update_posted_status(opportunity["id"])  # Mark as posted in DB
            return True
        else:
            print(f"❌ Telegram API Error: {response.text}")
            return False

    except Exception as e:
        print(f"❌ Exception while posting to Telegram: {e}")
        return False
