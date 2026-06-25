import math
import schedule
import time
import logging
from datetime import datetime
from threading import Lock
import sentry_sdk
from app.scraper import fetch_opportunities_by_date_safe
from app.database import delete_old_entries, get_schedule_times, get_unposted_opportunities
from app.telegram_bot import post_to_telegram

logger = logging.getLogger(__name__)

_last_scrape: list[str] = []
_last_post: list[str] = []
_lock = Lock()
_catch_up_done_today: str = ""

def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")

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
        fetch_opportunities_by_date_safe()
        delete_old_entries()
        logger.info("Search task completed")
    except Exception as e:
        logger.error(f"Search task failed: {e}", exc_info=True)
        sentry_sdk.capture_exception(e)

def _passed_post_slots_today() -> int:
    now = datetime.now().strftime("%H:%M")
    post_times = get_schedule_times("post")
    return sum(1 for t in sorted(post_times) if t < now)

def _remaining_post_slots_today() -> int:
    now = datetime.now().strftime("%H:%M")
    post_times = get_schedule_times("post")
    return sum(1 for t in sorted(post_times) if t >= now)

def run_post():
    print(f"[Scheduler] Running post...")
    try:
        unposted = get_unposted_opportunities()
        total = len(get_schedule_times("post"))
        remaining = _remaining_post_slots_today()
        if not unposted:
            print("[Scheduler] No unposted opportunities.")
            return
        if total <= 0:
            print("[Scheduler] No post times configured.")
            return
        if remaining <= 0:
            remaining = total
        batch_size = math.ceil(len(unposted) / remaining)
        batch = unposted[:batch_size]
        print(f"[Scheduler] Posting {len(batch)}/{len(unposted)} opportunities ({batch_size} per {remaining} remaining slot(s))")
        for opp in batch:
            post_to_telegram(opp)
        logger.info(f"Posted {len(batch)} opportunities")
    except Exception as e:
        logger.error(f"Post task failed: {e}", exc_info=True)
        sentry_sdk.capture_exception(e)

def _catch_up_posts():
    global _catch_up_done_today
    today = _today_str()
    if _catch_up_done_today == today:
        return
    passed = _passed_post_slots_today()
    if passed <= 0:
        _catch_up_done_today = today
        return
    unposted = get_unposted_opportunities()
    total = len(get_schedule_times("post"))
    if not unposted or total <= 0:
        _catch_up_done_today = today
        return
    batch_per_slot = math.ceil(len(unposted) / total)
    batch_size = min(batch_per_slot * passed, len(unposted))
    batch = unposted[:batch_size]
    print(f"[Scheduler] Catch-up: posting {len(batch)}/{len(unposted)} opportunities ({passed} slot(s) missed)")
    for opp in batch:
        post_to_telegram(opp)
    _catch_up_done_today = today

def start_scheduler():
    reload_schedules()
    _catch_up_posts()
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
            logger.error(f"Scheduler loop error: {e}", exc_info=True)
            sentry_sdk.capture_exception(e)
            time.sleep(60)
