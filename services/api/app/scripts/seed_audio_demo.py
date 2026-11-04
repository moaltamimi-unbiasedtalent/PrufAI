"""Original unsubmitted AUDIO-only development prerequisites; no assessed outcome.

The explicit development operator seeds internal policy metadata, not an uploaded
or ingested source. Pack, item and exam versions use their real governance
transitions. There are no seeded recordings, transcripts, assessments or scores.
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

AUDIO_CANDIDATE_EMAIL = "audio.candidate@demo.prufai.local"
AUDIO_CANDIDATE_NAME = "Recorded Audio Practice Candidate"
AUDIO_LABEL = "SYNTHETIC DEMO DATA — NOT OFFICIAL MATERIAL"
AUDIO_EXAM_NAME = f"Recorded B1 German Audio Practice — {AUDIO_LABEL}"
AUDIO_VERSION_LABEL = "recorded-audio-b1-v1"
AUDIO_POLICY = {
    "max_recording_seconds": 30,
    "max_recording_attempts": 2,
    "max_upload_bytes": 2 * 1024 * 1024,
    "supported_mime_types": [
        "audio/webm;codecs=opus",
        "audio/ogg;codecs=opus",
        "audio/mp4;codecs=mp4a.40.2",
        "audio/wav",
    ],
    "submission_behavior": "REQUIRE_ATTACHED",
    "upload_grace_seconds": 120,
}
AUDIO_TASKS = (
    (
        "INTRODUCTION",
        "Aufgabe 1: Sich vorstellen",
        "Stellen Sie sich kurz auf Deutsch vor. Verwenden Sie einen erfundenen Namen. "
        "Nennen Sie ein Hobby und erklären Sie, warum es Ihnen gefällt.",
    ),
    (
        "DAY_PLAN",
        "Aufgabe 2: Einen Tagesplan beschreiben",
        "Beschreiben Sie auf Deutsch einen erfundenen freien Tag. "
        "Was möchten Sie am Morgen und am Nachmittag machen? Erklären Sie Ihre Wahl.",
    ),
)
AUDIO_POLICY_CONTENT = (
    f"{AUDIO_LABEL}\n"
    "Original internal LANGUAGE/de/B1/PRACTICE recorded-audio evidence policy.\n"
    "Two original oral-response tasks require separate private AUDIO evidence.\n"
    "Each immutable item version pins its typed recording limits and supported formats.\n"
    "The server validates media, duration, checksum and exact response identity.\n"
    "An uploaded READY object is not a saved response until authoritative attachment succeeds.\n"
    "This fixture provides no speaking score, rubric approval, transcript, AI run or certificate.\n"
    "Original internal policy metadata is an explicit development prerequisite only.\n"
    "No uploaded source object, extracted page or ingestion job/success is claimed.\n"
    "No retention period or automatic evidence deletion is authorized here.\n"
)
_NAMESPACE = uuid.UUID("0683a4fb-053a-4ff1-b8df-92cb87b3c754")
Model = TypeVar("Model", bound=Base)


def audio_id(key: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, key)


def _ensure(
    session: Session, model: type[Model], key: str, *, namespace: str = "", **values: Any
) -> Model:
    row = session.get(model, audio_id(namespace + key))
    if row is None:
        row = model(id=audio_id(namespace + key), **values)
        session.add(row)
        session.flush()
    else:
        for name in (
            "organisation_id",
            "scope_type",
            "scope",
            "user_id",
            "standard_pack_id",
            "standard_pack_version_id",
            "module_id",
            "section_id",
            "source_id",
            "authority_id",
            "document_version_id",
            "knowledge_source_id",
            "code",
            "source_code",
            "version_label",
        ):
            if name in values and getattr(row, name) != values[name]:
                raise RuntimeError("Audio demo namespace collision; no record was rewritten.")
    return row


def _candidate(
    session: Session,
    settings: Settings,
    organisation: Organisation,
    password: str,
    *,
    namespace: str = "",
    email: str = AUDIO_CANDIDATE_EMAIL,
    name: str = AUDIO_CANDIDATE_NAME,
) -> User:
    identifier = audio_id(namespace + "candidate")
    user = session.get(User, identifier)
    existing = session.scalar(select(User).where(User.normalized_email == email))
    if (existing is not None and existing.id != identifier) or (
        user is not None and user.normalized_email != email
    ):
        raise RuntimeError("Audio demo identity collision; no account was claimed.")
    if user is None:
        user = User(
            id=identifier,
            email=email,
            normalized_email=email,
            display_name=name,
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
        raise RuntimeError("Audio demo identity belongs to another tenant; no membership changed.")
    if user.password_credential is None or not verify_password(
        user.password_credential.password_hash, password
    ):
        AuthenticationService(settings).set_password(session, user, password)
    membership = _ensure(
        session,
        OrganisationMembership,
        "candidate-membership",
        namespace=namespace,
        organisation_id=organisation.id,
        user_id=user.id,
        status=MembershipStatus.ACTIVE,
    )
    role = session.scalar(
        select(Role).where(Role.key == "EXAMINER", Role.scope == RoleScope.TENANT)
    )
    if role is None:
        raise RuntimeError("Audio demo requires the existing EXAMINER role.")
    if session.get(MembershipRole, (membership.id, role.id)) is None:
        session.add(MembershipRole(membership_id=membership.id, role_id=role.id))
    session.flush()
    return user


def reconcile_audio_demo(
    session: Session,
    settings: Settings,
    *,
    enabled: bool,
    organisation: Organisation,
    administrator: User,
    platform_operator: User,
    password: str,
    human_speaking: bool = False,
    speaking_results: bool = False,
    transcription_execution: bool = False,
    ai_proposals: bool = False,
    ai_policy: dict[str, Any] | None = None,
) -> dict[str, str]:
    if settings.environment != "development" or not enabled:
        raise RuntimeError("Audio demo requires ENVIRONMENT=development and PRUFAI_DEMO_ENABLED=1.")
    if ai_proposals and (human_speaking or speaking_results or transcription_execution):
        raise RuntimeError("AI Speaking prerequisites require their isolated scenario.")
    if ai_proposals and (
        not ai_policy
        or ai_policy.get("enabled") is not True
        or ai_policy.get("mode") != "AI_ASSISTED"
        or ai_policy.get("evidence_mode") != "AUDIO"
        or not ai_policy.get("execution_profile_id")
        or not ai_policy.get("execution_profile_version")
    ):
        raise RuntimeError("AI Speaking prerequisites require an exact approved AUDIO profile.")
    # A separate immutable namespace preserves the previously published evidence-only
    # scenario. The human scenario adds a DRAFT rubric, never a judged recording.
    from app.scripts.seed_speaking_human_demo import (
        SPEAKING_HUMAN_CRITERIA,
        SPEAKING_HUMAN_EXAM_NAME,
        SPEAKING_HUMAN_LEVELS,
        SPEAKING_HUMAN_POLICY_CONTENT,
        SPEAKING_HUMAN_VERSION_LABEL,
    )

    human_speaking = human_speaking or speaking_results
    namespace = "human-speaking-" if human_speaking else ""
    version_label = SPEAKING_HUMAN_VERSION_LABEL if human_speaking else AUDIO_VERSION_LABEL
    scenario_code = "PRUFAI_HUMAN_SPEAKING_B1" if human_speaking else "PRUFAI_RECORDED_AUDIO_B1"
    scenario_name = SPEAKING_HUMAN_EXAM_NAME if human_speaking else AUDIO_EXAM_NAME
    policy_content = SPEAKING_HUMAN_POLICY_CONTENT if human_speaking else AUDIO_POLICY_CONTENT
    candidate_email = (
        "speaking.candidate@demo.prufai.local" if human_speaking else AUDIO_CANDIDATE_EMAIL
    )
    candidate_name = "Human Speaking Practice Candidate" if human_speaking else AUDIO_CANDIDATE_NAME
    if speaking_results:
        from app.scripts.seed_speaking_results_demo import (
            SPEAKING_RESULTS_CANDIDATE_EMAIL,
            SPEAKING_RESULTS_EXAM_NAME,
            SPEAKING_RESULTS_VERSION_LABEL,
        )

        namespace = "speaking-results-"
        version_label = SPEAKING_RESULTS_VERSION_LABEL
        scenario_code = "PRUFAI_SPEAKING_RESULTS_B1"
        scenario_name = SPEAKING_RESULTS_EXAM_NAME
        candidate_email = SPEAKING_RESULTS_CANDIDATE_EMAIL
        candidate_name = "Speaking Results Practice Candidate"
        policy_content = SPEAKING_HUMAN_POLICY_CONTENT + (
            "This independent original development scenario supports a real human-governed "
            "Speaking result journey. No score, result, statement or release is seeded.\n"
        )
    if transcription_execution:
        from app.scripts.seed_transcription_execution_demo import (
            TRANSCRIPTION_CANDIDATE_EMAIL,
            TRANSCRIPTION_EXAM_NAME,
            TRANSCRIPTION_POLICY_CONTENT,
        )

        namespace = "transcription-execution-"
        version_label = "transcription-execution-b1-v1"
        scenario_code = "PRUFAI_TRANSCRIPTION_EXECUTION_B1"
        scenario_name = TRANSCRIPTION_EXAM_NAME
        policy_content = TRANSCRIPTION_POLICY_CONTENT
        candidate_email = TRANSCRIPTION_CANDIDATE_EMAIL
        candidate_name = "Transcription Execution Practice Candidate"
    if ai_proposals:
        from app.scripts.seed_ai_speaking_demo import (
            AI_SPEAKING_CANDIDATE_EMAIL,
            AI_SPEAKING_EXAM_NAME,
            AI_SPEAKING_POLICY_CONTENT,
        )

        namespace = "ai-speaking-execution-"
        version_label = "ai-speaking-execution-b1-v1"
        scenario_code = "PRUFAI_AI_SPEAKING_B1"
        scenario_name = AI_SPEAKING_EXAM_NAME
        policy_content = AI_SPEAKING_POLICY_CONTENT
        candidate_email = AI_SPEAKING_CANDIDATE_EMAIL
        candidate_name = "AI Speaking Technical Practice Candidate"
    assessed_speaking = human_speaking or ai_proposals
    tasks = AUDIO_TASKS[:1] if assessed_speaking or transcription_execution else AUDIO_TASKS

    def ensure(model: type[Model], key: str, **values: Any) -> Model:
        return _ensure(session, model, key, namespace=namespace, **values)

    def identifier(key: str) -> uuid.UUID:
        return audio_id(namespace + key)

    now = datetime.now(timezone.utc)
    candidate = _candidate(
        session,
        settings,
        organisation,
        password,
        namespace=namespace,
        email=candidate_email,
        name=candidate_name,
    )
    author = build_tenant_context(session, administrator.id, organisation.id)
    publisher = build_tenant_context(session, platform_operator.id, organisation.id)
    pack = ensure(
        StandardPack,
        "pack",
        code=scenario_code,
        name=(
            scenario_name
            if assessed_speaking or transcription_execution
            else f"Original recorded audio practice — {AUDIO_LABEL}"
        ),
        assessment_family=AssessmentFamily.LANGUAGE,
        language="de",
        status=StandardPackStatus.DRAFT,
    )
    version = ensure(
        StandardPackVersion,
        "pack-version",
        standard_pack_id=pack.id,
        version_label=version_label,
        status=StandardPackStatus.DRAFT,
        configuration_checksum="pending-audio-validation",
        supported_levels=["B1"],
        allowed_classifications=["PRACTICE"],
        created_at=now,
        created_by_user_id=administrator.id,
        notes=AUDIO_LABEL,
    )
    module = ensure(
        StandardPackModule,
        "pack-module",
        standard_pack_version_id=version.id,
        code="RECORDED_AUDIO",
        name="Recorded audio evidence",
        skill="SPEAKING",
        sequence=1,
        navigation_mode=PackNavigationMode.FREE_WITHIN_MODULE,
        created_at=now,
    )
    section = ensure(
        StandardPackSection,
        "pack-section",
        module_id=module.id,
        code="ORIGINAL",
        name="Original practice recordings",
        sequence=1,
        navigation_mode=PackNavigationMode.FREE_WITHIN_MODULE,
        created_at=now,
    )
    specifications = [
        ensure(
            StandardPackTaskSpecification,
            f"task-{index}",
            section_id=section.id,
            code=code,
            name=name,
            sequence=index,
            task_type="ORAL_RESPONSE",
            min_items=1,
            max_items=1,
            response_mode=PackResponseMode.AUDIO,
            media_requirement=PackMediaRequirement.NONE,
            created_at=now,
        )
        for index, (code, name, _prompt) in enumerate(tasks, 1)
    ]
    authority = ensure(
        KnowledgeAuthority,
        "policy-authority",
        code="PRUFAI_AI_SPEAKING_INTERNAL"
        if ai_proposals
        else "PRUFAI_TRANSCRIPTION_EXECUTION_INTERNAL"
        if transcription_execution
        else "PRUFAI_SPEAKING_RESULTS_INTERNAL"
        if speaking_results
        else "PRUFAI_HUMAN_SPEAKING_INTERNAL"
        if human_speaking
        else "PRUFAI_RECORDED_AUDIO_INTERNAL",
        name=f"Original internal audio policy — {AUDIO_LABEL}",
        authority_type=KnowledgeAuthorityType.INTERNAL,
    )
    source = ensure(
        KnowledgeSource,
        "policy-source",
        authority_id=authority.id,
        source_code=scenario_code + "_POLICY",
        title=f"Audio evidence policy — {AUDIO_LABEL}",
        document_type=KnowledgeDocumentType.POLICY,
        scope=KnowledgeScope.PLATFORM_SHARED,
        assessment_family=AssessmentFamily.LANGUAGE,
        language="de",
        cefr_level="B1",
        notes="Explicit original synthetic development policy; not external source ingestion.",
    )
    digest = hashlib.sha256(policy_content.encode()).hexdigest()
    document = ensure(
        KnowledgeDocumentVersion,
        "policy-document",
        source_id=source.id,
        version_label=version_label,
        status=KnowledgeVersionStatus.APPROVED,
        sha256=digest,
        notes=policy_content,
        created_at=now,
        created_by_user_id=platform_operator.id,
        reviewed_at=now,
        reviewed_by_user_id=platform_operator.id,
        approved_at=now,
        approved_by_user_id=platform_operator.id,
    )
    if document.sha256 != digest or document.notes != policy_content:
        raise RuntimeError("Audio policy metadata changed; no published policy was rewritten.")
    ensure(
        KnowledgeLegalMetadata,
        "policy-rights",
        document_version_id=document.id,
        copyright_status="PRUFAI_ORIGINAL",
        licence_type="ORIGINAL_SYNTHETIC_DEVELOPMENT_POLICY",
        rights_holder="PrüfAI original development fixture",
        usage_rights=AUDIO_LABEL,
        legal_review_status=LegalReviewStatus.APPROVED,
        legal_reviewed_at=now,
        legal_reviewed_by_user_id=platform_operator.id,
    )
    ensure(
        StandardPackSourceBinding,
        "policy-binding",
        standard_pack_version_id=version.id,
        knowledge_source_id=source.id,
        document_version_id=document.id,
        document_checksum=digest,
        binding_type=StandardPackBindingType.NORMATIVE,
        purpose="Original private audio evidence policy",
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
        if version.status is pack_current:
            StandardPackService.transition(
                session, version, pack_target, actor_id=platform_operator.id, context=publisher
            )
    rubric_version = None
    if assessed_speaking:
        rubric = ensure(
            AssessmentRubric,
            "rubric",
            code=scenario_code,
            name=(
                f"Original {'TEST ONLY AI-assisted' if ai_proposals else 'human'} "
                f"Speaking rubric — {AUDIO_LABEL}"
            ),
            organisation_id=organisation.id,
            status=RubricVersionStatus.DRAFT,
        )
        rubric_version = session.scalar(
            select(RubricVersion).where(
                RubricVersion.rubric_id == rubric.id,
                RubricVersion.version_label == version_label,
            )
        )
        if rubric_version is None:
            rubric_version = RubricService.create_version(
                session,
                rubric=rubric,
                standard_pack=version,
                task_spec_id=specifications[0].id,
                version_label=version_label,
                skill=QuestionBankSkill.SPEAKING,
                criteria=SPEAKING_HUMAN_CRITERIA,
                performance_levels=SPEAKING_HUMAN_LEVELS,
                aggregation_policy={"method": "SUM"},
                evidence_policy={
                    "mode": "AUDIO",
                    "required": True,
                    "minimum_references": 1,
                    "time_unit": "milliseconds",
                    **({"source_backed": False} if ai_proposals else {}),
                },
                ai_assessment_policy=dict(ai_policy or {})
                if ai_proposals
                else {"enabled": False, "mode": "HUMAN_ONLY"},
                human_review_policy={"required": True, "separation_of_duties": True},
                evidence_bindings=[
                    {
                        "knowledge_document_version_id": str(document.id),
                        "source_checksum": digest,
                        "purpose": "ORIGINAL_PRACTICE_POLICY",
                        **({"required": False} if ai_proposals else {}),
                    }
                ],
                context=author,
            )
        if (
            rubric_version.standard_pack_version_id != version.id
            or rubric_version.task_spec_id != specifications[0].id
        ):
            raise RuntimeError("Human Speaking rubric scope collision; no version was rewritten.")
    items: list[QuestionBankItemVersion] = []
    for index, ((code, name, prompt), task) in enumerate(zip(tasks, specifications), 1):
        item_code = f"{scenario_code}_{code}"
        item = session.get(QuestionBankItem, identifier(f"item-{index}"))
        if item is None:
            item = ensure(
                QuestionBankItem,
                f"item-{index}",
                code=item_code,
                scope_type="ORGANISATION_PRIVATE",
                organisation_id=organisation.id,
                status=QuestionBankLifecycleStatus.DRAFT,
                created_by_user_id=administrator.id,
            )
        if (
            item.organisation_id != organisation.id
            or item.code != item_code
            or item.scope_type != "ORGANISATION_PRIVATE"
        ):
            raise RuntimeError("Audio item scope collision; no item was rewritten.")
        item_version = session.scalar(
            select(QuestionBankItemVersion).where(
                QuestionBankItemVersion.item_id == item.id,
                QuestionBankItemVersion.version_label == version_label,
            )
        )
        if item_version is None:
            item_version = QuestionBankService.create_version(
                session,
                author,
                item,
                version_label=version_label,
                assessment_family=AssessmentFamily.LANGUAGE,
                language="de",
                cefr_levels=["B1"],
                skill=QuestionBankSkill.SPEAKING,
                task_type=QuestionBankTaskType.ORAL_RESPONSE,
                response_mode=PackResponseMode.AUDIO,
                content={
                    "kind": "ORAL_RESPONSE",
                    "prompt": f"{AUDIO_LABEL}\n{name}\n{prompt}\n"
                    + (
                        "TEST ONLY: Diese technische Übung prüft dauerhafte Verarbeitung. "
                        "Die Test-Transkription erkennt keine Sprache und vergibt keine Bewertung. "
                        if transcription_execution
                        else "TEST ONLY: Diese technische KI-Übung prüft Verarbeitung und "
                        "menschliche Freigabe. Der Testadapter versteht keine Sprache und "
                        "belegt keine echte KI-Bewertungsqualität. Originalaudio bleibt die "
                        "primäre Evidenz; keine automatische Bewertung oder Ergebnisfreigabe. "
                        if ai_proposals
                        else "Diese originale Übung wird von zwei berechtigten Menschen "
                        "anhand einer "
                        "zuvor veröffentlichten Rubrik geprüft. Keine KI oder Transkription. "
                        if human_speaking
                        else "Diese Übung speichert nur Ihre Aufnahme. "
                        "Keine Bewertung oder Transkription. "
                    )
                    + "Bitte nennen Sie keine echten Kontaktdaten.",
                    "audio_policy": AUDIO_POLICY,
                },
                scoring_policy=(
                    QuestionBankScoringPolicy.RUBRIC_REQUIRED
                    if assessed_speaking
                    else QuestionBankScoringPolicy.MANUAL_REVIEW
                ),
                max_points=Decimal("6") if assessed_speaking else None,
                scoring_metadata={"rubric_required": True} if assessed_speaking else {},
                rubric_reference=str(rubric_version.id) if rubric_version else None,
                rights_status=QuestionBankRightsStatus.PRUFAI_ORIGINAL,
                rights_holder="PrüfAI original synthetic practice",
            )
        if (
            session.scalar(
                select(QuestionBankStandardPackBinding.id).where(
                    QuestionBankStandardPackBinding.item_version_id == item_version.id,
                    QuestionBankStandardPackBinding.standard_pack_version_id == version.id,
                    QuestionBankStandardPackBinding.task_spec_id == task.id,
                )
            )
            is None
        ):
            QuestionBankService.bind_pack(session, author, item_version, version.id, task.id)
        if item_version.status in {
            QuestionBankLifecycleStatus.DRAFT,
            QuestionBankLifecycleStatus.VALIDATION_REQUIRED,
        }:
            QuestionBankService.request_review(session, publisher, item_version)
        validation = QuestionBankService.validate_pack_compatibility(
            session, item_version, version.id, task.id
        )
        for item_current, item_target in (
            (QuestionBankLifecycleStatus.HUMAN_REVIEW, QuestionBankLifecycleStatus.APPROVED),
            (QuestionBankLifecycleStatus.APPROVED, QuestionBankLifecycleStatus.PUBLISHED),
            (QuestionBankLifecycleStatus.PUBLISHED, QuestionBankLifecycleStatus.ACTIVE),
        ):
            if item_version.status is item_current:
                QuestionBankService.transition(
                    session,
                    publisher,
                    item_version,
                    item_target,
                    validation=validation,
                    checksum=item_version.checksum,
                )
        items.append(item_version)
    exam = session.get(Exam, identifier("exam"))
    if exam is None:
        exam = ensure(
            Exam,
            "exam",
            code=scenario_code,
            name=scenario_name,
            scope_type="ORGANISATION_PRIVATE",
            organisation_id=organisation.id,
            created_by_user_id=administrator.id,
            created_at=now,
        )
    if (
        exam.organisation_id != organisation.id
        or exam.scope_type != "ORGANISATION_PRIVATE"
        or exam.code != scenario_code
    ):
        raise RuntimeError("Audio exam scope collision; no exam was rewritten.")
    exam_version = session.scalar(
        select(ExamVersion).where(
            ExamVersion.exam_id == exam.id, ExamVersion.version_label == version_label
        )
    )
    if exam_version is None:
        exam_version = ExamStudioService.create_version(
            session,
            author,
            exam,
            version_label=version_label,
            standard_pack_version_id=version.id,
            classification=AssessmentClassification.PRACTICE,
        )
    if exam_version.status in {ExamVersionStatus.DRAFT, ExamVersionStatus.VALIDATION_REQUIRED}:
        if not exam_version.modules:
            ExamStudioService.materialize_blueprint(session, author, exam_version)
            session.expire(exam_version, ["modules"])
        for concrete, item_version in zip(exam_version.modules[0].sections[0].tasks, items):
            if not concrete.item_bindings:
                ExamStudioService.assign_item(
                    session,
                    author,
                    exam_version,
                    task_id=concrete.id,
                    item_version_id=item_version.id,
                    slot_index=0,
                )
    session.expire_all()
    if exam_version.status in {ExamVersionStatus.DRAFT, ExamVersionStatus.VALIDATION_REQUIRED}:
        ExamStudioService.request_review(session, publisher, exam_version)
    for exam_current, exam_target in (
        (ExamVersionStatus.HUMAN_REVIEW, ExamVersionStatus.APPROVED),
        (ExamVersionStatus.APPROVED, ExamVersionStatus.PUBLISHED),
        (ExamVersionStatus.PUBLISHED, ExamVersionStatus.ACTIVE),
    ):
        if exam_version.status is exam_current:
            ExamStudioService.transition(session, publisher, exam_version, exam_target)
    if ExamStudioService.validate(session, author, exam_version).status != "PASS":
        raise RuntimeError("Audio demo failed real Exam Studio validation.")
    CandidateRuntimeService._safe_plan(session, exam_version)
    if transcription_execution:
        return {
            "transcription_execution_candidate_user_id": str(candidate.id),
            "transcription_execution_candidate_email": candidate.email,
            "transcription_execution_exam_version_id": str(exam_version.id),
            "transcription_execution_exam_name": scenario_name,
            "transcription_execution_pack_version_id": str(version.id),
            "transcription_execution_item_version_id": str(items[0].id),
        }
    if assessed_speaking:
        assert rubric_version is not None
        prefix = (
            "ai_speaking"
            if ai_proposals
            else "speaking_results"
            if speaking_results
            else "speaking_human"
        )
        return {
            f"{prefix}_candidate_user_id": str(candidate.id),
            f"{prefix}_exam_version_id": str(exam_version.id),
            f"{prefix}_pack_version_id": str(version.id),
            f"{prefix}_item_version_id": str(items[0].id),
            f"{prefix}_task_spec_id": str(specifications[0].id),
            f"{prefix}_rubric_id": str(rubric_version.rubric_id),
            f"{prefix}_rubric_version_id": str(rubric_version.id),
        }
    return {
        "audio_candidate_user_id": str(candidate.id),
        "audio_exam_version_id": str(exam_version.id),
        "audio_pack_version_id": str(version.id),
        "audio_item_1_version_id": str(items[0].id),
        "audio_item_2_version_id": str(items[1].id),
        "audio_task_1_spec_id": str(specifications[0].id),
        "audio_task_2_spec_id": str(specifications[1].id),
    }
