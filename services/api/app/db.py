from collections.abc import Generator

from sqlalchemy import Engine, create_engine

from app.config import Settings


def create_db_engine(settings: Settings) -> Engine:
    """Create the SQLAlchemy engine without opening a connection at import time."""

    return create_engine(settings.database_url, pool_pre_ping=True)


def get_db_engine(settings: Settings) -> Generator[Engine, None, None]:
    """Dependency seam for future request-scoped database work."""

    engine = create_db_engine(settings)
    try:
        yield engine
    finally:
        engine.dispose()
