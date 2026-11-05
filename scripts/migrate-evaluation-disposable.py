#!/usr/bin/env python3
"""Migrate only an explicitly identified owned acceptance database, never defaults."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/api"))
from app.evaluation.disposable import assert_disposable_database


def main() -> None:
    target = os.environ.get("DATABASE_URL", "")
    if not target or target != os.environ.get("POSTGRES_INTEGRATION_URL", target):
        raise SystemExit("Explicit identical disposable database URLs are required.")
    name = assert_disposable_database(target)
    # Imports/settings that could have a default are intentionally after refusal.
    from sqlalchemy import create_engine, text

    engine = create_engine(target)
    with engine.connect() as connection:
        if connection.scalar(text("SELECT current_database()")) != name:
            raise SystemExit(
                "Connected database does not match the authorized disposable target."
            )
    engine.dispose()
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(ROOT / "services/api/alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "services/api/alembic"))
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise SystemExit("A single dynamic Alembic head is required.")
    print("Dynamic Alembic head:", heads[0], flush=True)
    command.upgrade(config, "head")
    with engine.connect() as connection:
        actual = list(
            connection.scalars(text("SELECT version_num FROM alembic_version"))
        )
        if actual != heads:
            raise SystemExit("Database is not at the current repository head.")
    engine.dispose()
    print("Disposable database migration verified.", flush=True)


if __name__ == "__main__":
    main()
