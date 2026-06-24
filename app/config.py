import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

BOT_OWNER_ID_str = os.getenv("BOT_OWNER_ID", "")
BOT_OWNER_ID = int(BOT_OWNER_ID_str) if BOT_OWNER_ID_str.strip() else 0

TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
PUBLIC_URL = os.getenv("PUBLIC_URL")
USE_POLLING = os.getenv("USE_POLLING", "true").lower() == "true"
RUN_SCHEDULER = os.getenv("RUN_SCHEDULER", "true").lower() == "true"
API_KEY = os.getenv("API_KEY", "")
