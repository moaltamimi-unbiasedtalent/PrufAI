"""Server-owned boundary for isolated, synthetic prompt validation."""

from __future__ import annotations

from sqlalchemy import Connection, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session

from app.authorization import AuthorizationError
from app.config import Settings
from app.evaluation.disposable import assert_disposable_database
from app.writing_execution.runtime import validate_execution_configuration

NONPRODUCTION = "NONPRODUCTION_VALIDATION"


def require_validation_environment(session: Session, settings: Settings) -> None:
    if (
        settings.environment not in {"test", "development"}
        or not settings.nonproduction_prompt_validation_enabled
        or not settings.nonproduction_prompt_validation_database
        or validate_execution_configuration(
            {
                "provider": "DETERMINISTIC_TEST",
                "model_name": "writing-fixture-v1",
                "adapter_version": "1",
                "configuration": {"schema_version": "1"},
                "tool_policy": [],
            },
            settings,
        )["availability"]
        != "TEST_ONLY"
    ):
        raise AuthorizationError("ISOLATED_PROMPT_VALIDATION_UNAVAILABLE")
    bind = session.get_bind()
    engine = bind.engine if isinstance(bind, Connection) else bind
    try:
        if make_url(settings.database_url) != engine.url:
            raise ValueError("DATABASE_BINDING_MISMATCH")
        name = assert_disposable_database(
            engine.url.render_as_string(hide_password=False),
            settings.nonproduction_prompt_validation_database,
            report=False,
        )
    except (ValueError, ArgumentError) as exc:
        raise AuthorizationError("ISOLATED_PROMPT_VALIDATION_DATABASE_REQUIRED") from exc
    # Never connect to an unrecognized URL to discover its identity.
    if session.scalar(text("SELECT current_database()")) != name:
        raise AuthorizationError("ISOLATED_PROMPT_VALIDATION_DATABASE_REQUIRED")


def validation_available(session: Session, settings: Settings) -> bool:
    try:
        require_validation_environment(session, settings)
    except AuthorizationError:
        return False
    return True
