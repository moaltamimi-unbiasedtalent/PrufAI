"""Server-controlled V1 delivery boundary. Existing permissions still apply."""

from __future__ import annotations

import os
import uuid
from typing import Literal

from fastapi import HTTPException, Request
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from app.authorization import TenantContext
from app.authorization.membership import require_active_tenant_permission
from app.config import Settings, get_settings
from app.evaluation.disposable import assert_disposable_database
from app.models import CandidateExamAttempt, ExamVersion, LidPracticeForm, StandardPackVersion
from app.models.enums import AssessmentClassification

PilotProfile = Literal["DISABLED", "BERLIN_LID_LOCKED", "BERLIN_LID_SYNTHETIC"]
DENIED = "PILOT_SCOPE_DENIED"
MANIFEST_REQUIRED = "PILOT_APPROVED_MANIFEST_REQUIRED"
BERLIN = "DE-BE"


class PilotDenied(PermissionError):
    pass


def active(settings: Settings | None = None) -> bool:
    return (settings or get_settings()).v1_pilot_profile != "DISABLED"


def require_unrestricted(settings: Settings | None = None) -> None:
    if active(settings):
        raise PilotDenied(DENIED)


def require_institution(session: Session, context: TenantContext, permission: str) -> None:
    if active():
        require_active_tenant_permission(session, context, permission)


def require_exam(session: Session, exam: ExamVersion | None, organisation_id: uuid.UUID) -> None:
    settings = get_settings()
    if not active(settings):
        return
    from app.lid_policy import is_lid_practice_pack
    from app.lid_practice.service import is_original_form, require_form

    if exam is None or exam.exam.organisation_id != organisation_id:
        raise PilotDenied(DENIED)
    form = session.get(LidPracticeForm, exam.id)
    if (
        form is None
        or form.bundesland != BERLIN
        or exam.classification is not AssessmentClassification.PRACTICE
    ):
        raise PilotDenied(DENIED)
    # The existing editorial receipt does not constitute E03A manifest approval.
    if settings.v1_pilot_profile != "BERLIN_LID_SYNTHETIC" or is_original_form(form):
        raise PilotDenied(MANIFEST_REQUIRED)
    bind = session.get_bind()
    engine = bind.engine if isinstance(bind, Connection) else bind
    if not isinstance(engine, Engine) or settings.environment not in {"development", "test"}:
        raise PilotDenied(MANIFEST_REQUIRED)
    try:
        assert_disposable_database(
            engine.url.render_as_string(hide_password=False),
            os.environ.get("PRUFAI_DISPOSABLE_DATABASE"),
            report=False,
        )
        pack = session.get(StandardPackVersion, exam.standard_pack_version_id)
        if pack is None or not is_lid_practice_pack(pack):
            raise ValueError(MANIFEST_REQUIRED)
        require_form(session, exam, pack)
    except ValueError as exc:
        raise PilotDenied(MANIFEST_REQUIRED) from exc


def exam_allowed(session: Session, exam_id: uuid.UUID, organisation_id: uuid.UUID) -> bool:
    if not active():
        return True
    try:
        require_exam(session, session.get(ExamVersion, exam_id), organisation_id)
    except PilotDenied:
        return False
    return True


def require_attempt(session: Session, attempt: CandidateExamAttempt) -> None:
    if not active():
        return
    from app.lid_practice.service import require_attempt_form

    exam = session.get(ExamVersion, attempt.exam_version_id)
    require_exam(session, exam, attempt.organisation_id)
    pack = session.get(StandardPackVersion, exam.standard_pack_version_id) if exam else None
    if pack is None:
        raise PilotDenied(MANIFEST_REQUIRED)
    try:
        require_attempt_form(session, attempt, pack)
    except ValueError as exc:
        raise PilotDenied(MANIFEST_REQUIRED) from exc


# Match the route selected by FastAPI, rather than comparing raw request URLs.
# New API routes need an explicit pilot decision before they become reachable.
_CANDIDATE = "/api/v1/candidate"
_ORGANISATION = "/api/v1/organisations/{organisation_id}"
_ATTEMPT = "/exam-attempts/{attempt_id}"
_VERSION = "/result-versions/{version_id}"
_RELEASE = "/releases/{release_id}"
_ALLOWED = {
    "GET": {
        "/health",
        "/ready",
        *("/api/v1/auth/" + value for value in ("me", "csrf", "enrollment", "sessions")),
        *(
            _CANDIDATE + value
            for value in (
                "/exam-assignments",
                "/exam-assignments/{assignment_id}",
                _ATTEMPT,
                _ATTEMPT + "/runtime",
                _ATTEMPT + "/lid-practice-result",
                _ATTEMPT + "/lid-practice-result/status",
                "/results",
                "/results/{result_id}",
                "/releases",
                _RELEASE,
                _RELEASE + "/statement",
            )
        ),
        *(
            _ORGANISATION + value
            for value in (
                "/exam-assignment-options",
                "/exam-assignments",
                "/results/{result_id}",
                "/results/{result_id}/versions",
                "/scoring-runs/{run_id}",
                "/releases",
                _RELEASE,
            )
        ),
        *(
            "/api/v1/platform/control-center/" + value
            for value in (
                "overview",
                "organisations",
                "source-registry",
                "audit",
                "system",
            )
        ),
        *(
            "/api/v1/platform/standards/lid/" + value
            for value in (
                "sources",
                "blueprints",
                "blueprints/{version_id}",
            )
        ),
    },
    "POST": {
        *(
            "/api/v1/auth/" + value
            for value in (
                "login",
                "refresh",
                "logout",
                "invitations/accept",
                "password-reset/request",
                "password-reset/confirm",
            )
        ),
        *(
            _CANDIDATE + value
            for value in (
                "/exam-assignments/{assignment_id}/attempts",
                _ATTEMPT + "/start",
                _ATTEMPT + "/heartbeat",
                _ATTEMPT + "/navigate",
                _ATTEMPT + "/submit",
            )
        ),
        *(
            _ORGANISATION + value
            for value in (
                "/invitations",
                "/exam-assignments",
                "/exam-assignments/{assignment_id}/cancel",
                _ATTEMPT + "/score",
                _VERSION + "/request-review",
                _VERSION + "/approve",
                _VERSION + "/reject",
                _VERSION + "/release",
                _RELEASE + "/statement",
                _RELEASE + "/revoke",
            )
        ),
    },
    "PUT": {_CANDIDATE + _ATTEMPT + "/responses/{binding_id}"},
    "DELETE": {"/api/v1/auth/sessions/{session_id}"},
}


async def enforce_route(request: Request) -> None:
    if active() and getattr(request.scope.get("route"), "path", None) not in _ALLOWED.get(
        request.method, ()
    ):
        raise HTTPException(status_code=403, detail=DENIED, headers={"Cache-Control": "no-store"})
