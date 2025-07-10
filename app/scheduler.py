import schedule
import time
import logging
from app.scraper import fetch_opportunities_by_date
from app.telegram_bot import post_new_opportunities
from app.database import delete_old_entries

logger = logging.getLogger(__name__)

def run_daily_tasks():
    logger.info("Scheduler task started")
    fetch_opportunities_by_date()  # use yesterday by default
    post_new_opportunities()
    delete_old_entries()
    logger.info("Scheduler task completed")

def start_scheduler():
    schedule.every().day.at("07:59").do(run_daily_tasks)
    schedule.every().day.at("13:59").do(run_daily_tasks)
    schedule.every().day.at("19:59").do(run_daily_tasks)

    while True:
        schedule.run_pending()
        time.sleep(30)
