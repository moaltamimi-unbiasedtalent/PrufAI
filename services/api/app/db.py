from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import Base


def create_db_engine(settings: Settings) -> Engine:
    """Create the SQLAlchemy engine without opening a connection at import time."""

    return create_engine(settings.database_url, pool_pre_ping=True)


def create_session_factory(settings: Settings) -> sessionmaker[Session]:
    return sessionmaker(bind=create_db_engine(settings), autoflush=False, expire_on_commit=False)


def get_db_engine(settings: Settings) -> Generator[Engine, None, None]:
    """Dependency seam for future request-scoped database work."""

    engine = create_db_engine(settings)
    try:
        yield engine
    finally:
        engine.dispose()


def get_db_session(settings: Settings) -> Generator[Session, None, None]:
    """FastAPI-compatible request session dependency."""

    factory = create_session_factory(settings)
    with factory() as session:
        yield session


__all__ = ["Base", "create_db_engine", "create_session_factory", "get_db_engine", "get_db_session"]
