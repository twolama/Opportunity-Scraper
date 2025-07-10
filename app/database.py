from datetime import datetime, timedelta
from os import getenv
from typing import List, Optional
from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, DateTime, and_
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.exc import IntegrityError
from dotenv import load_dotenv

load_dotenv()  # loads env vars from .env file

# Load DB connection from env
DATABASE_URL = getenv("DATABASE_URL")

# Create engine & session factory
engine = create_engine(DATABASE_URL, echo=False, future=True)
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

def init_db():
    """Create tables in DB if they don't exist"""
    Base.metadata.create_all(bind=engine)

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
        print(f"\U0001f9f9 Deleted {deleted} old opportunities (older than {days} days).")
    finally:
        db.close()
