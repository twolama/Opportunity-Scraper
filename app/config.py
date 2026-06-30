import os
import logging
from dotenv import load_dotenv

load_dotenv()

_logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    _logger.warning("TELEGRAM_BOT_TOKEN not set — Telegram features disabled")
    TELEGRAM_API_URL = "https://api.telegram.org/botDISABLED"
else:
    TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

raw_owner = os.getenv("BOT_OWNER_ID", "").strip()
if raw_owner:
    try:
        BOT_OWNER_ID = int(raw_owner)
    except ValueError:
        raise ValueError(f"BOT_OWNER_ID must be an integer, got {raw_owner!r}")
else:
    BOT_OWNER_ID = 0

TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID") or ""

TELEGRAPH_ACCESS_TOKEN = os.getenv("TELEGRAPH_ACCESS_TOKEN", "")
TELEGRAPH_AUTHOR_NAME = os.getenv("TELEGRAPH_AUTHOR_NAME", "Opportunity Spot")
TELEGRAPH_AUTHOR_URL = os.getenv("TELEGRAPH_AUTHOR_URL", "https://t.me/opportunityspots")
PUBLIC_URL = os.getenv("PUBLIC_URL") or ""
USE_POLLING = os.getenv("USE_POLLING", "true").lower() == "true"
RUN_SCHEDULER = os.getenv("RUN_SCHEDULER", "true").lower() == "true"
API_KEY = os.getenv("API_KEY", "")
SENTRY_DSN = os.getenv("SENTRY_DSN", "")

raw_days = os.getenv("DELETE_OLDER_THAN_DAYS", "30")
try:
    DELETE_OLDER_THAN_DAYS = int(raw_days)
except ValueError:
    raise ValueError(f"DELETE_OLDER_THAN_DAYS must be an integer, got {raw_days!r}")
