"""Reconcile a small, original, development-only objective examination demo.

Run explicitly with ENVIRONMENT=development and PRUFAI_DEMO_ENABLED=1 after
Alembic migrations. The v2 namespace leaves the earlier invalid demo graph and
unrelated developer data untouched. No media or AI evidence is fabricated.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.passwords import verify_password
from app.auth.service import AuthenticationService
from app.authorization import build_tenant_context
from app.authorization.seed import seed_defaults
from app.config import Settings
from app.db import create_session_factory
from app.exams.service import ExamStudioService
from app.models import (
    AssessmentResult,
    Base,
    CandidateExamAssignment,
    CandidateExamAttempt,
    CandidateResponse,
    Exam,
    ExamVersion,
    ExamVersionItemBinding,
    ExamVersionModule,
    ExamVersionSection,
    ExamVersionTask,
    MembershipRole,
    Organisation,
    OrganisationMembership,
    PlatformRoleAssignment,
    QuestionBankItem,
    QuestionBankItemCefrLevel,
    QuestionBankItemVersion,
    ResultVersion,
    Role,
    StandardPack,
    StandardPackModule,
    StandardPackSection,
    StandardPackTaskSpecification,
    StandardPackVersion,
    User,
)
from app.models.enums import (
    AssessmentClassification,
    AssessmentFamily,
    CandidateAttemptStatus,
    ExamOriginType,
    ExamVersionStatus,
    MembershipStatus,
    OrganisationType,
    PackMediaRequirement,
    PackNavigationMode,
    PackResponseMode,
    PlatformAssignmentStatus,
    QuestionBankLifecycleStatus,
    QuestionBankOriginType,
    QuestionBankRightsStatus,
    QuestionBankScoringPolicy,
    QuestionBankSkill,
    QuestionBankTaskType,
    RoleScope,
    StandardPackStatus,
    UserStatus,
)
from app.question_bank.service import item_checksum
from app.runtime.service import CandidateRuntimeService
from app.scoring.service import ScoringService, checksum
from app.services.identity import normalize_email

ORG_SLUG = "prufai-demo-academy-v2"
PASSWORD = "Demo-PrufAI-2026!"  # Explicit local demo invocation only; never API bootstrap.
PLATFORM_ADMIN_EMAIL = "platform.admin@demo.prufai.local"
PLATFORM_ADMIN_NAME = "Demo Platform Administrator"
ACCOUNTS = {
    "candidate@demo.prufai.local": ("Demo Candidate", "EXAMINER"),
    "reviewer@demo.prufai.local": ("Demo Reviewer", "EXAM_MANAGER"),
    "admin@demo.prufai.local": ("Demo Institution Admin", "INSTITUTION_ADMIN"),
    "history@demo.prufai.local": ("Demo Historical Candidate", "EXAMINER"),
}
NAMESPACE = uuid.UUID("f7dfe788-7118-4f7d-9b06-7d535365a45d")
Model = TypeVar("Model", bound=Base)


def demo_id(key: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, key)


def _ensure(session: Session, model: type[Model], key: str, **values: Any) -> Model:
    row = session.get(model, demo_id(key))
    if row is None:
        row = model(id=demo_id(key), **values)
        session.add(row)
        session.flush()
    return row


def require_demo_environment(settings: Settings, *, enabled: bool) -> None:
    if settings.environment != "development" or not enabled:
        raise RuntimeError("Demo seed requires ENVIRONMENT=development and PRUFAI_DEMO_ENABLED=1.")


def _platform_user(session: Session, settings: Settings, password: str) -> User:
    """Reconcile an owned demo identity without claiming a developer account.

    Platform permissions come from the existing platform role. No tenant
    membership is created, removed or silently used to impersonate an institution.
    """
    identifier = demo_id("platform-admin")
    email = normalize_email(PLATFORM_ADMIN_EMAIL)
    by_email = session.scalar(select(User).where(User.normalized_email == email))
    user = session.get(User, identifier)
    if by_email is not None and by_email.id != identifier:
        raise RuntimeError("Platform demo email belongs to another identity; no privilege granted.")
    if user is not None and user.normalized_email != email:
        raise RuntimeError("Platform demo identity collision; no privilege granted.")
    if user is not None and session.scalar(
        select(OrganisationMembership.id).where(OrganisationMembership.user_id == user.id).limit(1)
    ):
        raise RuntimeError("Platform demo identity has a tenant membership; no membership changed.")
    if user is None:
        user = User(
            id=identifier,
            email=PLATFORM_ADMIN_EMAIL,
            normalized_email=email,
            display_name=PLATFORM_ADMIN_NAME,
            status=UserStatus.ACTIVE,
        )
        session.add(user)
        session.flush()
    user.status = UserStatus.ACTIVE
    if user.display_name is None:
        user.display_name = PLATFORM_ADMIN_NAME
    credential = user.password_credential
    if credential is None or not verify_password(credential.password_hash, password):
        AuthenticationService(settings).set_password(session, user, password)
    role = session.scalar(
        select(Role).where(Role.key == "PRUFAI_SUPER_ADMIN", Role.scope == RoleScope.PLATFORM)
    )
    if role is None:
        raise RuntimeError("Required platform role is missing: PRUFAI_SUPER_ADMIN")
    assignment = session.scalar(
        select(PlatformRoleAssignment).where(
            PlatformRoleAssignment.user_id == user.id, PlatformRoleAssignment.role_id == role.id
        )
    )
    if assignment is None:
        assignment = PlatformRoleAssignment(
            id=demo_id("platform-admin-role"), user_id=user.id, role_id=role.id
        )
        session.add(assignment)
    assignment.status = PlatformAssignmentStatus.ACTIVE
    session.flush()
    return user


def _users(
    session: Session, settings: Settings, organisation: Organisation, password: str
) -> dict[str, User]:
    users: dict[str, User] = {}
    auth = AuthenticationService(settings)
    for email, (name, role_key) in ACCOUNTS.items():
        user = session.scalar(select(User).where(User.normalized_email == normalize_email(email)))
        if user is None:
            user = User(email=email, normalized_email=normalize_email(email), display_name=name)
            session.add(user)
            session.flush()
        user.status = UserStatus.ACTIVE
        if user.display_name is None:
            user.display_name = name
        credential = user.password_credential
        if credential is None or not verify_password(credential.password_hash, password):
            auth.set_password(session, user, password)
        membership = session.scalar(
            select(OrganisationMembership).where(
                OrganisationMembership.organisation_id == organisation.id,
                OrganisationMembership.user_id == user.id,
            )
        )
        if membership is None:
            membership = OrganisationMembership(organisation_id=organisation.id, user_id=user.id)
            session.add(membership)
            session.flush()
        membership.status = MembershipStatus.ACTIVE
        role = session.scalar(select(Role).where(Role.key == role_key))
        if role is None:
            raise RuntimeError(f"Required repository role is missing: {role_key}")
        if session.get(MembershipRole, (membership.id, role.id)) is None:
            session.add(MembershipRole(membership_id=membership.id, role_id=role.id))
        users[email] = user
    session.flush()
    return users


def _exam(session: Session, organisation: Organisation, author: User) -> ExamVersion:
    now = datetime.now(timezone.utc)
    pack = _ensure(
        session,
        StandardPack,
        "pack",
        code="PRUFAI_DEMO_V2",
        name="Original German reading practice — SYNTHETIC DEMO",
        assessment_family=AssessmentFamily.LANGUAGE,
        language="de",
        status=StandardPackStatus.ACTIVE,
    )
    pack_version = _ensure(
        session,
        StandardPackVersion,
        "pack-version",
        standard_pack_id=pack.id,
        version_label="demo-v2",
        status=StandardPackStatus.ACTIVE,
        configuration_checksum=checksum({"demo": "original-reading-v2"}),
        supported_levels=["B1"],
        allowed_classifications=["PRACTICE"],
        created_at=now,
        created_by_user_id=author.id,
        notes="Synthetic practice, not official provider material.",
    )
    module = _ensure(
        session,
        StandardPackModule,
        "pack-module",
        standard_pack_version_id=pack_version.id,
        code="READING",
        name="Reading",
        skill="READING",
        sequence=1,
        navigation_mode=PackNavigationMode.FREE_WITHIN_MODULE,
        created_at=now,
    )
    section = _ensure(
        session,
        StandardPackSection,
        "pack-section",
        module_id=module.id,
        code="ORIGINAL",
        name="Original short text",
        sequence=1,
        navigation_mode=PackNavigationMode.FREE_WITHIN_MODULE,
        created_at=now,
    )
    task = _ensure(
        session,
        StandardPackTaskSpecification,
        "pack-task",
        section_id=section.id,
        code="READING_1",
        name="Read the notice and choose an answer",
        sequence=1,
        task_type="MULTIPLE_CHOICE",
        min_items=1,
        max_items=1,
        response_mode=PackResponseMode.SELECT_ONE,
        media_requirement=PackMediaRequirement.NONE,
        created_at=now,
    )
    item = _ensure(
        session,
        QuestionBankItem,
        "item",
        code="PRUFAI_DEMO_V2_READING_1",
        scope_type="ORGANISATION_PRIVATE",
        organisation_id=organisation.id,
        status=QuestionBankLifecycleStatus.ACTIVE,
        created_by_user_id=author.id,
    )
    content = {
        "kind": "MULTIPLE_CHOICE",
        "prompt": "Am Montag beginnt der Deutschkurs um 9 Uhr. Wann beginnt der Kurs?",
        "options": [
            {"option_id": "a", "display_text": "Um 9 Uhr.", "order": 0},
            {"option_id": "b", "display_text": "Um 10 Uhr.", "order": 1},
        ],
        "correct_option_ids": ["a"],
    }
    item_version = _ensure(
        session,
        QuestionBankItemVersion,
        "item-version",
        item_id=item.id,
        version_label="demo-v2",
        status=QuestionBankLifecycleStatus.ACTIVE,
        assessment_family=AssessmentFamily.LANGUAGE,
        language="de",
        skill=QuestionBankSkill.READING,
        task_type=QuestionBankTaskType.MULTIPLE_CHOICE,
        response_mode=PackResponseMode.SELECT_ONE,
        media_requirement=PackMediaRequirement.NONE,
        content=content,
        scoring_policy=QuestionBankScoringPolicy.OBJECTIVE_KEYED,
        max_points=Decimal("1"),
        scoring_metadata={"unanswered_policy": {"type": "ZERO", "points": "0"}},
        rights_status=QuestionBankRightsStatus.PRUFAI_ORIGINAL,
        rights_notes="Original synthetic demo question; not official provider material.",
        origin_type=QuestionBankOriginType.HUMAN_AUTHORED,
        checksum=item_checksum(content),
        created_at=now,
        created_by_user_id=author.id,
    )
    _ensure(
        session,
        QuestionBankItemCefrLevel,
        "item-level",
        item_version_id=item_version.id,
        cefr_level="B1",
    )
    exam = _ensure(
        session,
        Exam,
        "exam",
        code="PRUFAI_DEMO_V2_READING",
        name="Original German reading practice — local demo",
        description="One original objective item. Writing and recorded audio are unavailable.",
        scope_type="ORGANISATION_PRIVATE",
        organisation_id=organisation.id,
        created_by_user_id=author.id,
        created_at=now,
    )
    version = _ensure(
        session,
        ExamVersion,
        "exam-version",
        exam_id=exam.id,
        version_label="demo-v2",
        status=ExamVersionStatus.ACTIVE,
        standard_pack_version_id=pack_version.id,
        classification=AssessmentClassification.PRACTICE,
        origin_type=ExamOriginType.HUMAN_AUTHORED,
        created_at=now,
        created_by_user_id=author.id,
        checksum=checksum({"demo": "exam-v2"}),
    )
    exam_module = _ensure(
        session,
        ExamVersionModule,
        "exam-module",
        exam_version_id=version.id,
        standard_pack_module_id=module.id,
        ordinal=1,
    )
    exam_section = _ensure(
        session,
        ExamVersionSection,
        "exam-section",
        exam_version_module_id=exam_module.id,
        standard_pack_section_id=section.id,
        ordinal=1,
    )
    exam_task = _ensure(
        session,
        ExamVersionTask,
        "exam-task",
        exam_version_section_id=exam_section.id,
        standard_pack_task_spec_id=task.id,
        ordinal=1,
    )
    _ensure(
        session,
        ExamVersionItemBinding,
        "exam-binding",
        exam_version_task_id=exam_task.id,
        slot_index=0,
        question_bank_item_version_id=item_version.id,
        origin_type=ExamOriginType.HUMAN_AUTHORED,
        created_at=now,
    )
    # Re-query relationships after repairing a missing hierarchy child. Existing
    # published content is never edited by this command.
    session.expire_all()
    context = build_tenant_context(session, author.id, organisation.id)
    validation = ExamStudioService.validate(session, context, version)
    if validation.status != "PASS":
        raise RuntimeError(f"Demo exam validation failed: {validation.issues}")
    CandidateRuntimeService._safe_plan(session, version)
    return version


def _assignment(
    session: Session,
    administrator: User,
    candidate: User,
    organisation: Organisation,
    version: ExamVersion,
) -> CandidateExamAssignment:
    existing = session.scalar(
        select(CandidateExamAssignment)
        .where(
            CandidateExamAssignment.organisation_id == organisation.id,
            CandidateExamAssignment.candidate_user_id == candidate.id,
            CandidateExamAssignment.exam_version_id == version.id,
        )
        .order_by(CandidateExamAssignment.created_at)
    )
    if existing is not None:
        return existing
    return CandidateRuntimeService.create_assignment(
        session,
        build_tenant_context(session, administrator.id, organisation.id),
        candidate_user_id=candidate.id,
        exam_version_id=version.id,
    )


def _historical_result(
    session: Session,
    organisation: Organisation,
    administrator: User,
    candidate: User,
    version: ExamVersion,
) -> ResultVersion:
    assignment = _assignment(session, administrator, candidate, organisation, version)
    candidate_context = build_tenant_context(session, candidate.id, organisation.id)
    attempt = session.scalar(
        select(CandidateExamAttempt).where(CandidateExamAttempt.assignment_id == assignment.id)
    )
    if attempt is None:
        attempt = CandidateRuntimeService.create_attempt(session, candidate_context, assignment.id)
    if attempt.status is CandidateAttemptStatus.READY:
        CandidateRuntimeService.start(session, candidate_context, attempt.id)
    if attempt.status is CandidateAttemptStatus.IN_PROGRESS:
        binding_id = version.modules[0].sections[0].tasks[0].item_bindings[0].id
        saved = session.scalar(
            select(CandidateResponse).where(
                CandidateResponse.attempt_id == attempt.id,
                CandidateResponse.exam_version_item_binding_id == binding_id,
            )
        )
        CandidateRuntimeService.save_response(
            session,
            candidate_context,
            attempt.id,
            binding_id,
            payload={"option_id": "a"},
            client_operation_id=demo_id("history-response"),
            expected_revision=saved.current_revision if saved else 0,
        )
        CandidateRuntimeService.submit(session, candidate_context, attempt.id)
    if attempt.status is not CandidateAttemptStatus.SUBMITTED:
        raise RuntimeError("Historical demo attempt is not a submitted immutable attempt.")
    context = build_tenant_context(session, administrator.id, organisation.id)
    result = session.scalar(
        select(AssessmentResult).where(AssessmentResult.attempt_id == attempt.id)
    )
    if result is None:
        ScoringService.score(session, context, attempt.id)
        result = session.scalar(
            select(AssessmentResult).where(AssessmentResult.attempt_id == attempt.id)
        )
    if result is None:
        raise RuntimeError("Historical demo scoring did not produce an assessment result.")
    result_version = session.scalar(
        select(ResultVersion)
        .where(ResultVersion.result_id == result.id)
        .order_by(ResultVersion.version_number.desc())
    )
    if result_version is None:
        raise RuntimeError("Historical demo result version is missing.")
    # The development operator explicitly seeds a synthetic human-reviewed
    # practice result through the existing review/release services, not an
    # official certificate or Phase 8 Step 2 release artifact.
    if result_version.status.value == "REVIEW_REQUIRED":
        ScoringService.request_review(session, context, result_version.id)
        ScoringService.approve_review(session, context, result_version.id)
    if result_version.status.value == "APPROVED":
        ScoringService.release(session, context, result_version.id)
    return result_version


def reconcile_demo(
    session: Session, settings: Settings, *, enabled: bool, password: str = PASSWORD
) -> dict[str, str]:
    require_demo_environment(settings, enabled=enabled)
    seed_defaults(session)
    platform_administrator = _platform_user(session, settings, password)
    organisation = _ensure(
        session,
        Organisation,
        "organisation",
        slug=ORG_SLUG,
        name="PrüfAI Demo Academy v2 — Synthetic Practice",
        organisation_type=OrganisationType.LANGUAGE_SCHOOL,
    )
    users = _users(session, settings, organisation, password)
    administrator = users["admin@demo.prufai.local"]
    version = _exam(session, organisation, administrator)
    assignment = _assignment(
        session, administrator, users["candidate@demo.prufai.local"], organisation, version
    )
    historical = _historical_result(
        session, organisation, administrator, users["history@demo.prufai.local"], version
    )
    session.commit()
    return {
        "organisation_id": str(organisation.id),
        "organisation_slug": organisation.slug,
        "exam_version_id": str(version.id),
        "candidate_assignment_id": str(assignment.id),
        "historical_result_version_id": str(historical.id),
        "platform_admin_user_id": str(platform_administrator.id),
    }


def seed() -> dict[str, str]:
    settings = Settings()
    enabled = os.getenv("PRUFAI_DEMO_ENABLED") == "1"
    require_demo_environment(settings, enabled=enabled)
    with create_session_factory(settings)() as session:
        identifiers = reconcile_demo(
            session,
            settings,
            enabled=enabled,
            password=os.getenv("PRUFAI_DEMO_PASSWORD", PASSWORD),
        )
    print(f"Reconciled synthetic objective and platform governance demo: {ORG_SLUG}")
    print("Use the documented local-only accounts. No audio/writing evidence is seeded.")
    return identifiers


if __name__ == "__main__":
    seed()
