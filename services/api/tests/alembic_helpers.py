from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def repository_alembic_head() -> str:
    """Return the single current head declared by this checkout's migrations."""
    service_root = Path(__file__).resolve().parents[1]
    config = Config(str(service_root / "alembic.ini"))
    return ScriptDirectory.from_config(config).get_current_head()
