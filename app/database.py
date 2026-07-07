import logging
from datetime import datetime, timedelta
from os import getenv
from typing import List, Optional
import re
from sqlalchemy import Column, Integer, BigInteger, String, Text, Boolean, DateTime, func, Index, case, ForeignKey, Table
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.exc import IntegrityError

from app.db import engine, SessionLocal, get_session

_logger = logging.getLogger(__name__)

Base = declarative_base()


opportunity_tags = Table(
    "opportunity_tags", Base.metadata,
    Column("opportunity_id", Integer, ForeignKey("opportunities.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Tag(Base):
    __tablename__ = "tags"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)


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

    tags_rel = relationship("Tag", secondary=opportunity_tags, lazy="selectin")

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


class Channel(Base):
    __tablename__ = "channels"

    chat_id = Column(BigInteger, primary_key=True)
    title = Column(String, default="")
    added_by = Column(BigInteger, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)


def add_channel(chat_id: int, title: str = "", added_by: int = 0) -> bool:
    try:
        with get_session() as db:
            existing = db.query(Channel).filter_by(chat_id=chat_id).first()
            if existing:
                existing.is_active = True
                if title:
                    existing.title = title
                return True
            db.add(Channel(chat_id=chat_id, title=title, added_by=added_by))
            return True
    except Exception as e:
        _logger.warning("Failed to add channel %s: %s", chat_id, e, exc_info=True)
        return False


def remove_channel(chat_id: int) -> bool:
    try:
        with get_session() as db:
            ch = db.query(Channel).filter_by(chat_id=chat_id).first()
            if not ch:
                return False
            db.delete(ch)
            return True
    except Exception as e:
        _logger.warning("Failed to remove channel %s: %s", chat_id, e, exc_info=True)
        return False


def get_active_channels() -> list[dict]:
    try:
        with get_session() as db:
            rows = db.query(Channel).filter_by(is_active=True).order_by(Channel.created_at).all()
            return [{"chat_id": r.chat_id, "title": r.title, "added_by": r.added_by, "created_at": r.created_at} for r in rows]
    except Exception:
        _logger.warning("Failed to fetch active channels from DB", exc_info=True)
        return []


def get_channel(chat_id: int) -> Optional[dict]:
    with get_session() as db:
        r = db.query(Channel).filter_by(chat_id=chat_id).first()
        if r:
            return {"chat_id": r.chat_id, "title": r.title, "added_by": r.added_by, "created_at": r.created_at, "is_active": r.is_active}
        return None


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
                    _logger.info("Owner %s registered as admin", owner)
        except Exception:
            _logger.warning("Failed to register owner as admin", exc_info=True)
    try:
        with get_session() as db:
            scrape_count = db.query(ScheduleTime).filter(ScheduleTime.schedule_type == "scrape").count()
            post_count = db.query(ScheduleTime).filter(ScheduleTime.schedule_type == "post").count()
            if scrape_count == 0:
                for t in ["04:59", "10:59", "16:59"]:
                    db.add(ScheduleTime(time_str=t, schedule_type="scrape"))
                _logger.info("Seeded default scrape times")
            if post_count == 0:
                for t in ["08:00", "14:00", "20:00"]:
                    db.add(ScheduleTime(time_str=t, schedule_type="post"))
                _logger.info("Seeded default post times")
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


def _set_opportunity_tags(db, opp_id: int, tag_names: list[str]):
    """Associate tags with an opportunity, creating new tags as needed."""
    db.execute(opportunity_tags.delete().where(opportunity_tags.c.opportunity_id == opp_id))
    for name in tag_names:
        name = name.strip()
        if not name:
            continue
        tag = db.query(Tag).filter(Tag.name == name).first()
        if not tag:
            tag = Tag(name=name)
            db.add(tag)
            db.flush()
        db.execute(opportunity_tags.insert().values(opportunity_id=opp_id, tag_id=tag.id))


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
            _set_opportunity_tags(db, opp.id, opportunity.get('tags', []))
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
                if val is None:
                    continue
                if key == "tags" and isinstance(val, list):
                    setattr(opp, key, ", ".join(val))
                elif key == "created_at" and isinstance(val, str):
                    try:
                        setattr(opp, key, datetime.strptime(val, "%Y-%m-%d"))
                    except ValueError:
                        _logger.warning("Invalid date format for created_at: %s", val)
                elif hasattr(opp, key):
                    setattr(opp, key, val)
            db.flush()
            if "tags" in data and isinstance(data["tags"], list):
                _set_opportunity_tags(db, opp.id, data["tags"])
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
        _logger.info("Fetched %d opportunities from DB", len(results))
        return [opportunity_to_dict(opp) for opp in results]


def opportunity_to_dict(opp, include_status: bool = True) -> dict:
    d = {
        "id": opp.id,
        "title": opp.title,
        "link": opp.link,
        "description": opp.description,
        "deadline": opp.deadline,
        "thumbnail": opp.thumbnail,
        "tags": [t.name for t in opp.tags_rel] if opp.tags_rel else [],
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

        top_tags = (
            db.query(Tag.name, func.count(opportunity_tags.c.opportunity_id))
            .join(opportunity_tags, Tag.id == opportunity_tags.c.tag_id)
            .group_by(Tag.id, Tag.name)
            .order_by(func.count(opportunity_tags.c.opportunity_id).desc())
            .limit(10)
            .all()
        )
        top_tags = [(name, count) for name, count in top_tags]

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
            q = q.outerjoin(opportunity_tags, Opportunity.id == opportunity_tags.c.opportunity_id) \
                 .outerjoin(Tag, opportunity_tags.c.tag_id == Tag.id) \
                 .filter(
                     (Opportunity.title.ilike(like)) |
                     (Opportunity.description.ilike(like)) |
                     (Opportunity.tags.ilike(like)) |
                     (Tag.name.ilike(like))
                 ).distinct()
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
        existing_links = opportunities_exist([o["link"] for o in opportunities])
        new_data = []
        tag_map = []
        for opp in opportunities:
            if opp["link"] in existing_links:
                continue
            if opp["link"].startswith("https://opportunitydesk.org"):
                continue
            new_data.append({
                "title": opp['title'],
                "link": opp['link'],
                "description": opp.get('description', ''),
                "deadline": opp.get('deadline', ''),
                "thumbnail": opp.get('thumbnail', ''),
                "tags": ', '.join(opp.get('tags', [])),
                "created_at": dt,
            })
            tag_map.append(opp.get("tags", []))
        if not new_data:
            return 0
        with get_session() as db:
            from sqlalchemy import insert
            stmt = insert(Opportunity).returning(Opportunity.id)
            result = db.execute(stmt, new_data)
            for row_id, tags in zip(result.scalars().all(), tag_map):
                _set_opportunity_tags(db, row_id, tags)
        return len(new_data)
    except Exception:
        _logger.exception("bulk_save_opportunities failed")
        return 0


def delete_old_entries(days: Optional[int] = None):
    from app.config import DELETE_OLDER_THAN_DAYS as _cfg_days
    if days is None:
        days = _cfg_days
    with get_session() as db:
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        deleted = db.query(Opportunity).filter(Opportunity.created_at < cutoff_date).delete()
        _logger.info("Deleted %d old opportunities (older than %s days)", deleted, days)
