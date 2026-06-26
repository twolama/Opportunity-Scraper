import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Use in-memory SQLite for tests
TEST_DATABASE_URL = "sqlite:///:memory:"

os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["BOT_OWNER_ID"] = "12345"
os.environ["TELEGRAM_API_URL"] = "https://api.telegram.org/botTEST"
os.environ["USE_POLLING"] = "false"
os.environ["RUN_SCHEDULER"] = "false"

from app.database import Base, SessionLocal, init_db


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    engine = create_engine(TEST_DATABASE_URL)
    Base.metadata.create_all(bind=engine)
    original_session = SessionLocal
    import app.database as dbmod
    dbmod.engine = engine
    dbmod.SessionLocal = sessionmaker(bind=engine)
    init_db()
    yield
    dbmod.SessionLocal = original_session


@pytest.fixture
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
