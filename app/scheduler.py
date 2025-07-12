import schedule
import time
import logging
from app.scraper import fetch_opportunities_by_date
from app.telegram_bot import post_new_opportunities
from app.database import delete_old_entries

logger = logging.getLogger(__name__)

def run_daily_tasks():
    print("🔁 Running scheduled job...")
    logger.info("Scheduler task started")
    fetch_opportunities_by_date()  # use yesterday by default
    post_new_opportunities()
    delete_old_entries()
    logger.info("Scheduler task completed")

def start_scheduler():
    schedule.every().day.at("04:59").do(run_daily_tasks)  # 07:59 AM Ethiopia
    schedule.every().day.at("10:59").do(run_daily_tasks)  # 01:59 PM Ethiopia
    schedule.every().day.at("16:59").do(run_daily_tasks)  # 07:59 PM Ethiopia
    
    schedule.every().day.at("05:19").do(run_daily_tasks)  # 08:19 PM Ethiopia

    
    
    while True:
        schedule.run_pending()
        time.sleep(30)
