from datetime import datetime, timedelta
from os import getenv
from typing import List, Optional
import re
from sqlalchemy import create_engine, Column, Integer, BigInteger, String, Text, Boolean, DateTime, and_, text, func, Index
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.exc import IntegrityError
from dotenv import load_dotenv

load_dotenv()  # loads env vars from .env file

# Load DB connection from env
DATABASE_URL = getenv("DATABASE_URL")

# Build connect args
_connect_args = {"keepalives_idle": 60, "keepalives_interval": 10, "keepalives_count": 5}

# Supabase / cloud Postgres requires SSL
if DATABASE_URL and ("supabase" in DATABASE_URL.lower() or getenv("DB_SSL", "false").lower() == "true"):
    _connect_args["sslmode"] = "require"

# Create engine & session factory
engine = create_engine(
    DATABASE_URL, 
    echo=False, 
    future=True, 
    pool_pre_ping=True,
    pool_recycle=300,
    pool_size=int(getenv("DB_POOL_SIZE", "5")),
    max_overflow=int(getenv("DB_MAX_OVERFLOW", "5")),
    connect_args=_connect_args
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class Opportunity(Base):
    __tablename__ = "opportunities"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    link = Column(String, nullable=False, unique=True)
    description = Column(Text)
    deadline = Column(String)
    thumbnail = Column(String)
    tags = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    posted_to_telegram = Column(Boolean, default=False)

    __table_args__ = (
        Index("idx_posted_to_telegram", "posted_to_telegram"),
        Index("idx_created_at", "created_at"),
    )

class Admin(Base):
    __tablename__ = "bot_admins"

    user_id = Column(BigInteger, primary_key=True)
    name = Column(String, default="")
    added_by = Column(BigInteger, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class ScheduleTime(Base):
    __tablename__ = "schedule_times"

    id = Column(Integer, primary_key=True, index=True)
    time_str = Column(String(5), nullable=False)  # "HH:MM" in UTC
    schedule_type = Column(String(10), nullable=False, default="scrape")  # "scrape" or "post"
    created_at = Column(DateTime, default=datetime.utcnow)

def get_schedule_times(schedule_type: str = "scrape") -> list[str]:
    db = SessionLocal()
    try:
        rows = db.query(ScheduleTime).filter(ScheduleTime.schedule_type == schedule_type).order_by(ScheduleTime.time_str).all()
        return [r.time_str for r in rows]
    finally:
        db.close()

def add_schedule_time(time_str: str, schedule_type: str = "scrape") -> bool:
    db = SessionLocal()
    try:
        existing = db.query(ScheduleTime).filter(
            ScheduleTime.time_str == time_str,
            ScheduleTime.schedule_type == schedule_type
        ).first()
        if existing:
            return False
        db.add(ScheduleTime(time_str=time_str, schedule_type=schedule_type))
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False
    finally:
        db.close()

def remove_schedule_time(time_str: str, schedule_type: str = "scrape") -> bool:
    db = SessionLocal()
    try:
        row = db.query(ScheduleTime).filter(
            ScheduleTime.time_str == time_str,
            ScheduleTime.schedule_type == schedule_type
        ).first()
        if not row:
            return False
        db.delete(row)
        db.commit()
        return True
    except Exception:
        db.rollback()
        return False
    finally:
        db.close()

_TIME_RE = re.compile(r'^(\d{1,2}):(\d{2})(?:\s*([ap]\.?m\.?))?$', re.IGNORECASE)

def parse_time_12h(text: str) -> str | None:
    """Convert '6:30 AM' or '06:30' (24h) to '06:30' (24h UTC). Returns None if invalid."""
    m = _TIME_RE.match(text.strip())
    if not m:
        return None
    hour, minute, ampm = int(m.group(1)), m.group(2), m.group(3)
    if ampm:
        ampm = ampm.lower().replace(".", "")
        if hour > 12 or hour < 1:
            return None
        if ampm == "pm" and hour != 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
    else:
        if hour > 23:
            return None
    return f"{hour:02d}:{minute}"

def format_time_12h(time_str: str) -> str:
    """Convert '16:59' to '4:59 PM'."""
    try:
        h, m = map(int, time_str.split(":"))
        ampm = "AM" if h < 12 else "PM"
        h12 = h % 12
        if h12 == 0:
            h12 = 12
        return f"{h12}:{m:02d} {ampm}"
    except Exception:
        return time_str

def init_db():
    """Create tables in DB if they don't exist"""
    Base.metadata.create_all(bind=engine)
    # Ensure BOT_OWNER_ID is always an admin
    owner_id = getenv("BOT_OWNER_ID")
    if owner_id:
        try:
            db = SessionLocal()
            owner = int(owner_id)
            existing = db.query(Admin).filter(Admin.user_id == owner).first()
            if not existing:
                db.add(Admin(user_id=owner, name="Owner", added_by=owner))
                db.commit()
                print(f"[Admin] Owner {owner} registered as admin")
        except Exception:
            pass
        finally:
            db.close()
    # Migration: add name column if missing
    try:
        db = SessionLocal()
        db.execute(text("ALTER TABLE bot_admins ADD COLUMN name VARCHAR DEFAULT ''"))
        db.commit()
        print("[DB] Added name column to bot_admins")
    except Exception:
        pass
    finally:
        db.close()
    # Migration: add indexes if missing
    try:
        db = SessionLocal()
        db.execute(text("CREATE INDEX IF NOT EXISTS idx_posted_to_telegram ON opportunities (posted_to_telegram)"))
        db.execute(text("CREATE INDEX IF NOT EXISTS idx_created_at ON opportunities (created_at)"))
        db.commit()
        print("[DB] Indexes created")
    except Exception:
        pass
    finally:
        db.close()
    # Migration: unique constraint on link (ignore if already exists)
    try:
        db = SessionLocal()
        db.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_link ON opportunities (link)"))
        db.commit()
        print("[DB] Unique index on link created")
    except Exception:
        pass
    finally:
        db.close()
    # Migration: add schedule_type column + unique index
    try:
        db = SessionLocal()
        db.execute(text("ALTER TABLE schedule_times ADD COLUMN IF NOT EXISTS schedule_type VARCHAR(10) DEFAULT 'scrape'"))
        db.execute(text("UPDATE schedule_times SET schedule_type = 'scrape' WHERE schedule_type IS NULL"))
        db.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_schedule ON schedule_times (time_str, schedule_type)"))
        db.commit()
        print("[DB] Schedule type migration done")
    except Exception:
        pass
    finally:
        db.close()
    # Seed default schedule times if table is empty
    try:
        db = SessionLocal()
        scrape_count = db.query(ScheduleTime).filter(ScheduleTime.schedule_type == "scrape").count()
        post_count = db.query(ScheduleTime).filter(ScheduleTime.schedule_type == "post").count()
        if scrape_count == 0:
            for t in ["04:59", "10:59", "16:59"]:
                db.add(ScheduleTime(time_str=t, schedule_type="scrape"))
            print(f"[DB] Seeded default scrape times")
        if post_count == 0:
            for t in ["08:00", "14:00", "20:00"]:
                db.add(ScheduleTime(time_str=t, schedule_type="post"))
            print(f"[DB] Seeded default post times")
        db.commit()
    except Exception:
        pass
    finally:
        db.close()

def is_admin(user_id: int) -> bool:
    db = SessionLocal()
    try:
        return db.query(Admin).filter(Admin.user_id == user_id).first() is not None
    finally:
        db.close()

def add_admin(user_id: int, added_by: int, name: str = "") -> bool:
    db = SessionLocal()
    try:
        existing = db.query(Admin).filter(Admin.user_id == user_id).first()
        if existing:
            if name and existing.name != name:
                existing.name = name
                db.commit()
            return False
        db.add(Admin(user_id=user_id, added_by=added_by, name=name))
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False
    finally:
        db.close()

def remove_admin(user_id: int) -> bool:
    db = SessionLocal()
    try:
        admin = db.query(Admin).filter(Admin.user_id == user_id).first()
        if not admin:
            return False
        db.delete(admin)
        db.commit()
        return True
    except Exception:
        db.rollback()
        return False
    finally:
        db.close()

def get_admins() -> List[dict]:
    db = SessionLocal()
    try:
        results = db.query(Admin).order_by(Admin.created_at).all()
        return [{"user_id": a.user_id, "name": a.name, "added_by": a.added_by, "created_at": a.created_at} for a in results]
    finally:
        db.close()

def opportunity_exists(title: str, link: str) -> bool:
    db = SessionLocal()
    try:
        return db.query(Opportunity).filter_by(link=link).first() is not None
    finally:
        db.close()

def save_opportunity(opportunity: dict, scraped_date: Optional[str] = None) -> bool:
    db = SessionLocal()
    if scraped_date:
        try:
            dt = datetime.strptime(scraped_date.replace("/", "-"), "%Y-%m-%d")
        except ValueError:
            dt = datetime.utcnow()
    else:
        dt = datetime.utcnow()
    opp = Opportunity(
        title=opportunity['title'],
        link=opportunity['link'],
        description=opportunity.get('description', ''),
        deadline=opportunity.get('deadline', ''),
        thumbnail=opportunity.get('thumbnail', ''),
        tags=', '.join(opportunity.get('tags', [])),
        created_at=dt
    )
    try:
        db.add(opp)
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False
    finally:
        db.close()

def update_posted_status(opportunity_id: int):
    db = SessionLocal()
    try:
        db.query(Opportunity).filter_by(id=opportunity_id).update({"posted_to_telegram": True})
        db.commit()
    finally:
        db.close()

def get_opportunity_by_id(opportunity_id: int) -> Optional[dict]:
    db = SessionLocal()
    try:
        opp = db.query(Opportunity).filter_by(id=opportunity_id).first()
        if opp:
            return {
                "id": opp.id,
                "title": opp.title,
                "link": opp.link,
                "description": opp.description,
                "deadline": opp.deadline,
                "thumbnail": opp.thumbnail,
                "tags": opp.tags.split(", ") if opp.tags else [],
                "created_at": opp.created_at,
                "posted_to_telegram": opp.posted_to_telegram,
            }
        return None
    finally:
        db.close()

def update_opportunity(opportunity_id: int, data: dict) -> bool:
    db = SessionLocal()
    try:
        opp = db.query(Opportunity).filter_by(id=opportunity_id).first()
        if not opp:
            return False
        for key, val in data.items():
            if hasattr(opp, key) and val is not None:
                if key == "tags" and isinstance(val, list):
                    setattr(opp, key, ", ".join(val))
                elif key == "created_at" and isinstance(val, str):
                    try:
                        setattr(opp, key, datetime.strptime(val, "%Y-%m-%d"))
                    except ValueError:
                        pass
                else:
                    setattr(opp, key, val)
        db.commit()
        return True
    except Exception:
        db.rollback()
        return False
    finally:
        db.close()

def delete_opportunity(opportunity_id: int) -> bool:
    db = SessionLocal()
    try:
        opp = db.query(Opportunity).filter_by(id=opportunity_id).first()
        if not opp:
            return False
        db.delete(opp)
        db.commit()
        return True
    except Exception:
        db.rollback()
        return False
    finally:
        db.close()

def get_unposted_opportunities() -> List[dict]:
    db = SessionLocal()
    try:
        results = db.query(Opportunity).filter_by(posted_to_telegram=False).all()
        return [
            {
                "id": opp.id,
                "title": opp.title,
                "link": opp.link,
                "description": opp.description,
                "deadline": opp.deadline,
                "thumbnail": opp.thumbnail,
                "tags": opp.tags.split(", ") if opp.tags else []
            }
            for opp in results
        ]
    finally:
        db.close()

def get_all_opportunities() -> List[dict]:
    db = SessionLocal()
    try:
        results = db.query(Opportunity).order_by(Opportunity.created_at.desc()).all()
        print(f"Fetched {len(results)} opportunities from DB")
        return [
            {
                "id": opp.id,
                "title": opp.title,
                "link": opp.link,
                "description": opp.description,
                "deadline": opp.deadline,
                "thumbnail": opp.thumbnail,
                "tags": opp.tags.split(", ") if opp.tags else [],
                "created_at": opp.created_at,
                "posted_to_telegram": opp.posted_to_telegram,
            }
            for opp in results
        ]
    finally:
        db.close()

def _format_opportunity(opp):
    return {
        "id": opp.id,
        "title": opp.title,
        "link": opp.link,
        "description": opp.description,
        "deadline": opp.deadline,
        "thumbnail": opp.thumbnail,
        "tags": opp.tags.split(", ") if opp.tags else [],
        "created_at": opp.created_at,
        "posted_to_telegram": opp.posted_to_telegram,
    }

def get_unposted_by_date(date_str: str) -> List[dict]:
    db = SessionLocal()
    try:
        results = db.query(Opportunity).filter(
            Opportunity.posted_to_telegram == False,
            func.date(Opportunity.created_at) == date_str
        ).order_by(Opportunity.created_at.desc()).all()
        return [_format_opportunity(o) for o in results]
    finally:
        db.close()

def get_posted_by_date(date_str: str) -> List[dict]:
    db = SessionLocal()
    try:
        results = db.query(Opportunity).filter(
            Opportunity.posted_to_telegram == True,
            func.date(Opportunity.created_at) == date_str
        ).order_by(Opportunity.created_at.desc()).all()
        return [_format_opportunity(o) for o in results]
    finally:
        db.close()

def get_stats_from_db() -> dict:
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timedelta(days=today_start.weekday())
        month_start = today_start.replace(day=1)

        total = db.query(func.count(Opportunity.id)).scalar() or 0
        unposted = db.query(func.count(Opportunity.id)).filter(
            Opportunity.posted_to_telegram == False
        ).scalar() or 0
        posted = db.query(func.count(Opportunity.id)).filter(
            Opportunity.posted_to_telegram == True
        ).scalar() or 0

        today_count = db.query(func.count(Opportunity.id)).filter(
            Opportunity.created_at >= today_start
        ).scalar() or 0
        week_count = db.query(func.count(Opportunity.id)).filter(
            Opportunity.created_at >= week_start
        ).scalar() or 0
        month_count = db.query(func.count(Opportunity.id)).filter(
            Opportunity.created_at >= month_start
        ).scalar() or 0

        last_posted = db.query(func.max(Opportunity.created_at)).filter(
            Opportunity.posted_to_telegram == True
        ).scalar()
        oldest = db.query(func.min(Opportunity.created_at)).scalar()

        # Top 10 tags by frequency
        all_tags = db.query(Opportunity.tags).filter(
            Opportunity.tags.isnot(None), Opportunity.tags != ""
        ).all()
        from collections import Counter
        tag_counter: Counter = Counter()
        for (tags_str,) in all_tags:
            for tag in tags_str.split(", "):
                tag = tag.strip()
                if tag:
                    tag_counter[tag] += 1
        top_tags = tag_counter.most_common(10)

        return {
            "total": total,
            "unposted": unposted,
            "posted": posted,
            "today": today_count,
            "week": week_count,
            "month": month_count,
            "last_posted": last_posted.strftime("%Y-%m-%d %H:%M") if last_posted else "N/A",
            "oldest": oldest.strftime("%Y-%m-%d") if oldest else "N/A",
            "top_tags": top_tags,
        }
    finally:
        db.close()

def search_opportunities(keyword: str, skip: int = 0, limit: int = 10, posted: Optional[bool] = None) -> dict:
    db = SessionLocal()
    try:
        q = db.query(Opportunity)
        if keyword:
            like = f"%{keyword}%"
            q = q.filter(
                (Opportunity.title.ilike(like)) |
                (Opportunity.description.ilike(like)) |
                (Opportunity.tags.ilike(like))
            )
        if posted is not None:
            q = q.filter(Opportunity.posted_to_telegram == posted)
        total = q.count()
        results = q.order_by(Opportunity.created_at.desc()).offset(skip).limit(limit).all()
        return {
            "results": [_format_opportunity(o) for o in results],
            "total": total,
            "offset": skip,
            "limit": limit
        }
    finally:
        db.close()

def bulk_save_opportunities(opportunities: list[dict], scraped_date: Optional[str] = None) -> int:
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    db = SessionLocal()
    try:
        if scraped_date:
            try:
                dt = datetime.strptime(scraped_date.replace("/", "-"), "%Y-%m-%d")
            except ValueError:
                dt = datetime.utcnow()
        else:
            dt = datetime.utcnow()
        rows = []
        for opp in opportunities:
            rows.append({
                "title": opp['title'],
                "link": opp['link'],
                "description": opp.get('description', ''),
                "deadline": opp.get('deadline', ''),
                "thumbnail": opp.get('thumbnail', ''),
                "tags": ', '.join(opp.get('tags', [])),
                "created_at": dt,
            })
        if rows:
            stmt = pg_insert(Opportunity).values(rows)
            stmt = stmt.on_conflict_do_nothing(index_elements=["link"])
            result = db.execute(stmt)
            db.commit()
            return result.rowcount
        return 0
    except Exception:
        db.rollback()
        return 0
    finally:
        db.close()

def delete_old_entries(days: Optional[int] = 30):
    db = SessionLocal()
    try:
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        deleted = db.query(Opportunity).filter(Opportunity.created_at < cutoff_date).delete()
        db.commit()
        print(f"[Clean] Deleted {deleted} old opportunities (older than {days} days).")
    finally:
        db.close()
