import schedule
import time
from app.scraper import fetch_opportunities_by_date
from app.telegram_bot import post_new_opportunities
from app.database import delete_old_entries

def run_daily_tasks():
    fetch_opportunities_by_date()
    post_new_opportunities()
    delete_old_entries()

def start_scheduler():
    schedule.every().day.at("08:00").do(run_daily_tasks)
    schedule.every().day.at("14:00").do(run_daily_tasks)
    schedule.every().day.at("20:00").do(run_daily_tasks)

    while True:
        schedule.run_pending()
        time.sleep(30)
