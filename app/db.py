import os
import logging
from contextlib import contextmanager
from os import getenv

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.exc import SQLAlchemyError

_logger = logging.getLogger(__name__)
from sqlalchemy.orm import sessionmaker, declarative_base

load_dotenv()

DATABASE_URL = getenv("DATABASE_URL")

_connect_args = {"keepalives_idle": 60, "keepalives_interval": 10, "keepalives_count": 5}

if DATABASE_URL and ("supabase" in DATABASE_URL.lower() or getenv("DB_SSL", "false").lower() == "true"):
    _connect_args["sslmode"] = "require"

_is_sqlite = DATABASE_URL and DATABASE_URL.startswith("sqlite")
if _is_sqlite:
    engine = create_engine(DATABASE_URL, echo=False, future=True)
else:
    engine = create_engine(
        DATABASE_URL,
        echo=False,
        future=True,
        pool_pre_ping=True,
        pool_recycle=300,
        pool_size=int(getenv("DB_POOL_SIZE", "5")),
        max_overflow=int(getenv("DB_MAX_OVERFLOW", "5")),
        connect_args=_connect_args,
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


@contextmanager
def get_session():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except SQLAlchemyError:
        _logger.warning("DB session rollback due to error", exc_info=True)
        db.rollback()
        raise
    finally:
        db.close()
