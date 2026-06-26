import logging
from datetime import datetime, timedelta
from os import getenv
from typing import List, Optional
import re
from sqlalchemy import Column, Integer, BigInteger, String, Text, Boolean, DateTime, func, Index, case
from sqlalchemy.orm import declarative_base
from sqlalchemy.exc import IntegrityError

from app.db import engine, SessionLocal, get_session

_logger = logging.getLogger(__name__)

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
        Index("idx_posted_created", "posted_to_telegram", "created_at"),
        Index("idx_tags", "tags"),
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
    time_str = Column(String(5), nullable=False)
    schedule_type = Column(String(10), nullable=False, default="scrape")
    created_at = Column(DateTime, default=datetime.utcnow)


class PendingAdmin(Base):
    __tablename__ = "pending_admins"

    user_id = Column(BigInteger, primary_key=True)
    name = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class InviteToken(Base):
    __tablename__ = "invite_tokens"

    token = Column(String, primary_key=True)
    owner_id = Column(BigInteger, nullable=False)
    used = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class PendingScheduleInput(Base):
    __tablename__ = "pending_schedule_input"

    user_id = Column(BigInteger, primary_key=True)
    input_type = Column(String(10), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


def get_schedule_times(schedule_type: str = "scrape") -> list[str]:
    with get_session() as db:
        rows = db.query(ScheduleTime).filter(ScheduleTime.schedule_type == schedule_type).order_by(ScheduleTime.time_str).all()
        return [r.time_str for r in rows]


def add_schedule_time(time_str: str, schedule_type: str = "scrape") -> bool:
    try:
        with get_session() as db:
            existing = db.query(ScheduleTime).filter(
                ScheduleTime.time_str == time_str,
                ScheduleTime.schedule_type == schedule_type
            ).first()
            if existing:
                return False
            db.add(ScheduleTime(time_str=time_str, schedule_type=schedule_type))
            return True
    except IntegrityError:
        return False


def remove_schedule_time(time_str: str, schedule_type: str = "scrape") -> bool:
    try:
        with get_session() as db:
            row = db.query(ScheduleTime).filter(
                ScheduleTime.time_str == time_str,
                ScheduleTime.schedule_type == schedule_type
            ).first()
            if not row:
                return False
            db.delete(row)
            return True
    except Exception:
        return False


_TIME_RE = re.compile(r'^(\d{1,2}):(\d{2})(?:\s*([ap]\.?m\.?))?$', re.IGNORECASE)


def parse_time_12h(text: str) -> str | None:
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
    try:
        h, m = map(int, time_str.split(":"))
        ampm = "AM" if h < 12 else "PM"
        h12 = h % 12
        if h12 == 0:
            h12 = 12
        return f"{h12}:{m:02d} {ampm}"
    except Exception:
        return time_str


def add_pending_admin(user_id: int, name: str) -> bool:
    try:
        with get_session() as db:
            existing = db.query(PendingAdmin).filter(PendingAdmin.user_id == user_id).first()
            if existing:
                return False
            db.add(PendingAdmin(user_id=user_id, name=name))
            return True
    except IntegrityError:
        return False


def remove_pending_admin(user_id: int) -> str | None:
    try:
        with get_session() as db:
            row = db.query(PendingAdmin).filter(PendingAdmin.user_id == user_id).first()
            if not row:
                return None
            name = row.name
            db.delete(row)
            return name
    except Exception:
        return None


def get_pending_admins() -> list[dict]:
    with get_session() as db:
        rows = db.query(PendingAdmin).order_by(PendingAdmin.created_at).all()
        return [{"user_id": r.user_id, "name": r.name} for r in rows]


def add_invite_token(token: str, owner_id: int) -> bool:
    try:
        with get_session() as db:
            db.add(InviteToken(token=token, owner_id=owner_id))
            return True
    except IntegrityError:
        return False


def consume_invite_token(token: str) -> int | None:
    try:
        with get_session() as db:
            row = db.query(InviteToken).filter(
                InviteToken.token == token,
                InviteToken.used == False,
            ).first()
            if not row:
                return None
            now = datetime.utcnow()
            age = now - row.created_at
            if age.total_seconds() > 86400:
                row.used = True
                return None
            row.used = True
            return row.owner_id
    except Exception:
        return None


def set_pending_schedule_input(user_id: int, input_type: str) -> None:
    try:
        with get_session() as db:
            existing = db.query(PendingScheduleInput).filter(PendingScheduleInput.user_id == user_id).first()
            if existing:
                existing.input_type = input_type
            else:
                db.add(PendingScheduleInput(user_id=user_id, input_type=input_type))
    except Exception:
        _logger.warning("Failed to set pending schedule input for user %s", user_id, exc_info=True)


def pop_pending_schedule_input(user_id: int) -> str | None:
    try:
        with get_session() as db:
            row = db.query(PendingScheduleInput).filter(PendingScheduleInput.user_id == user_id).first()
            if not row:
                return None
            input_type = row.input_type
            db.delete(row)
            return input_type
    except Exception:
        return None


def init_db():
    Base.metadata.create_all(bind=engine)
    _run_alembic_migrations()
    owner_id = getenv("BOT_OWNER_ID")
    if owner_id:
        try:
            with get_session() as db:
                owner = int(owner_id)
                existing = db.query(Admin).filter(Admin.user_id == owner).first()
                if not existing:
                    db.add(Admin(user_id=owner, name="Owner", added_by=owner))
                    print(f"[Admin] Owner {owner} registered as admin")
        except Exception:
            _logger.warning("Failed to register owner as admin", exc_info=True)
    try:
        with get_session() as db:
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
    except Exception:
        _logger.warning("Failed to seed default schedule times", exc_info=True)


def _run_alembic_migrations():
    try:
        from alembic.config import Config as AlembicConfig
        from alembic import command as alembic_cmd
        import os
        ini_path = os.path.join(os.path.dirname(__file__), "..", "alembic.ini")
        if os.path.isfile(ini_path):
            alembic_cfg = AlembicConfig(ini_path)
            alembic_cmd.upgrade(alembic_cfg, "head")
    except Exception:
        _logger.warning("Alembic migration failed (non-fatal)", exc_info=True)


def is_admin(user_id: int) -> bool:
    with get_session() as db:
        return db.query(Admin).filter(Admin.user_id == user_id).first() is not None


def add_admin(user_id: int, added_by: int, name: str = "") -> bool:
    try:
        with get_session() as db:
            existing = db.query(Admin).filter(Admin.user_id == user_id).first()
            if existing:
                if name and existing.name != name:
                    existing.name = name
                return False
            db.add(Admin(user_id=user_id, added_by=added_by, name=name))
            return True
    except IntegrityError:
        return False


def remove_admin(user_id: int) -> bool:
    try:
        with get_session() as db:
            admin = db.query(Admin).filter(Admin.user_id == user_id).first()
            if not admin:
                return False
            db.delete(admin)
            return True
    except Exception:
        return False


def get_admins() -> List[dict]:
    with get_session() as db:
        results = db.query(Admin).order_by(Admin.created_at).all()
        return [{"user_id": a.user_id, "name": a.name, "added_by": a.added_by, "created_at": a.created_at} for a in results]


def opportunity_exists(title: str, link: str) -> bool:
    with get_session() as db:
        return db.query(Opportunity).filter_by(link=link).first() is not None


def opportunities_exist(links: list[str]) -> set[str]:
    with get_session() as db:
        results = db.query(Opportunity.link).filter(Opportunity.link.in_(links)).all()
        return {r[0] for r in results}


def save_opportunity(opportunity: dict, scraped_date: Optional[str] = None) -> Optional[int]:
    if scraped_date:
        try:
            dt = datetime.strptime(scraped_date.replace("/", "-"), "%Y-%m-%d")
        except ValueError:
            dt = datetime.utcnow()
    else:
        dt = datetime.utcnow()
    try:
        with get_session() as db:
            opp = Opportunity(
                title=opportunity['title'],
                link=opportunity['link'],
                description=opportunity.get('description', ''),
                deadline=opportunity.get('deadline', ''),
                thumbnail=opportunity.get('thumbnail', ''),
                tags=', '.join(opportunity.get('tags', [])),
                created_at=dt
            )
            db.add(opp)
            db.flush()
            opp_id = opp.id
        return opp_id
    except IntegrityError:
        return None


def update_posted_status(opportunity_id: int):
    with get_session() as db:
        db.query(Opportunity).filter_by(id=opportunity_id).update({"posted_to_telegram": True})


def get_opportunity_by_id(opportunity_id: int) -> Optional[dict]:
    with get_session() as db:
        opp = db.query(Opportunity).filter_by(id=opportunity_id).first()
        if opp:
            return opportunity_to_dict(opp)
        return None


def update_opportunity(opportunity_id: int, data: dict) -> bool:
    try:
        with get_session() as db:
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
                            _logger.warning("Invalid date format for created_at: %s", val)
                    else:
                        setattr(opp, key, val)
            return True
    except Exception:
        return False


def delete_opportunity(opportunity_id: int) -> bool:
    try:
        with get_session() as db:
            opp = db.query(Opportunity).filter_by(id=opportunity_id).first()
            if not opp:
                return False
            db.delete(opp)
            return True
    except Exception:
        return False


def get_unposted_opportunities() -> List[dict]:
    with get_session() as db:
        results = db.query(Opportunity).filter_by(posted_to_telegram=False).all()
        return [opportunity_to_dict(opp, include_status=False) for opp in results]


def get_all_opportunities() -> List[dict]:
    with get_session() as db:
        results = db.query(Opportunity).order_by(Opportunity.created_at.desc()).all()
        print(f"Fetched {len(results)} opportunities from DB")
        return [opportunity_to_dict(opp) for opp in results]


def opportunity_to_dict(opp, include_status: bool = True) -> dict:
    d = {
        "id": opp.id,
        "title": opp.title,
        "link": opp.link,
        "description": opp.description,
        "deadline": opp.deadline,
        "thumbnail": opp.thumbnail,
        "tags": opp.tags.split(", ") if opp.tags else [],
    }
    if include_status:
        d["created_at"] = opp.created_at
        d["posted_to_telegram"] = opp.posted_to_telegram
    return d


def _date_range(date_str: str) -> tuple[datetime, datetime]:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return dt, dt + timedelta(days=1)


def get_unposted_by_date(date_str: str) -> List[dict]:
    with get_session() as db:
        start, end = _date_range(date_str)
        results = db.query(Opportunity).filter(
            Opportunity.posted_to_telegram == False,
            Opportunity.created_at >= start,
            Opportunity.created_at < end,
        ).order_by(Opportunity.created_at.desc()).all()
        return [opportunity_to_dict(o) for o in results]


def get_posted_by_date(date_str: str) -> List[dict]:
    with get_session() as db:
        start, end = _date_range(date_str)
        results = db.query(Opportunity).filter(
            Opportunity.posted_to_telegram == True,
            Opportunity.created_at >= start,
            Opportunity.created_at < end,
        ).order_by(Opportunity.created_at.desc()).all()
        return [opportunity_to_dict(o) for o in results]


def get_stats_from_db() -> dict:
    with get_session() as db:
        now = datetime.utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timedelta(days=today_start.weekday())
        month_start = today_start.replace(day=1)

        status_counts = dict(
            db.query(Opportunity.posted_to_telegram, func.count(Opportunity.id))
            .group_by(Opportunity.posted_to_telegram)
            .all()
        )
        posted = status_counts.get(True, 0)
        unposted = status_counts.get(False, 0)
        total = posted + unposted

        row = db.query(
            func.min(Opportunity.created_at),
            func.max(Opportunity.created_at).filter(Opportunity.posted_to_telegram == True),
            func.sum(case((Opportunity.created_at >= today_start, 1), else_=0)),
            func.sum(case((Opportunity.created_at >= week_start, 1), else_=0)),
            func.sum(case((Opportunity.created_at >= month_start, 1), else_=0)),
        ).first()
        oldest = row[0]
        last_posted = row[1]
        today_count = row[2] or 0
        week_count = row[3] or 0
        month_count = row[4] or 0

        from collections import Counter
        tag_counter: Counter = Counter()
        TAG_LIMIT = 10000
        all_tags = db.query(Opportunity.tags).filter(
            Opportunity.tags.isnot(None), Opportunity.tags != ""
        ).limit(TAG_LIMIT).all()
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
            "today": int(today_count),
            "week": int(week_count),
            "month": int(month_count),
            "last_posted": last_posted.strftime("%Y-%m-%d %H:%M") if last_posted else "N/A",
            "oldest": oldest.strftime("%Y-%m-%d") if oldest else "N/A",
            "top_tags": top_tags,
        }


def search_opportunities(keyword: str, skip: int = 0, limit: int = 10, posted: Optional[bool] = None) -> dict:
    with get_session() as db:
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
            "results": [opportunity_to_dict(o) for o in results],
            "total": total,
            "offset": skip,
            "limit": limit
        }


def bulk_save_opportunities(opportunities: list[dict], scraped_date: Optional[str] = None) -> int:
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
        if not rows:
            return 0
        existing_links = opportunities_exist([r["link"] for r in rows])
        new_rows = [r for r in rows if r["link"] not in existing_links]
        if not new_rows:
            return 0
        with get_session() as db:
            db.execute(Opportunity.__table__.insert(), new_rows)
        return len(new_rows)
    except Exception:
        return 0


def delete_old_entries(days: Optional[int] = None):
    from app.config import DELETE_OLDER_THAN_DAYS as _cfg_days
    if days is None:
        days = _cfg_days
    with get_session() as db:
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        deleted = db.query(Opportunity).filter(Opportunity.created_at < cutoff_date).delete()
        print(f"[Clean] Deleted {deleted} old opportunities (older than {days} days).")
