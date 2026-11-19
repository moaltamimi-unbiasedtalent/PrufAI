"""Read submission evidence only from the guarded synthetic browser database."""

import json
import os
import sys
import uuid

from app.config import get_settings
from app.db import create_session_factory
from app.evaluation.disposable import assert_disposable_database
from app.lid_practice import service as lid
from app.models import AuditEvent, CandidateExamAttempt, LidPracticeForm, User
from app.scripts.seed_demo import demo_id
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError


def main() -> None:
    if os.environ.get("PRUFAI_E2E_ALLOW_DEMO") != "1" or len(sys.argv) != 2:
        raise ValueError("Synthetic browser acceptance is required")
    settings = get_settings()
    name = assert_disposable_database(os.environ["DATABASE_URL"], report=False)
    if settings.environment != "development" or "browser" not in name:
        raise ValueError("Disposable development browser database required")
    assert_disposable_database(settings.database_url, name, report=False)
    with create_session_factory(settings)() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        if session.scalar(text("SELECT current_database()")) != name:
            raise ValueError("Database identity mismatch")
        attempt = session.get(CandidateExamAttempt, uuid.UUID(sys.argv[1]))
        if attempt is None or attempt.organisation_id != demo_id("organisation"):
            raise ValueError("Exact synthetic demo attempt required")
        owner = session.get(User, attempt.candidate_user_id)
        form = session.get(LidPracticeForm, attempt.exam_version_id)
        if (
            owner is None
            or owner.normalized_email != "candidate@demo.prufai.local"
            or form is None
            or lid.is_original_form(form)
        ):
            raise ValueError("Exact synthetic LiD attempt required")
        events = session.scalars(
            select(AuditEvent.id).where(
                AuditEvent.organisation_id == attempt.organisation_id,
                AuditEvent.entity_type == "CANDIDATE_ATTEMPT",
                AuditEvent.entity_id == str(attempt.id),
                AuditEvent.event_type == "exam_attempt.submitted",
            )
        ).all()
        print(
            json.dumps(
                {
                    "status": attempt.status.value,
                    "submitted_at": (
                        attempt.submitted_at.isoformat()
                        if attempt.submitted_at
                        else None
                    ),
                    "submission_events": sorted(str(event) for event in events),
                }
            )
        )


if __name__ == "__main__":
    try:
        main()
    except (KeyError, OSError, ValueError, SQLAlchemyError):
        # A failing browser assertion must not print SQL or connection details.
        raise SystemExit("Synthetic submission evidence unavailable.") from None
