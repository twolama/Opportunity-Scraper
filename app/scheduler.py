import schedule
import time
import logging
from threading import Lock
from app.scraper import fetch_opportunities_by_date
from app.telegram_bot import post_new_opportunities
from app.database import delete_old_entries, get_schedule_times

logger = logging.getLogger(__name__)

_last_scrape: list[str] = []
_last_post: list[str] = []
_lock = Lock()

def reload_schedules():
    global _last_scrape, _last_post
    with _lock:
        scrape_times = get_schedule_times("scrape")
        post_times = get_schedule_times("post")
        changed = False
        if scrape_times != _last_scrape:
            _last_scrape = scrape_times
            changed = True
        if post_times != _last_post:
            _last_post = post_times
            changed = True
        if not changed:
            return
        schedule.clear()
        for t in scrape_times:
            schedule.every().day.at(t).do(run_scrape)
        for t in post_times:
            schedule.every().day.at(t).do(run_post)
        print(f"[Scheduler] Search times: {scrape_times}")
        print(f"[Scheduler] Post times: {post_times}")

def run_scrape():
    print(f"[Scheduler] Running search...")
    try:
        fetch_opportunities_by_date()
        delete_old_entries()
        logger.info("Search task completed")
    except Exception as e:
        logger.error(f"Search task failed: {e}")

def run_post():
    print(f"[Scheduler] Running post...")
    try:
        post_new_opportunities()
        logger.info("Post task completed")
    except Exception as e:
        logger.error(f"Post task failed: {e}")

def start_scheduler():
    reload_schedules()
    check_counter = 0
    while True:
        try:
            schedule.run_pending()
            check_counter += 1
            if check_counter >= 10:
                reload_schedules()
                check_counter = 0
            time.sleep(30)
        except Exception as e:
            logger.error(f"Scheduler loop error: {e}")
            time.sleep(60)
