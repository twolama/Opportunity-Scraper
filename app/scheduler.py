import math
import schedule
import time
import logging
import threading
from datetime import datetime, timedelta
from threading import Lock
from concurrent.futures import ThreadPoolExecutor, as_completed
import sentry_sdk
from app.scraper import fetch_opportunities_by_date_safe
from app.database import delete_old_entries, get_schedule_times, get_unposted_opportunities
from app.telegram_bot import post_to_all_channels
from app.rate_limiter import telegram_limiter

logger = logging.getLogger(__name__)

_last_scrape: list[str] = []
_last_post: list[str] = []
_lock = Lock()
_catch_up_scrape_done_today: str = ""
_telegram_failures: int = 0
_telegram_failures_lock = Lock()
_TELEGRAM_CIRCUIT_BREAKER_MAX = 5

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
        now = datetime.now().strftime("%H:%M")
        for t in scrape_times:
            if t >= now:
                schedule.every().day.at(t).do(run_scrape)
        for t in post_times:
            if t >= now:
                schedule.every().day.at(t).do(run_post)
        logger.info("Search times: %s", scrape_times)
        logger.info("Post times: %s", post_times)

def run_scrape():
    logger.info("Running search...")
    try:
        fetch_opportunities_by_date_safe()
        delete_old_entries()
        logger.info("Search task completed")
    except Exception as e:
        logger.error(f"Search task failed: {e}", exc_info=True)
        sentry_sdk.capture_exception(e)

def _passed_scrape_slots_today() -> int:
    now = datetime.now().strftime("%H:%M")
    scrape_times = get_schedule_times("scrape")
    return sum(1 for t in sorted(scrape_times) if t < now)


def _catch_up_scrapes():
    global _catch_up_scrape_done_today
    today = _today_str()
    if _catch_up_scrape_done_today == today:
        return
    passed = _passed_scrape_slots_today()
    if passed <= 0:
        _catch_up_scrape_done_today = today
        return
    weekday_count = 0
    max_iter = 10
    target = min(passed, 5)
    for i in range(max_iter):
        if weekday_count >= target:
            break
        day_dt = datetime.now() - timedelta(days=i + 1)
        if day_dt.weekday() >= 5:
            logger.info("Catch-up skipping weekend %s (no articles expected)", day_dt.strftime("%Y/%m/%d"))
            continue
        weekday_count += 1
        day = day_dt.strftime("%Y/%m/%d")
        logger.info("Catch-up search for %s (%d/%d weekday(s))", day, weekday_count, target)
        try:
            fetch_opportunities_by_date_safe(day)
        except Exception:
            logger.exception(f"Catch-up search failed for {day}")
    _catch_up_scrape_done_today = today


def _remaining_post_slots_today() -> int:
    now = datetime.now().strftime("%H:%M")
    post_times = get_schedule_times("post")
    return sum(1 for t in sorted(post_times) if t >= now)

def _post_batch(batch: list) -> int:
    """Post a batch of opportunities to all channels in parallel with rate limiting."""
    sent = 0
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {}
        for opp in batch:
            wait = telegram_limiter.consume()
            if wait > 0:
                time.sleep(wait)
            futures[pool.submit(post_to_all_channels, opp)] = opp
        for future in as_completed(futures):
            if future.result():
                sent += 1
    return sent

def run_post():
    global _telegram_failures
    with _telegram_failures_lock:
        if _telegram_failures >= _TELEGRAM_CIRCUIT_BREAKER_MAX:
            logger.warning("Telegram circuit breaker open (%s consecutive failures), skipping post cycle", _telegram_failures)
            _telegram_failures = max(0, _telegram_failures - 1)
            return
    logger.info("Running post...")
    try:
        unposted = get_unposted_opportunities()
        total = len(get_schedule_times("post"))
        remaining = _remaining_post_slots_today()
        if not unposted:
            logger.info("No unposted opportunities.")
            return
        if total <= 0:
            logger.info("No post times configured.")
            return
        if remaining <= 0:
            remaining = total
        batch_size = math.ceil(len(unposted) / remaining)
        batch = unposted[:batch_size]
        logger.info("Posting %d/%d opportunities (%d per %d remaining slot(s))", len(batch), len(unposted), batch_size, remaining)
        sent = _post_batch(batch)
        with _telegram_failures_lock:
            if sent == 0 and batch:
                _telegram_failures += 1
                logger.warning("Post batch returned 0 sent (%d/%d consecutive failures)", _telegram_failures, _TELEGRAM_CIRCUIT_BREAKER_MAX)
            else:
                _telegram_failures = 0
        logger.info(f"Posted {sent}/{len(batch)} opportunities")
    except Exception as e:
        logger.error(f"Post task failed: {e}", exc_info=True)
        sentry_sdk.capture_exception(e)
        with _telegram_failures_lock:
            _telegram_failures += 1

def start_scheduler(shutdown: threading.Event | None = None):
    reload_schedules()
    _catch_up_scrapes()
    run_post()
    check_counter = 0
    while not (shutdown and shutdown.is_set()):
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
