import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

TEST_DATABASE_URL = "sqlite:///:memory:"

os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["BOT_OWNER_ID"] = "12345"
os.environ["TELEGRAM_API_URL"] = "https://api.telegram.org/botTEST"
os.environ["USE_POLLING"] = "false"
os.environ["RUN_SCHEDULER"] = "false"
os.environ["API_KEY"] = "test-api-key-123"
os.environ["TESTING"] = "true"

from app.database import Base, engine as _orig_engine, SessionLocal as _orig_SessionLocal, init_db
import app.db as db_module


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    db_module.engine = engine
    db_module.SessionLocal = sessionmaker(bind=engine)
    import app.database as _db_mod
    _db_mod.engine = engine
    _db_mod.SessionLocal = db_module.SessionLocal
    init_db()
    yield


@pytest.fixture
def db_session():
    session = db_module.SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
