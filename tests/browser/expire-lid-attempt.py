"""Advance a single guarded, synthetic browser attempt through the actual expiry transition."""

import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from app.authorization import build_tenant_context
from app.config import get_settings
from app.db import create_session_factory
from app.evaluation.disposable import assert_disposable_database
from app.lid_practice import service as lid
from app.models import CandidateExamAttempt, LidPracticeForm, User
from app.models.enums import CandidateAttemptStatus
from app.runtime import service as runtime
from app.scripts.seed_demo import demo_id
from sqlalchemy import text


def main() -> None:
    if os.environ.get("PRUFAI_E2E_ALLOW_DEMO") != "1":
        raise SystemExit("Isolated browser acceptance is required.")
    settings = get_settings()
    name = assert_disposable_database(os.environ.get("DATABASE_URL", ""))
    if settings.environment != "development" or "browser" not in name:
        raise SystemExit("Isolated development browser database required.")
    assert_disposable_database(settings.database_url, name, report=False)
    with create_session_factory(settings)() as session:
        if session.scalar(text("SELECT current_database()")) != name:
            raise SystemExit("Database identity mismatch.")
        attempt = session.get(CandidateExamAttempt, uuid.UUID(sys.argv[1]))
        if attempt is None or attempt.organisation_id != demo_id("organisation"):
            raise SystemExit("An exact synthetic demo attempt is required.")
        owner = session.get(User, attempt.candidate_user_id)
        form = session.get(LidPracticeForm, attempt.exam_version_id)
        if (
            owner is None
            or owner.normalized_email != "candidate@demo.prufai.local"
            or form is None
            or lid.is_original_form(form)
        ):
            raise SystemExit("An exact synthetic LiD attempt is required.")
        context = build_tenant_context(session, owner.id, attempt.organisation_id)
        if len(sys.argv) == 3:
            seconds = int(sys.argv[2])
            if (
                seconds not in {20}
                or attempt.status is not CandidateAttemptStatus.READY
            ):
                raise SystemExit(
                    "A ready synthetic attempt and allowed deadline window are required."
                )
            duration = attempt.runtime_plan["duration_seconds"]
            assert duration == 3600
            now = datetime.now(UTC)
            with patch.object(
                runtime,
                "_now",
                return_value=now - timedelta(seconds=duration - seconds),
            ):
                runtime.CandidateRuntimeService.start(session, context, attempt.id)
            session.commit()
            return
        if (
            attempt.status is not CandidateAttemptStatus.IN_PROGRESS
            or attempt.deadline_at is None
        ):
            raise SystemExit("An active synthetic attempt is required.")
        with patch.object(
            runtime, "_now", return_value=attempt.deadline_at + timedelta(seconds=1)
        ):
            runtime.CandidateRuntimeService.expire_if_due(session, context, attempt)
        assert attempt.status is CandidateAttemptStatus.EXPIRED
        session.commit()


if __name__ == "__main__":
    main()
