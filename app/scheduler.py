import schedule
import time
import logging
from threading import Lock
from app.scraper import fetch_opportunities_by_date
from app.telegram_bot import post_new_opportunities
from app.database import delete_old_entries, get_schedule_times

logger = logging.getLogger(__name__)

_last_times: list[str] = []
_lock = Lock()

def reload_schedule():
    """Re-read schedule times from DB and update the job queue."""
    global _last_times
    with _lock:
        times = get_schedule_times()
        if times == _last_times:
            return
        schedule.clear()
        for t in times:
            schedule.every().day.at(t).do(run_daily_tasks)
        _last_times = times
        print(f"[Scheduler] Times updated: {times}")

def run_daily_tasks():
    print(f"[Run] Running scheduled job...")
    try:
        fetch_opportunities_by_date()
        post_new_opportunities()
        delete_old_entries()
        logger.info("Scheduler task completed")
    except Exception as e:
        logger.error(f"Scheduler task failed: {e}")

def start_scheduler():
    reload_schedule()
    _check_counter = 0
    while True:
        try:
            schedule.run_pending()
            _check_counter += 1
            if _check_counter >= 10:  # Every ~5 minutes (10 * 30s)
                reload_schedule()
                _check_counter = 0
            time.sleep(30)
        except Exception as e:
            logger.error(f"Scheduler loop error: {e}")
            time.sleep(60)
