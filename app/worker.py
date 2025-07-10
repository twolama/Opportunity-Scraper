from app.scheduler import start_scheduler
from app.database import init_db

if __name__ == "__main__":
    init_db()
    start_scheduler()
