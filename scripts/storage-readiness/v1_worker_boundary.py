"""Exercise the restored database's connection boundary with pinned legacy code."""

import secrets
import subprocess
import sys
from uuid import uuid4

from psycopg import sql
from sqlalchemy import make_url

CLAIM_PROBE = """
import importlib
import sys
from sqlalchemy.exc import DBAPIError
from app.config import get_settings
from app.db import create_session_factory

module, name = sys.argv[1:]
settings = get_settings()
assert settings.v1_pilot_profile == "DISABLED"
service = getattr(importlib.import_module(f"app.{module}.execution"), name)
with create_session_factory(settings)() as session:
    try:
        job = service.claim_next(session, worker_id="synthetic-legacy-rollback")
    except DBAPIError as error:
        # libpq startup failures can omit SQLSTATE even when PostgreSQL
        # reports a database ACL denial. Match only the CONNECT diagnostic.
        diagnostic = str(error.orig)
        if (
            "permission denied for database" not in diagnostic
            or "does not have CONNECT privilege" not in diagnostic
        ):
            raise
        print("DATABASE_CONNECT_DENIED")
    else:
        assert job is not None and job.status == "RUNNING" and job.attempt_count == 1
        session.rollback()
        print("ELIGIBLE_CLAIM_ROLLED_BACK")
"""

LEGACY_WORKERS = {
    "speech_transcription": "TranscriptionExecutionService",
    "writing_execution": "WritingExecutionService",
    "speaking_execution": "SpeakingExecutionService",
}


def verify_legacy_worker_isolation(source, target, runtime, prior):
    """Keep rollback API credentials separate from retired worker credentials."""
    assert source.rig is target.rig and source.container == target.container
    assert source.name != target.name
    assert (
        target.sql("SELECT excluded FROM public.v1_worker_policy WHERE id=1").strip()
        == b"t"
    )
    assert (
        target.sql(
            "SELECT count(*) FROM public.speech_transcription_jobs "
            "WHERE status='QUEUED' AND next_attempt_at <= CURRENT_TIMESTAMP"
        ).strip()
        == b"1"
    )

    role = "prufai_legacy_" + uuid4().hex
    password = secrets.token_urlsafe(32)
    identifier = sql.Identifier(role)

    def change(statement, *args, **kwargs):
        # Feed SQL via stdin so the synthetic role password is absent from arguments.
        source.command(
            "psql",
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            data=statement.format(*args, **kwargs).as_string().encode(),
        )

    change(
        sql.SQL(
            "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
            "NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD {};"
        ),
        identifier,
        sql.Literal(password),
    )
    try:
        change(
            sql.SQL(
                "GRANT CONNECT ON DATABASE {} TO {role};"
                "GRANT USAGE ON SCHEMA public TO {role};"
                "GRANT SELECT, UPDATE ON public.speech_transcription_jobs, "
                "public.speech_transcriptions TO {role};"
                "GRANT INSERT ON public.audit_events TO {role};"
            ),
            sql.Identifier(source.name),
            role=identifier,
        )
        allowed, denied = (
            source.sql(
                sql.SQL(
                    "SELECT has_database_privilege({}, {}, 'CONNECT'), "
                    "has_database_privilege({}, {}, 'CONNECT')"
                )
                .format(
                    sql.Literal(role),
                    sql.Literal(source.name),
                    sql.Literal(role),
                    sql.Literal(target.name),
                )
                .as_string()
            )
            .strip()
            .split(b"|")
        )
        assert (allowed, denied) == (b"t", b"f")

        def probe(db, module, service, expected):
            url = make_url(db.url).set(username=role, password=password)
            result = subprocess.run(
                (sys.executable, "-c", CLAIM_PROBE, module, service),
                cwd=prior,
                timeout=15,
                capture_output=True,
                check=False,
                env={
                    **runtime,
                    "DATABASE_URL": url.render_as_string(hide_password=False),
                    "V1_PILOT_PROFILE": "DISABLED",
                },
            )
            assert result.returncode == 0 and result.stdout.strip() == expected, (
                "legacy worker boundary probe failed",
                module,
                result.returncode,
            )

        # Positive control uses the same binary and role on the separate, stopped
        # source. Roll the claim back so the evidence and recovered history agree.
        probe(
            source,
            "speech_transcription",
            LEGACY_WORKERS["speech_transcription"],
            b"ELIGIBLE_CLAIM_ROLLED_BACK",
        )
        denied_workers = {}
        for module, service in LEGACY_WORKERS.items():
            probe(target, module, service, b"DATABASE_CONNECT_DENIED")
            denied_workers[module] = "DATABASE_CONNECT_DENIED"
        return {
            "source_positive_control": "ELIGIBLE_CLAIM_ROLLED_BACK",
            "target": denied_workers,
        }
    finally:
        change(
            sql.SQL(
                "REVOKE CONNECT ON DATABASE {} FROM {role};"
                "DROP OWNED BY {role}; DROP ROLE {role};"
            ),
            sql.Identifier(source.name),
            role=identifier,
        )
