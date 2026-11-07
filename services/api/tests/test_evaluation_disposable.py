"""URL-only refusal checks never connect to any database."""

import pytest

from app.evaluation.disposable import assert_disposable_database

NAME = "prufai_eval2_unit_" + "a" * 32


@pytest.mark.parametrize(
    "url,expected",
    [
        ("postgresql+psycopg://prufai@localhost:5432/prufai", "prufai"),
        ("postgresql+psycopg://prufai@127.0.0.1:55438/postgres", "postgres"),
        (f"postgresql+psycopg://prufai@127.0.0.1:55438/{NAME}", "different"),
        (f"postgresql+psycopg://prufai@remote:55438/{NAME}", NAME),
        (
            f"postgresql+psycopg://prufai@127.0.0.1:55438/{NAME}?host=localhost&port=5432&dbname=prufai",
            NAME,
        ),
        (f"postgresql+psycopg://prufai@127.0.0.1:55438/{NAME}#override", NAME),
        (f"postgresql+psycopg://other@127.0.0.1:55438/{NAME}", NAME),
        ("sqlite:///test.db", "test.db"),
    ],
)
def test_unknown_default_override_targets_refused(monkeypatch, url, expected):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(ValueError, match="DISPOSABLE_DATABASE_REQUIRED"):
        assert_disposable_database(url, expected)


def test_exact_disposable_identity_printed_without_secret(capsys):
    assert (
        assert_disposable_database(
            f"postgresql+psycopg://prufai:PRIVATE@127.0.0.1:55438/{NAME}", NAME
        )
        == NAME
    )
    output = capsys.readouterr().out
    assert NAME in output and "55438" in output and "PRIVATE" not in output


def test_github_ephemeral_target_requires_both_markers(monkeypatch):
    url = "postgresql+psycopg://prufai:PRIVATE@localhost:5432/prufai_browser"
    monkeypatch.setattr("app.evaluation.disposable.sys.platform", "linux")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv("CI", raising=False)
    with pytest.raises(ValueError):
        assert_disposable_database(url, "prufai_browser")
    monkeypatch.setenv("CI", "true")
    assert assert_disposable_database(url, "prufai_browser") == "prufai_browser"


def test_mac_default_database_refused_even_with_inherited_ci_markers(monkeypatch):
    monkeypatch.setattr("app.evaluation.disposable.sys.platform", "darwin")
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with pytest.raises(ValueError, match="DISPOSABLE_DATABASE_REQUIRED"):
        assert_disposable_database("postgresql+psycopg://prufai@localhost:5432/prufai", "prufai")


def test_lid_demo_refuses_mismatched_effective_database_before_engine_creation(monkeypatch):
    from app.config import Settings
    from app.scripts import seed_lid_practice

    monkeypatch.setenv("PRUFAI_DEMO_ENABLED", "1")
    monkeypatch.setenv("PRUFAI_DISPOSABLE_DATABASE", NAME)
    monkeypatch.setenv("DATABASE_URL", f"postgresql+psycopg://prufai@127.0.0.1:55438/{NAME}")
    monkeypatch.setattr("sys.argv", ["seed_lid_practice", "--prepare-demo"])
    monkeypatch.setattr(
        seed_lid_practice,
        "get_settings",
        lambda: Settings(
            _env_file=None,
            environment="development",
            database_url="postgresql+psycopg://prufai@127.0.0.1:55438/postgres",
        ),
    )
    monkeypatch.setattr(
        seed_lid_practice,
        "create_session_factory",
        lambda settings: pytest.fail("Unexpected database session creation"),
    )
    with pytest.raises(ValueError, match="DISPOSABLE_DATABASE_REQUIRED"):
        seed_lid_practice.main()


def test_migration_entrypoint_refuses_before_database_import_or_connection():
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    environment = {
        **os.environ,
        "DATABASE_URL": "postgresql+psycopg://prufai:DO_NOT_PRINT@localhost:5432/prufai",
        "POSTGRES_INTEGRATION_URL": "postgresql+psycopg://prufai:DO_NOT_PRINT@localhost:5432/prufai",
        "PRUFAI_DISPOSABLE_DATABASE": "prufai",
        "GITHUB_ACTIONS": "",
        "CI": "",
    }
    result = subprocess.run(
        [sys.executable, str(root / "scripts/migrate-evaluation-disposable.py")],
        env=environment,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "DISPOSABLE_DATABASE_REQUIRED" in output
    assert "DO_NOT_PRINT" not in output
    assert "alembic.runtime.migration" not in output
