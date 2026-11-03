"""Reconcile a small, original, development-only objective examination demo.

Run explicitly with ENVIRONMENT=development and PRUFAI_DEMO_ENABLED=1 after
Alembic migrations. The v2 namespace leaves the earlier invalid demo graph and
unrelated developer data untouched. No media or AI evidence is fabricated.
"""

from __future__ import annotations

import hashlib
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
    KnowledgeAuthority,
    KnowledgeDocumentVersion,
    KnowledgeLegalMetadata,
    KnowledgeSource,
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
    StandardPackSourceBinding,
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
    KnowledgeAuthorityType,
    KnowledgeDocumentType,
    KnowledgeScope,
    KnowledgeVersionStatus,
    LegalReviewStatus,
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
    StandardPackBindingType,
    StandardPackStatus,
    UserStatus,
)
from app.question_bank.service import item_checksum
from app.runtime.service import CandidateRuntimeService
from app.scoring.service import ScoringService, checksum
from app.scripts.seed_audio_demo import reconcile_audio_demo
from app.scripts.seed_writing_demo import reconcile_writing_demo
from app.services.identity import normalize_email
from app.standard_packs.service import StandardPackService

ORG_SLUG = "prufai-demo-academy-v2"
PASSWORD = "Demo-PrufAI-2026!"  # Explicit local demo invocation only; never API bootstrap.
PLATFORM_ADMIN_EMAIL = "platform.admin@demo.prufai.local"
PLATFORM_ADMIN_NAME = "Demo Platform Administrator"
GOLDEN_CANDIDATE_EMAIL = "golden.candidate@demo.prufai.local"
GOLDEN_CONTENT_LABEL = "SYNTHETIC DEMO DATA — NOT OFFICIAL MATERIAL"
GOLDEN_POLICY_CONTENT = (
    f"{GOLDEN_CONTENT_LABEL}\n"
    "Original internal B1 German LANGUAGE PRACTICE policy for local development.\n"
    "This one-item reading practice uses German (de), SELECT_ONE and immutable item versions.\n"
    "The authored answer key awards one point for option a and zero otherwise.\n"
    "The final submitted response revision is scored by the deterministic scoring service.\n"
    "An authorized human must review and approve the result before its governed release.\n"
    "Raw points do not establish a language qualification or an official provider entitlement.\n"
    "No pass/fail threshold, official certificate, AI assessment or transcription is supplied.\n"
    "This is seeded human-approved internal policy metadata, stored durably in document notes.\n"
    "There is no uploaded object, extracted page, ingestion job or ingestion-success claim.\n"
)
ACCOUNTS = {
    "candidate@demo.prufai.local": ("Demo Candidate", "EXAMINER"),
    "reviewer@demo.prufai.local": ("Demo Reviewer", "EXAM_MANAGER"),
    "admin@demo.prufai.local": ("Demo Institution Admin", "INSTITUTION_ADMIN"),
    "history@demo.prufai.local": ("Demo Historical Candidate", "EXAMINER"),
    GOLDEN_CANDIDATE_EMAIL: ("Golden B1 Practice Candidate", "EXAMINER"),
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
        if email == GOLDEN_CANDIDATE_EMAIL:
            expected_id = demo_id("golden-candidate")
            by_id = session.get(User, expected_id)
            if (user is not None and user.id != expected_id) or (
                by_id is not None and by_id.normalized_email != normalize_email(email)
            ):
                raise RuntimeError("Golden demo identity collision; no account was claimed.")
        if user is None:
            user = User(
                id=demo_id("golden-candidate") if email == GOLDEN_CANDIDATE_EMAIL else uuid.uuid4(),
                email=email,
                normalized_email=normalize_email(email),
                display_name=name,
            )
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


def _golden_policy(session: Session, operator: User, pack_version: StandardPackVersion) -> None:
    """Seed original governance prerequisites; do not simulate source ingestion.

    The explicit development operator approves this small internal policy
    metadata fixture. Normal KnowledgeRegistryService.approve requires an
    ingested file and pages; this fixture deliberately claims neither. Pack and
    exam governance below still execute their real validation/lifecycle services.
    """
    now = datetime.now(timezone.utc)
    content_hash = hashlib.sha256(GOLDEN_POLICY_CONTENT.encode()).hexdigest()
    authority = _ensure(
        session,
        KnowledgeAuthority,
        "golden-policy-authority",
        code="PRUFAI_GOLDEN_INTERNAL",
        name=f"Original internal practice policy — {GOLDEN_CONTENT_LABEL}",
        authority_type=KnowledgeAuthorityType.INTERNAL,
    )
    source = _ensure(
        session,
        KnowledgeSource,
        "golden-policy-source",
        authority_id=authority.id,
        source_code="PRUFAI_GOLDEN_B1_POLICY",
        title=f"B1 original practice policy — {GOLDEN_CONTENT_LABEL}",
        document_type=KnowledgeDocumentType.POLICY,
        scope=KnowledgeScope.PLATFORM_SHARED,
        assessment_family=AssessmentFamily.LANGUAGE,
        language="de",
        cefr_level="B1",
        notes="Original internal development policy. No provider alignment or authority.",
    )
    document = _ensure(
        session,
        KnowledgeDocumentVersion,
        "golden-policy-document",
        source_id=source.id,
        version_label="golden-b1-policy-v1",
        status=KnowledgeVersionStatus.APPROVED,
        sha256=content_hash,
        notes=GOLDEN_POLICY_CONTENT,
        created_at=now,
        created_by_user_id=operator.id,
        reviewed_at=now,
        reviewed_by_user_id=operator.id,
        approved_at=now,
        approved_by_user_id=operator.id,
    )
    _ensure(
        session,
        KnowledgeLegalMetadata,
        "golden-policy-rights",
        document_version_id=document.id,
        copyright_status="PRUFAI_ORIGINAL",
        licence_type="ORIGINAL_SYNTHETIC_DEVELOPMENT_POLICY",
        rights_holder="PrüfAI synthetic development fixture",
        usage_rights=GOLDEN_CONTENT_LABEL,
        legal_review_status=LegalReviewStatus.APPROVED,
        legal_reviewed_at=now,
        legal_reviewed_by_user_id=operator.id,
    )
    _ensure(
        session,
        StandardPackSourceBinding,
        "golden-policy-binding",
        standard_pack_version_id=pack_version.id,
        knowledge_source_id=source.id,
        document_version_id=document.id,
        document_checksum=content_hash,
        binding_type=StandardPackBindingType.NORMATIVE,
        purpose="Original synthetic internal practice policy; no official material.",
        required=True,
        created_at=now,
    )


def _exam(
    session: Session,
    organisation: Organisation,
    author: User,
    *,
    golden: bool = False,
    operator: User | None = None,
) -> ExamVersion:
    now = datetime.now(timezone.utc)

    def key(value: str) -> str:
        return f"golden-{value}" if golden else value

    code = "PRUFAI_GOLDEN_B1" if golden else "PRUFAI_DEMO_V2"
    label = "golden-b1-v1" if golden else "demo-v2"
    pack = _ensure(
        session,
        StandardPack,
        key("pack"),
        code=code,
        name=f"B1 German objective practice — {GOLDEN_CONTENT_LABEL}"
        if golden
        else "Original German reading practice — SYNTHETIC DEMO",
        assessment_family=AssessmentFamily.LANGUAGE,
        language="de",
        status=StandardPackStatus.ACTIVE,
    )
    pack_version = _ensure(
        session,
        StandardPackVersion,
        key("pack-version"),
        standard_pack_id=pack.id,
        version_label=label,
        status=StandardPackStatus.DRAFT if golden else StandardPackStatus.ACTIVE,
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
        key("pack-module"),
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
        key("pack-section"),
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
        key("pack-task"),
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
        key("item"),
        code=f"{code}_READING_1",
        scope_type="ORGANISATION_PRIVATE",
        organisation_id=organisation.id,
        status=QuestionBankLifecycleStatus.ACTIVE,
        created_by_user_id=author.id,
    )
    content = {
        "kind": "MULTIPLE_CHOICE",
        "prompt": f"{GOLDEN_CONTENT_LABEL}\n"
        "Am Dienstag beginnt der Deutschkurs um 9 Uhr. Wann beginnt der Kurs?"
        if golden
        else "Am Montag beginnt der Deutschkurs um 9 Uhr. Wann beginnt der Kurs?",
        "options": [
            {"option_id": "a", "display_text": "Um 9 Uhr.", "order": 0},
            {"option_id": "b", "display_text": "Um 10 Uhr.", "order": 1},
        ],
        "correct_option_ids": ["a"],
    }
    item_version = _ensure(
        session,
        QuestionBankItemVersion,
        key("item-version"),
        item_id=item.id,
        version_label=label,
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
        key("item-level"),
        item_version_id=item_version.id,
        cefr_level="B1",
    )
    exam = _ensure(
        session,
        Exam,
        key("exam"),
        code=f"{code}_READING",
        name=f"Golden B1 German Practice — {GOLDEN_CONTENT_LABEL}"
        if golden
        else "Original German reading practice — local demo",
        description="One original objective item. Writing and recorded audio are unavailable.",
        scope_type="ORGANISATION_PRIVATE",
        organisation_id=organisation.id,
        created_by_user_id=author.id,
        created_at=now,
    )
    version = _ensure(
        session,
        ExamVersion,
        key("exam-version"),
        exam_id=exam.id,
        version_label=label,
        status=ExamVersionStatus.DRAFT if golden else ExamVersionStatus.ACTIVE,
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
        key("exam-module"),
        exam_version_id=version.id,
        standard_pack_module_id=module.id,
        ordinal=1,
    )
    exam_section = _ensure(
        session,
        ExamVersionSection,
        key("exam-section"),
        exam_version_module_id=exam_module.id,
        standard_pack_section_id=section.id,
        ordinal=1,
    )
    exam_task = _ensure(
        session,
        ExamVersionTask,
        key("exam-task"),
        exam_version_section_id=exam_section.id,
        standard_pack_task_spec_id=task.id,
        ordinal=1,
    )
    _ensure(
        session,
        ExamVersionItemBinding,
        key("exam-binding"),
        exam_version_task_id=exam_task.id,
        slot_index=0,
        question_bank_item_version_id=item_version.id,
        origin_type=ExamOriginType.HUMAN_AUTHORED,
        created_at=now,
    )
    # Re-query relationships after repairing a missing hierarchy child. Existing
    # published content is never edited by this command.
    session.expire_all()
    if golden:
        if operator is None:
            raise RuntimeError("The golden fixture requires its explicit platform operator.")
        _golden_policy(session, operator, pack_version)
        session.expire_all()
        context = build_tenant_context(session, operator.id)
        for pack_status, pack_target in (
            (StandardPackStatus.DRAFT, StandardPackStatus.VALIDATION_REQUIRED),
            (StandardPackStatus.VALIDATION_REQUIRED, StandardPackStatus.HUMAN_REVIEW),
            (StandardPackStatus.HUMAN_REVIEW, StandardPackStatus.APPROVED),
            (StandardPackStatus.APPROVED, StandardPackStatus.PUBLISHED),
            (StandardPackStatus.PUBLISHED, StandardPackStatus.ACTIVE),
        ):
            if pack_version.status is pack_status:
                StandardPackService.transition(
                    session, pack_version, pack_target, actor_id=operator.id, context=context
                )
    else:
        context = build_tenant_context(session, author.id, organisation.id)
    if golden and version.status in {
        ExamVersionStatus.DRAFT,
        ExamVersionStatus.VALIDATION_REQUIRED,
    }:
        ExamStudioService._refresh_checksum(session, version)
        ExamStudioService.request_review(session, context, version)
    if golden:
        for exam_status, exam_target in (
            (ExamVersionStatus.HUMAN_REVIEW, ExamVersionStatus.APPROVED),
            (ExamVersionStatus.APPROVED, ExamVersionStatus.PUBLISHED),
            (ExamVersionStatus.PUBLISHED, ExamVersionStatus.ACTIVE),
        ):
            if version.status is exam_status:
                ExamStudioService.transition(session, context, version, exam_target)
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
    golden_version = _exam(
        session, organisation, administrator, golden=True, operator=platform_administrator
    )
    assignment = _assignment(
        session, administrator, users["candidate@demo.prufai.local"], organisation, version
    )
    historical = _historical_result(
        session, organisation, administrator, users["history@demo.prufai.local"], version
    )
    writing = reconcile_writing_demo(
        session,
        settings,
        enabled=enabled,
        organisation=organisation,
        administrator=administrator,
        platform_operator=platform_administrator,
        password=password,
    )
    audio = reconcile_audio_demo(
        session,
        settings,
        enabled=enabled,
        organisation=organisation,
        administrator=administrator,
        platform_operator=platform_administrator,
        password=password,
    )
    session.commit()
    return {
        "organisation_id": str(organisation.id),
        "organisation_slug": organisation.slug,
        "exam_version_id": str(version.id),
        "candidate_assignment_id": str(assignment.id),
        "historical_result_version_id": str(historical.id),
        "platform_admin_user_id": str(platform_administrator.id),
        "golden_candidate_user_id": str(users[GOLDEN_CANDIDATE_EMAIL].id),
        "golden_exam_version_id": str(golden_version.id),
        "golden_pack_version_id": str(golden_version.standard_pack_version_id),
        "golden_item_version_id": str(demo_id("golden-item-version")),
        **writing,
        **audio,
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
    print(
        "Use the documented local-only accounts. "
        "No audio/writing responses or assessments are seeded."
    )
    return identifiers


if __name__ == "__main__":
    seed()
