#!/usr/bin/env python3
"""Run required privacy acceptance in newly allocated, guarded test resources."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/api"))

from app.evaluation.disposable import assert_disposable_database


def main() -> None:
    url = os.environ["DATABASE_URL"]
    control_name = assert_disposable_database(url)
    tag = uuid4().hex
    name = f"prufai_eval2_privacy_{tag}"
    base = make_url(url)
    database = base.set(database=name).render_as_string(hide_password=False)
    assert_disposable_database(database, name)
    with psycopg.connect(
        base.set(drivername="postgresql").render_as_string(hide_password=False),
        autocommit=True,
    ) as connection:
        if connection.execute("SELECT current_database(), current_user").fetchone() != (
            control_name,
            "prufai",
        ):
            raise SystemExit("Disposable control identity mismatch")
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    if not all(
        os.environ.get(key)
        for key in (
            "OBJECT_STORAGE_ENDPOINT",
            "OBJECT_STORAGE_ACCESS_KEY",
            "OBJECT_STORAGE_SECRET_KEY",
        )
    ):
        raise SystemExit("Private MinIO endpoint and credentials are required")
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "services/api"),
        "ENVIRONMENT": "test",
        "DATABASE_URL": database,
        "POSTGRES_INTEGRATION_URL": database,
        "PRUFAI_DISPOSABLE_DATABASE": name,
        "OBJECT_STORAGE_BACKEND": "s3",
        "OBJECT_STORAGE_BUCKET": f"prufai-privacy-{tag}",
        "PRUFAI_REQUIRE_PRIVACY_INTEGRATION": "1",
        "PRUFAI_REQUIRE_POSTGRES_INTEGRATION": "1",
        "PRUFAI_REQUIRE_OBJECT_STORAGE_INTEGRATION": "1",
    }

    def run(*command: str, cwd: Path = ROOT) -> None:
        subprocess.run([sys.executable, *command], env=env, cwd=cwd, check=True)

    run("scripts/migrate-evaluation-disposable.py")
    run("scripts/initialize-audio-storage.py")
    result = sys.argv[1]
    run(
        "-m",
        "pytest",
        "tests/test_privacy_postgresql_minio_integration.py",
        "-v",
        f"--junitxml={result}",
        cwd=ROOT / "services/api",
    )
    run(
        "scripts/check-required-evaluation-junit.py",
        result,
        "test_privacy_postgresql_minio_integration",
    )


if __name__ == "__main__":
    main()
