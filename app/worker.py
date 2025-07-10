import logging
from app.scheduler import start_scheduler, run_daily_tasks
from app.database import init_db

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    init_db()
    run_daily_tasks()  # optionally run once on start
    start_scheduler()
