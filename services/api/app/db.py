from collections.abc import Generator

from fastapi import Depends
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.models import Base


def create_db_engine(settings: Settings) -> Engine:
    """Create the SQLAlchemy engine without opening a connection at import time."""

    return create_engine(settings.database_url, pool_pre_ping=True)


def create_session_factory(settings: Settings) -> sessionmaker[Session]:
    return sessionmaker(bind=create_db_engine(settings), autoflush=False, expire_on_commit=False)


def get_db_engine(settings: Settings = Depends(get_settings)) -> Generator[Engine, None, None]:  # noqa: B008
    """Dependency seam for future request-scoped database work."""

    engine = create_db_engine(settings)
    try:
        yield engine
    finally:
        engine.dispose()


def get_db_session(settings: Settings = Depends(get_settings)) -> Generator[Session, None, None]:  # noqa: B008
    """FastAPI-compatible request session dependency."""

    factory = create_session_factory(settings)
    with factory() as session:
        yield session


__all__ = ["Base", "create_db_engine", "create_session_factory", "get_db_engine", "get_db_session"]
