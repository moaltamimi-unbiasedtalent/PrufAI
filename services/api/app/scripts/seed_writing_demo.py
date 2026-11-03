"""Original HUMAN Writing prerequisites for the explicit development demo.

Only the pack/item/exam are activated through their existing governance services.
The rubric remains DRAFT for real authenticated review, approval and publication.
The candidate has no seeded assignment, response, assessment, score or AI run.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.passwords import verify_password
from app.auth.service import AuthenticationService
from app.authorization import build_tenant_context
from app.config import Settings
from app.exams.service import ExamStudioService
from app.models import (
    AssessmentRubric,
    Base,
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
    QuestionBankItem,
    QuestionBankItemVersion,
    QuestionBankStandardPackBinding,
    Role,
    RubricVersion,
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
    ExamOriginType,
    ExamVersionStatus,
    KnowledgeAuthorityType,
    KnowledgeDocumentType,
    KnowledgeScope,
    KnowledgeVersionStatus,
    LegalReviewStatus,
    MembershipStatus,
    PackMediaRequirement,
    PackNavigationMode,
    PackResponseMode,
    QuestionBankLifecycleStatus,
    QuestionBankRightsStatus,
    QuestionBankScoringPolicy,
    QuestionBankSkill,
    QuestionBankTaskType,
    RoleScope,
    RubricVersionStatus,
    StandardPackBindingType,
    StandardPackStatus,
    UserStatus,
)
from app.question_bank.service import QuestionBankService
from app.runtime.service import CandidateRuntimeService
from app.standard_packs.service import StandardPackService
from app.writing_assessment.service import RubricService

WRITING_CANDIDATE_EMAIL = "writing.candidate@demo.prufai.local"
WRITING_CANDIDATE_NAME = "Human Writing Practice Candidate"
WRITING_LABEL = "SYNTHETIC DEMO DATA — NOT OFFICIAL MATERIAL"
WRITING_EXAM_NAME = f"Human B1 German Writing Practice — {WRITING_LABEL}"
WRITING_VERSION_LABEL = "human-writing-b1-v1"
WRITING_PROMPT = (
    f"{WRITING_LABEL}\n"
    "Sie besuchen einen Deutschkurs. Am Dienstag können Sie nicht zum Unterricht kommen. "
    "Schreiben Sie Ihrer Kursleiterin Frau Sommer eine kurze Nachricht auf Deutsch. "
    "Erklären Sie, warum Sie fehlen. Fragen Sie nach den Hausaufgaben. "
    "Sagen Sie, wann Sie wieder am Kurs teilnehmen. "
    "Schreiben Sie etwa 60–90 Wörter mit einer passenden Anrede und einem Gruß. "
    "Dies ist eine originale interne B1-Schreibübung, keine offizielle Prüfungsaufgabe. "
    "Eine berechtigte Person bewertet Ihre tatsächlich eingereichte Antwort anhand "
    "einer zuvor menschlich freigegebenen Rubrik. Es wird keine KI-Bewertung ausgeführt."
)
WRITING_POLICY_CONTENT = (
    f"{WRITING_LABEL}\n"
    "Original internal LANGUAGE/de/B1/PRACTICE Writing policy for local development.\n"
    "One LONG_TEXT response is assessed by authorized humans against an exact published rubric.\n"
    "TASK and LANGUAGE each award 0–3 points; their SUM is a raw practice score out of 6.\n"
    "These are original practice descriptors, not official CEFR or provider scoring thresholds.\n"
    "A distinct authorized human reviews the proposed values before assessment approval.\n"
    "No AI provider, AI run, official qualification, certificate or release is supplied.\n"
    "This is explicit seeded human-approved internal policy metadata, not ingestion evidence.\n"
    "No uploaded object, extracted page, ingestion job or successful ingestion is claimed.\n"
)
WRITING_CRITERIA = [
    {
        "code": "TASK",
        "name": "Aufgabe und Verständlichkeit",
        "max_points": "3",
        "weight": "1",
        "descriptors": {
            "NOT_YET": "Die Nachricht bearbeitet die drei Anliegen noch nicht verständlich.",
            "DEVELOPING": "Die Nachricht behandelt einige Anliegen verständlich.",
            "CLEAR": "Erklärt das Fehlen, fragt nach Aufgaben und nennt die Rückkehr.",
        },
    },
    {
        "code": "LANGUAGE",
        "name": "Sprache und Zusammenhang",
        "max_points": "3",
        "weight": "1",
        "descriptors": {
            "NOT_YET": "Die sprachlichen Mittel reichen noch nicht für die Nachricht.",
            "DEVELOPING": "Einfache Sätze vermitteln die Absicht trotz Unsicherheiten.",
            "CLEAR": "Passende verbundene Sätze und Anrede vermitteln die Nachricht verständlich.",
        },
    },
]
WRITING_LEVELS = [
    {"code": "NOT_YET", "name": "Noch nicht erkennbar"},
    {"code": "DEVELOPING", "name": "Teilweise verständlich"},
    {"code": "CLEAR", "name": "Klar verständlich"},
]
_NAMESPACE = uuid.UUID("42abfb7d-c936-4db0-92ce-9cb0d3dfca72")
Model = TypeVar("Model", bound=Base)


def writing_id(key: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, key)


def _ensure(session: Session, model: type[Model], key: str, **values: Any) -> Model:
    row = session.get(model, writing_id(key))
    if row is None:
        row = model(id=writing_id(key), **values)
        session.add(row)
        session.flush()
    else:
        # Namespace collisions fail closed; no tenant identity or immutable
        # parent/content linkage is changed to repair an unrelated record.
        for name in (
            "organisation_id",
            "user_id",
            "standard_pack_id",
            "standard_pack_version_id",
            "module_id",
            "section_id",
            "exam_id",
            "exam_version_id",
            "exam_version_module_id",
            "exam_version_section_id",
            "exam_version_task_id",
            "question_bank_item_version_id",
            "standard_pack_task_spec_id",
            "standard_pack_module_id",
            "standard_pack_section_id",
            "source_id",
            "authority_id",
            "document_version_id",
            "knowledge_source_id",
            "code",
            "source_code",
            "version_label",
        ):
            if name in values and getattr(row, name) != values[name]:
                raise RuntimeError("Writing demo namespace collision; no record was rewritten.")
    return row


def _candidate(
    session: Session, settings: Settings, organisation: Organisation, password: str
) -> User:
    identifier = writing_id("candidate")
    user = session.get(User, identifier)
    by_email = session.scalar(select(User).where(User.normalized_email == WRITING_CANDIDATE_EMAIL))
    if (by_email is not None and by_email.id != identifier) or (
        user is not None and user.normalized_email != WRITING_CANDIDATE_EMAIL
    ):
        raise RuntimeError("Writing demo identity collision; no account was claimed.")
    if user is None:
        user = User(
            id=identifier,
            email=WRITING_CANDIDATE_EMAIL,
            normalized_email=WRITING_CANDIDATE_EMAIL,
            display_name=WRITING_CANDIDATE_NAME,
            status=UserStatus.ACTIVE,
        )
        session.add(user)
        session.flush()
    if session.scalar(
        select(OrganisationMembership.id)
        .where(
            OrganisationMembership.user_id == user.id,
            OrganisationMembership.organisation_id != organisation.id,
        )
        .limit(1)
    ):
        raise RuntimeError("Writing demo identity has another tenant; no membership was changed.")
    credential = user.password_credential
    if credential is None or not verify_password(credential.password_hash, password):
        AuthenticationService(settings).set_password(session, user, password)
    membership = _ensure(
        session,
        OrganisationMembership,
        "candidate-membership",
        organisation_id=organisation.id,
        user_id=user.id,
        status=MembershipStatus.ACTIVE,
    )
    role = session.scalar(
        select(Role).where(Role.key == "EXAMINER", Role.scope == RoleScope.TENANT)
    )
    if role is None:
        raise RuntimeError("Writing demo requires the repository EXAMINER role.")
    if session.get(MembershipRole, (membership.id, role.id)) is None:
        session.add(MembershipRole(membership_id=membership.id, role_id=role.id))
    session.flush()
    return user


def reconcile_writing_demo(
    session: Session,
    settings: Settings,
    *,
    enabled: bool,
    organisation: Organisation,
    administrator: User,
    platform_operator: User,
    password: str,
) -> dict[str, str]:
    if settings.environment != "development" or not enabled:
        raise RuntimeError(
            "Writing demo requires ENVIRONMENT=development and PRUFAI_DEMO_ENABLED=1."
        )
    now = datetime.now(timezone.utc)
    candidate = _candidate(session, settings, organisation, password)
    tenant = build_tenant_context(session, administrator.id, organisation.id)
    platform = build_tenant_context(session, platform_operator.id)
    scoped_operator = build_tenant_context(session, platform_operator.id, organisation.id)
    pack = _ensure(
        session,
        StandardPack,
        "pack",
        code="PRUFAI_HUMAN_WRITING_B1",
        name=f"Original human B1 Writing practice — {WRITING_LABEL}",
        assessment_family=AssessmentFamily.LANGUAGE,
        language="de",
        status=StandardPackStatus.DRAFT,
    )
    pack_version = _ensure(
        session,
        StandardPackVersion,
        "pack-version",
        standard_pack_id=pack.id,
        version_label=WRITING_VERSION_LABEL,
        status=StandardPackStatus.DRAFT,
        configuration_checksum="pending-writing-validation",
        supported_levels=["B1"],
        allowed_classifications=["PRACTICE"],
        created_at=now,
        created_by_user_id=administrator.id,
        notes=WRITING_LABEL,
    )
    module = _ensure(
        session,
        StandardPackModule,
        "pack-module",
        standard_pack_version_id=pack_version.id,
        code="WRITING",
        name="Human Writing",
        skill="WRITING",
        sequence=1,
        navigation_mode=PackNavigationMode.FREE_WITHIN_MODULE,
        created_at=now,
    )
    section = _ensure(
        session,
        StandardPackSection,
        "pack-section",
        module_id=module.id,
        code="MESSAGE",
        name="Original German message",
        sequence=1,
        navigation_mode=PackNavigationMode.FREE_WITHIN_MODULE,
        created_at=now,
    )
    task = _ensure(
        session,
        StandardPackTaskSpecification,
        "pack-task",
        section_id=section.id,
        code="WRITING_1",
        name="Write to the course teacher",
        sequence=1,
        task_type="EXTENDED_WRITING",
        min_items=1,
        max_items=1,
        response_mode=PackResponseMode.LONG_TEXT,
        media_requirement=PackMediaRequirement.NONE,
        created_at=now,
    )
    authority = _ensure(
        session,
        KnowledgeAuthority,
        "policy-authority",
        code="PRUFAI_HUMAN_WRITING_INTERNAL",
        name=f"Internal original Writing policy — {WRITING_LABEL}",
        authority_type=KnowledgeAuthorityType.INTERNAL,
    )
    source = _ensure(
        session,
        KnowledgeSource,
        "policy-source",
        authority_id=authority.id,
        source_code="PRUFAI_HUMAN_WRITING_B1_POLICY",
        title=f"Original Writing policy — {WRITING_LABEL}",
        document_type=KnowledgeDocumentType.POLICY,
        scope=KnowledgeScope.PLATFORM_SHARED,
        assessment_family=AssessmentFamily.LANGUAGE,
        language="de",
        cefr_level="B1",
        notes="Seeded original internal practice prerequisite. No external provider authorization.",
    )
    policy_hash = hashlib.sha256(WRITING_POLICY_CONTENT.encode()).hexdigest()
    document = _ensure(
        session,
        KnowledgeDocumentVersion,
        "policy-document",
        source_id=source.id,
        version_label=WRITING_VERSION_LABEL,
        status=KnowledgeVersionStatus.APPROVED,
        sha256=policy_hash,
        notes=WRITING_POLICY_CONTENT,
        created_at=now,
        created_by_user_id=platform_operator.id,
        reviewed_at=now,
        reviewed_by_user_id=platform_operator.id,
        approved_at=now,
        approved_by_user_id=platform_operator.id,
    )
    if document.sha256 != policy_hash or document.notes != WRITING_POLICY_CONTENT:
        raise RuntimeError("Writing internal policy changed; no published metadata was rewritten.")
    _ensure(
        session,
        KnowledgeLegalMetadata,
        "policy-rights",
        document_version_id=document.id,
        copyright_status="PRUFAI_ORIGINAL",
        licence_type="ORIGINAL_SYNTHETIC_DEVELOPMENT_POLICY",
        rights_holder="PrüfAI synthetic development fixture",
        usage_rights=WRITING_LABEL,
        legal_review_status=LegalReviewStatus.APPROVED,
        legal_reviewed_at=now,
        legal_reviewed_by_user_id=platform_operator.id,
    )
    _ensure(
        session,
        StandardPackSourceBinding,
        "policy-binding",
        standard_pack_version_id=pack_version.id,
        knowledge_source_id=source.id,
        document_version_id=document.id,
        document_checksum=policy_hash,
        binding_type=StandardPackBindingType.NORMATIVE,
        purpose="Original synthetic human Writing policy; no ingestion or official material.",
        required=True,
        created_at=now,
    )
    session.expire_all()
    for pack_current, pack_target in (
        (StandardPackStatus.DRAFT, StandardPackStatus.VALIDATION_REQUIRED),
        (StandardPackStatus.VALIDATION_REQUIRED, StandardPackStatus.HUMAN_REVIEW),
        (StandardPackStatus.HUMAN_REVIEW, StandardPackStatus.APPROVED),
        (StandardPackStatus.APPROVED, StandardPackStatus.PUBLISHED),
        (StandardPackStatus.PUBLISHED, StandardPackStatus.ACTIVE),
    ):
        if pack_version.status is pack_current:
            StandardPackService.transition(
                session, pack_version, pack_target, actor_id=platform_operator.id, context=platform
            )
    rubric = _ensure(
        session,
        AssessmentRubric,
        "rubric",
        code="PRUFAI_HUMAN_WRITING_B1",
        name=f"Original human Writing rubric — {WRITING_LABEL}",
        organisation_id=organisation.id,
        status=RubricVersionStatus.DRAFT,
    )
    rubric_version = session.scalar(
        select(RubricVersion).where(
            RubricVersion.rubric_id == rubric.id,
            RubricVersion.version_label == WRITING_VERSION_LABEL,
        )
    )
    if rubric_version is None:
        rubric_version = RubricService.create_version(
            session,
            rubric=rubric,
            standard_pack=pack_version,
            task_spec_id=task.id,
            version_label=WRITING_VERSION_LABEL,
            criteria=WRITING_CRITERIA,
            performance_levels=WRITING_LEVELS,
            aggregation_policy={"method": "SUM"},
            evidence_policy={"required": True, "minimum_references": 1},
            ai_assessment_policy={"enabled": False, "mode": "HUMAN_ONLY"},
            human_review_policy={"required": True, "separation_of_duties": True},
            evidence_bindings=[
                {
                    "knowledge_document_version_id": str(document.id),
                    "source_checksum": policy_hash,
                    "purpose": "ORIGINAL_PRACTICE_POLICY",
                    "reference": {"fixture": "ORIGINAL_SYNTHETIC_INTERNAL_POLICY"},
                }
            ],
            context=tenant,
        )
    if (
        rubric_version.standard_pack_version_id != pack_version.id
        or rubric_version.task_spec_id != task.id
    ):
        raise RuntimeError("Writing rubric scope collision; no rubric version was rewritten.")
    item = _ensure(
        session,
        QuestionBankItem,
        "item",
        code="PRUFAI_HUMAN_WRITING_B1_MESSAGE",
        scope_type="ORGANISATION_PRIVATE",
        organisation_id=organisation.id,
        status=QuestionBankLifecycleStatus.DRAFT,
        created_by_user_id=administrator.id,
    )
    item_version = session.scalar(
        select(QuestionBankItemVersion).where(
            QuestionBankItemVersion.item_id == item.id,
            QuestionBankItemVersion.version_label == WRITING_VERSION_LABEL,
        )
    )
    if item_version is None:
        item_version = QuestionBankService.create_version(
            session,
            tenant,
            item,
            version_label=WRITING_VERSION_LABEL,
            assessment_family=AssessmentFamily.LANGUAGE,
            language="de",
            cefr_levels=["B1"],
            skill=QuestionBankSkill.WRITING,
            task_type=QuestionBankTaskType.EXTENDED_WRITING,
            response_mode=PackResponseMode.LONG_TEXT,
            content={"kind": "EXTENDED_WRITING", "prompt": WRITING_PROMPT},
            scoring_policy=QuestionBankScoringPolicy.RUBRIC_REQUIRED,
            max_points=Decimal("6"),
            scoring_metadata={"rubric_required": True},
            rubric_reference=str(rubric_version.id),
            rights_status=QuestionBankRightsStatus.PRUFAI_ORIGINAL,
            rights_holder="PrüfAI original synthetic development fixture",
        )
    if not session.scalar(
        select(QuestionBankStandardPackBinding.id).where(
            QuestionBankStandardPackBinding.item_version_id == item_version.id,
            QuestionBankStandardPackBinding.standard_pack_version_id == pack_version.id,
            QuestionBankStandardPackBinding.task_spec_id == task.id,
        )
    ):
        QuestionBankService.bind_pack(session, tenant, item_version, pack_version.id, task.id)
    if item_version.rubric_reference != str(rubric_version.id):
        raise RuntimeError(
            "Writing item rubric reference changed; no published item was rewritten."
        )
    if item_version.status in {
        QuestionBankLifecycleStatus.DRAFT,
        QuestionBankLifecycleStatus.VALIDATION_REQUIRED,
    }:
        QuestionBankService.request_review(session, scoped_operator, item_version)
    validation = QuestionBankService.validate_pack_compatibility(
        session, item_version, pack_version.id, task.id
    )
    for item_current, item_target in (
        (QuestionBankLifecycleStatus.HUMAN_REVIEW, QuestionBankLifecycleStatus.APPROVED),
        (QuestionBankLifecycleStatus.APPROVED, QuestionBankLifecycleStatus.PUBLISHED),
        (QuestionBankLifecycleStatus.PUBLISHED, QuestionBankLifecycleStatus.ACTIVE),
    ):
        if item_version.status is item_current:
            QuestionBankService.transition(
                session,
                scoped_operator,
                item_version,
                item_target,
                validation=validation,
                checksum=item_version.checksum,
            )
    exam = _ensure(
        session,
        Exam,
        "exam",
        code="PRUFAI_HUMAN_WRITING_B1",
        name=WRITING_EXAM_NAME,
        description="Original HUMAN Writing practice; rubric publication requires human action.",
        scope_type="ORGANISATION_PRIVATE",
        organisation_id=organisation.id,
        created_by_user_id=administrator.id,
        created_at=now,
    )
    exam_version = _ensure(
        session,
        ExamVersion,
        "exam-version",
        exam_id=exam.id,
        version_label=WRITING_VERSION_LABEL,
        status=ExamVersionStatus.DRAFT,
        standard_pack_version_id=pack_version.id,
        classification=AssessmentClassification.PRACTICE,
        origin_type=ExamOriginType.HUMAN_AUTHORED,
        created_at=now,
        created_by_user_id=administrator.id,
        checksum="pending-writing-validation",
    )
    exam_module = _ensure(
        session,
        ExamVersionModule,
        "exam-module",
        exam_version_id=exam_version.id,
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
    session.expire_all()
    if exam_version.status in {ExamVersionStatus.DRAFT, ExamVersionStatus.VALIDATION_REQUIRED}:
        ExamStudioService._refresh_checksum(session, exam_version)
        ExamStudioService.request_review(session, scoped_operator, exam_version)
    for exam_current, exam_target in (
        (ExamVersionStatus.HUMAN_REVIEW, ExamVersionStatus.APPROVED),
        (ExamVersionStatus.APPROVED, ExamVersionStatus.PUBLISHED),
        (ExamVersionStatus.PUBLISHED, ExamVersionStatus.ACTIVE),
    ):
        if exam_version.status is exam_current:
            ExamStudioService.transition(session, scoped_operator, exam_version, exam_target)
    if ExamStudioService.validate(session, tenant, exam_version).status != "PASS":
        raise RuntimeError("Writing demo exam failed real Exam Studio validation.")
    CandidateRuntimeService._safe_plan(session, exam_version)
    return {
        "writing_candidate_user_id": str(candidate.id),
        "writing_exam_version_id": str(exam_version.id),
        "writing_pack_version_id": str(pack_version.id),
        "writing_item_version_id": str(item_version.id),
        "writing_task_spec_id": str(task.id),
        "writing_rubric_id": str(rubric.id),
        "writing_rubric_version_id": str(rubric_version.id),
    }
