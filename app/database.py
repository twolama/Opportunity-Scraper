from datetime import datetime, timedelta
from os import getenv
from typing import List, Optional
from sqlalchemy import create_engine, Column, Integer, BigInteger, String, Text, Boolean, DateTime, and_, text
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.exc import IntegrityError
from dotenv import load_dotenv

load_dotenv()  # loads env vars from .env file

# Load DB connection from env
DATABASE_URL = getenv("DATABASE_URL")

# Create engine & session factory
engine = create_engine(
    DATABASE_URL, 
    echo=False, 
    future=True, 
    connect_args={"keepalives_idle": 60}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class Opportunity(Base):
    __tablename__ = "opportunities"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    link = Column(String, nullable=False)
    description = Column(Text)
    deadline = Column(String)
    thumbnail = Column(String)
    tags = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    posted_to_telegram = Column(Boolean, default=False)

class Admin(Base):
    __tablename__ = "bot_admins"

    user_id = Column(BigInteger, primary_key=True)
    name = Column(String, default="")
    added_by = Column(BigInteger, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

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
        exists = db.query(Opportunity).filter(
            and_(Opportunity.title == title, Opportunity.link == link)
        ).first() is not None
    finally:
        db.close()
    return exists

def save_opportunity(opportunity: dict) -> bool:
    db = SessionLocal()
    opp = Opportunity(
        title=opportunity['title'],
        link=opportunity['link'],
        description=opportunity.get('description', ''),
        deadline=opportunity.get('deadline', ''),
        thumbnail=opportunity.get('thumbnail', ''),
        tags=', '.join(opportunity.get('tags', []))
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

def delete_old_entries(days: Optional[int] = 30):
    db = SessionLocal()
    try:
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        deleted = db.query(Opportunity).filter(Opportunity.created_at < cutoff_date).delete()
        db.commit()
        print(f"[Clean] Deleted {deleted} old opportunities (older than {days} days).")
    finally:
        db.close()
