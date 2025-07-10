from datetime import datetime, timedelta
from os import getenv
from typing import List
from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.exc import IntegrityError

# Load DB connection from env (e.g. postgresql://user:pass@localhost/dbname)
DATABASE_URL = getenv("DATABASE_URL")

engine = create_engine(DATABASE_URL)
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
    Base.metadata.create_all(bind=engine)

def opportunity_exists(title: str, link: str) -> bool:
    db = SessionLocal()
    exists = db.query(Opportunity).filter_by(title=title, link=link).first() is not None
    db.close()
    return exists

def save_opportunity(opportunity: dict):
    db = SessionLocal()
    opp = Opportunity(
        title=opportunity['title'],
        link=opportunity['link'],
        description=opportunity['description'],
        deadline=opportunity['deadline'],
        thumbnail=opportunity['thumbnail'],
        tags=', '.join(opportunity['tags'])
    )
    try:
        db.add(opp)
        db.commit()
    except IntegrityError:
        db.rollback()
        return False
    finally:
        db.close()
    return True

def update_posted_status(opportunity_id: int):
    db = SessionLocal()
    db.query(Opportunity).filter_by(id=opportunity_id).update({"posted_to_telegram": True})
    db.commit()
    db.close()

def get_unposted_opportunities() -> List[dict]:
    db = SessionLocal()
    results = db.query(Opportunity).filter_by(posted_to_telegram=False).all()
    db.close()
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

def get_all_opportunities() -> List[dict]:
    db = SessionLocal()
    results = db.query(Opportunity).order_by(Opportunity.created_at.desc()).all()
    db.close()
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

def delete_old_entries():
    db = SessionLocal()
    cutoff_date = datetime.utcnow() - timedelta(days=30)
    deleted = db.query(Opportunity).filter(Opportunity.created_at < cutoff_date).delete()
    db.commit()
    db.close()
    print(f"\U0001f9f9 Deleted {deleted} old opportunities (older than 30 days).")
