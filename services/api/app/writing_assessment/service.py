from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import AuditService
from app.authorization import AuthorizationService, TenantContext
from app.models import (
    AgentVersion,
    AIRun,
    AssessmentRubric,
    CandidateExamAttempt,
    CandidateResponse,
    HumanReviewRequest,
    KnowledgeDocumentVersion,
    QuestionBankItemVersion,
    RubricEvidenceBinding,
    RubricVersion,
    StandardPackVersion,
    SubjectiveAssessment,
    SubjectiveAssessmentVersion,
    SubjectiveCriterionAssessment,
)
from app.models.enums import (
    AssessmentFamily,
    CandidateAttemptStatus,
    HumanReviewStatus,
    PackResponseMode,
    QuestionBankSkill,
    RubricVersionStatus,
    SubjectiveAssessmentStatus,
    SubjectiveConfidence,
    SubjectiveCriterionStatus,
)


class RubricError(ValueError):
    pass


def canonical_checksum(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def normalized_writing_input(
    *, candidate_text: str, task_prompt: str, language: str, cefr_levels: list[str]
) -> dict[str, Any]:
    """Build the narrow, explicitly untrusted model input for writing assessment."""
    return {
        "candidate_text": candidate_text,
        "candidate_text_untrusted": True,
        "task_prompt": task_prompt,
        "language": language,
        "cefr_levels": list(cefr_levels),
    }


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise RubricError("invalid Decimal rubric value") from exc


def _reject_executable(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in {"eval", "exec", "python", "expression", "executable"}:
                raise RubricError("rubric contains executable configuration")
            _reject_executable(child)
    elif isinstance(value, list):
        for child in value:
            _reject_executable(child)


def validate_rubric(
    *,
    criteria: list[dict[str, Any]],
    performance_levels: list[dict[str, Any]],
    aggregation_policy: dict[str, Any],
    evidence_policy: dict[str, Any],
    assessment_family: AssessmentFamily,
    language: str | None,
    skill: QuestionBankSkill,
    standard_pack: StandardPackVersion,
    schema_version: str,
) -> None:
    payload = {
        "criteria": criteria,
        "performance_levels": performance_levels,
        "aggregation_policy": aggregation_policy,
        "evidence_policy": evidence_policy,
    }
    _reject_executable(payload)
    if schema_version != "1":
        raise RubricError("unsupported rubric schema version")
    if assessment_family is not AssessmentFamily.LANGUAGE or skill is not QuestionBankSkill.WRITING:
        raise RubricError("writing rubric must be LANGUAGE / WRITING")
    if language != "de":
        raise RubricError("MVP writing assessment requires German language metadata")
    if not standard_pack or not standard_pack.configuration_checksum:
        raise RubricError("exact StandardPackVersion checksum is required")
    if not criteria:
        raise RubricError("rubric requires criteria")
    codes: set[str] = set()
    levels = {str(level.get("code")) for level in performance_levels}
    if not levels:
        raise RubricError("rubric requires performance levels")
    for criterion in criteria:
        code = str(criterion.get("code", ""))
        if not code or code in codes:
            raise RubricError("criterion codes must be unique")
        codes.add(code)
        maximum = _decimal(criterion.get("max_points"))
        if maximum < 0:
            raise RubricError("criterion max points must be non-negative")
        weight = _decimal(criterion.get("weight", "1"))
        if weight < 0:
            raise RubricError("criterion weights must be non-negative")
        descriptors = criterion.get("descriptors")
        if not isinstance(descriptors, dict) or set(descriptors) != levels:
            raise RubricError("every criterion requires every performance descriptor")
        for descriptor in descriptors.values():
            if not isinstance(descriptor, str) or not descriptor.strip():
                raise RubricError("performance descriptors must be non-empty text")
    method = aggregation_policy.get("method")
    if method not in {"SUM", "WEIGHTED_SUM", "DECLARED_MAPPING"}:
        raise RubricError("unsupported rubric aggregation method")
    if method == "WEIGHTED_SUM":
        total = sum((_decimal(item.get("weight", "0")) for item in criteria), Decimal("0"))
        if total not in {Decimal("1"), Decimal("100")}:
            raise RubricError("weighted rubric criteria must total 1 or 100")
    if evidence_policy.get("required") and evidence_policy.get("minimum_references", 0) < 1:
        raise RubricError("required rubric evidence must require a reference")


class RubricService:
    EXECUTABLE_STATUSES = {
        RubricVersionStatus.APPROVED,
        RubricVersionStatus.PUBLISHED,
        RubricVersionStatus.ACTIVE,
    }

    @staticmethod
    def checksum_payload(version: RubricVersion) -> dict[str, Any]:
        return {
            "schema_version": version.schema_version,
            "assessment_family": version.assessment_family.value,
            "language": version.language,
            "cefr_levels": version.cefr_levels,
            "skill": version.skill.value,
            "standard_pack_version_id": str(version.standard_pack_version_id),
            "criteria": version.criteria,
            "performance_levels": version.performance_levels,
            "aggregation_policy": version.aggregation_policy,
            "evidence_policy": version.evidence_policy,
            "ai_assessment_policy": version.ai_assessment_policy,
            "human_review_policy": version.human_review_policy,
            "evidence": [
                {
                    "document_version_id": str(binding.knowledge_document_version_id),
                    "page_id": str(binding.knowledge_page_id)
                    if binding.knowledge_page_id
                    else None,
                    "chunk_id": str(binding.knowledge_chunk_id)
                    if binding.knowledge_chunk_id
                    else None,
                    "reference": binding.reference,
                    "purpose": binding.purpose,
                    "source_checksum": binding.source_checksum,
                    "required": binding.required,
                }
                for binding in version.evidence_bindings
            ],
        }

    @staticmethod
    def create_rubric(
        session: Session,
        *,
        code: str,
        name: str,
        organisation_id: uuid.UUID | None,
        context: TenantContext | None = None,
    ) -> AssessmentRubric:
        rubric = AssessmentRubric(
            code=code,
            name=name,
            organisation_id=organisation_id,
            status=RubricVersionStatus.DRAFT,
        )
        session.add(rubric)
        session.flush()
        RubricService._audit(session, context, "rubric.created", rubric)
        return rubric

    @staticmethod
    def _audit(session: Session, context: TenantContext | None, event: str, entity: Any) -> None:
        if (
            context is not None
            and context.organisation_id
            and AuthorizationService.has_permission(context, "audit.read", context.organisation_id)
        ):
            AuditService.record(
                session,
                context,
                event_type=event,
                entity_type=type(entity).__name__,
                entity_id=str(entity.id),
                organisation_id=context.organisation_id,
            )

    @staticmethod
    def create_version(
        session: Session,
        *,
        rubric: AssessmentRubric,
        standard_pack: StandardPackVersion,
        version_label: str,
        criteria: list[dict[str, Any]],
        performance_levels: list[dict[str, Any]],
        aggregation_policy: dict[str, Any],
        evidence_policy: dict[str, Any],
        ai_assessment_policy: dict[str, Any],
        human_review_policy: dict[str, Any],
        evidence_bindings: list[dict[str, Any]] | None = None,
        context: TenantContext | None = None,
    ) -> RubricVersion:
        validate_rubric(
            criteria=criteria,
            performance_levels=performance_levels,
            aggregation_policy=aggregation_policy,
            evidence_policy=evidence_policy,
            assessment_family=standard_pack.standard_pack.assessment_family,
            language=standard_pack.standard_pack.language,
            skill=QuestionBankSkill.WRITING,
            standard_pack=standard_pack,
            schema_version="1",
        )
        version = RubricVersion(
            rubric_id=rubric.id,
            version_label=version_label,
            schema_version="1",
            assessment_family=standard_pack.standard_pack.assessment_family,
            language=standard_pack.standard_pack.language,
            cefr_levels=standard_pack.supported_levels,
            skill=QuestionBankSkill.WRITING,
            standard_pack_version_id=standard_pack.id,
            criteria=criteria,
            performance_levels=performance_levels,
            aggregation_policy=aggregation_policy,
            evidence_policy=evidence_policy,
            ai_assessment_policy=ai_assessment_policy,
            human_review_policy=human_review_policy,
            status=RubricVersionStatus.DRAFT,
            checksum="",
            created_at=datetime.now(timezone.utc),
        )
        rubric.versions.append(version)
        session.flush()
        bindings = evidence_bindings or []
        if evidence_policy.get("required") and not bindings:
            raise RubricError("required rubric evidence binding is missing")
        for binding in bindings:
            if not binding.get("knowledge_document_version_id"):
                raise RubricError("rubric evidence requires exact document version")
            document = session.get(
                KnowledgeDocumentVersion, uuid.UUID(str(binding["knowledge_document_version_id"]))
            )
            if document is None or not document.sha256:
                raise RubricError("rubric evidence document version is unavailable")
            if binding.get("source_checksum") not in {None, document.sha256}:
                raise RubricError("rubric evidence checksum does not match source version")
            version.evidence_bindings.append(
                RubricEvidenceBinding(
                    knowledge_document_version_id=uuid.UUID(
                        str(binding["knowledge_document_version_id"])
                    ),
                    knowledge_page_id=uuid.UUID(str(binding["knowledge_page_id"]))
                    if binding.get("knowledge_page_id")
                    else None,
                    knowledge_chunk_id=uuid.UUID(str(binding["knowledge_chunk_id"]))
                    if binding.get("knowledge_chunk_id")
                    else None,
                    reference=binding.get("reference", {}),
                    purpose=str(binding.get("purpose", "RUBRIC")),
                    source_checksum=document.sha256,
                    required=bool(binding.get("required", True)),
                )
            )
        session.flush()
        version.checksum = canonical_checksum(RubricService.checksum_payload(version))
        session.flush()
        RubricService._audit(session, context, "rubric.version.created", version)
        return version

    @staticmethod
    def assert_executable(version: RubricVersion) -> None:
        if version.status not in RubricService.EXECUTABLE_STATUSES:
            raise RubricError("rubric version is not executable")

    @staticmethod
    def diff(left: RubricVersion, right: RubricVersion) -> dict[str, bool]:
        return {
            "criterion_changed": left.criteria != right.criteria,
            "descriptor_changed": left.performance_levels != right.performance_levels,
            "aggregation_changed": left.aggregation_policy != right.aggregation_policy,
            "evidence_changed": left.evidence_policy != right.evidence_policy
            or [b.reference for b in left.evidence_bindings]
            != [b.reference for b in right.evidence_bindings],
            "assessment_policy_changed": left.ai_assessment_policy != right.ai_assessment_policy
            or left.human_review_policy != right.human_review_policy,
        }


class SubjectiveAssessmentService:
    @staticmethod
    def _audit(session: Session, context: TenantContext | None, event: str, entity: Any) -> None:
        if (
            context is not None
            and context.organisation_id
            and AuthorizationService.has_permission(context, "audit.read", context.organisation_id)
        ):
            AuditService.record(
                session,
                context,
                event_type=event,
                entity_type=type(entity).__name__,
                entity_id=str(entity.id),
                organisation_id=context.organisation_id,
            )

    @staticmethod
    def _criterion_map(version: RubricVersion) -> dict[str, dict[str, Any]]:
        return {str(item["code"]): item for item in version.criteria}

    @staticmethod
    def create(
        session: Session,
        context: TenantContext,
        *,
        attempt_id: uuid.UUID,
        binding_id: uuid.UUID,
        rubric_version_id: uuid.UUID,
    ) -> SubjectiveAssessmentVersion:
        AuthorizationService.require_permission(
            context, "subjective.assess", context.organisation_id
        )
        attempt = session.get(CandidateExamAttempt, attempt_id)
        rubric = session.get(RubricVersion, rubric_version_id)
        if attempt is None or attempt.organisation_id != context.organisation_id:
            raise RubricError("attempt is not available in this organisation")
        if attempt.status is not CandidateAttemptStatus.SUBMITTED:
            raise RubricError("subjective assessment requires a submitted attempt")
        if rubric is None:
            raise RubricError("rubric version not found")
        RubricService.assert_executable(rubric)
        item_data = next(
            (
                item
                for module in attempt.runtime_plan.get("modules", [])
                for section in module.get("sections", [])
                for task in section.get("tasks", [])
                for item in task.get("items", [])
                if str(item.get("binding_id")) == str(binding_id)
            ),
            None,
        )
        if item_data is None:
            raise RubricError("writing item binding is not in the submitted attempt")
        item = session.get(QuestionBankItemVersion, item_data.get("item_version_id"))
        if item is None or item.response_mode not in {
            PackResponseMode.TEXT,
            PackResponseMode.LONG_TEXT,
        }:
            raise RubricError("only text writing responses are supported")
        if (
            item.assessment_family is not AssessmentFamily.LANGUAGE
            or item.skill is not QuestionBankSkill.WRITING
        ):
            raise RubricError("writing rubric is incompatible with item taxonomy")
        if (
            item.language != rubric.language
            or item.assessment_family is not rubric.assessment_family
        ):
            raise RubricError("writing item and rubric taxonomy are incompatible")
        response = session.scalar(
            select(CandidateResponse).where(
                CandidateResponse.attempt_id == attempt.id,
                CandidateResponse.exam_version_item_binding_id == binding_id,
            )
        )
        if response is None or not response.revisions:
            raise RubricError("submitted writing response revision is missing")
        revision = max(response.revisions, key=lambda row: row.revision_number)
        existing = session.scalar(
            select(SubjectiveAssessment).where(
                SubjectiveAssessment.attempt_id == attempt.id,
                SubjectiveAssessment.exam_version_item_binding_id == binding_id,
            )
        )
        if existing is None:
            existing = SubjectiveAssessment(
                attempt_id=attempt.id,
                exam_version_id=attempt.exam_version_id,
                exam_version_item_binding_id=binding_id,
                question_bank_item_version_id=item.id,
                candidate_user_id=attempt.candidate_user_id,
                organisation_id=attempt.organisation_id,
                created_at=datetime.now(timezone.utc),
            )
            session.add(existing)
            session.flush()
            SubjectiveAssessmentService._audit(
                session, context, "subjective_assessment.created", existing
            )
        latest = max(existing.versions, key=lambda row: row.version_number, default=None)
        version = SubjectiveAssessmentVersion(
            subjective_assessment_id=existing.id,
            version_number=(latest.version_number + 1 if latest else 1),
            attempt_id=attempt.id,
            exam_version_id=attempt.exam_version_id,
            exam_version_item_binding_id=binding_id,
            question_bank_item_version_id=item.id,
            candidate_response_id=response.id,
            candidate_response_revision_id=revision.id,
            rubric_version_id=rubric.id,
            standard_pack_version_id=rubric.standard_pack_version_id,
            status=SubjectiveAssessmentStatus.CREATED,
            response_checksum=revision.payload_checksum,
            rubric_checksum=rubric.checksum,
            confidence_summary={},
            disagreement_summary={},
            provenance={"candidate_text_untrusted": True},
            checksum=canonical_checksum(
                {
                    "response_checksum": revision.payload_checksum,
                    "rubric_checksum": rubric.checksum,
                    "revision_id": str(revision.id),
                }
            ),
            created_at=datetime.now(timezone.utc),
            supersedes_version_id=latest.id if latest else None,
        )
        existing.versions.append(version)
        session.flush()
        return version

    @staticmethod
    def _aggregate(version: RubricVersion, values: dict[str, Decimal]) -> Decimal:
        method = version.aggregation_policy.get("method")
        if method == "SUM":
            return sum(values.values(), Decimal("0"))
        if method == "WEIGHTED_SUM":
            total = sum(
                (_decimal(item.get("weight", "0")) for item in version.criteria), Decimal("0")
            )
            scale = Decimal("100") if total == Decimal("100") else Decimal("1")
            return sum(
                (
                    values[str(item["code"])] * _decimal(item.get("weight", "0")) / scale
                    for item in version.criteria
                ),
                Decimal("0"),
            )
        mapping = version.aggregation_policy.get("mapping", {})
        key = ",".join(f"{code}:{values[code]}" for code in sorted(values))
        if key not in mapping:
            raise RubricError("declared rubric aggregation mapping is missing")
        return _decimal(mapping[key])

    @staticmethod
    def propose(
        session: Session,
        context: TenantContext,
        assessment_version_id: uuid.UUID,
        proposal: dict[str, Any],
        *,
        ai_run_id: uuid.UUID | None = None,
    ) -> SubjectiveAssessmentVersion:
        AuthorizationService.require_permission(
            context, "subjective.assess", context.organisation_id
        )
        version = session.get(SubjectiveAssessmentVersion, assessment_version_id)
        if version is None:
            raise RubricError("subjective assessment version not found")
        assessment = session.get(SubjectiveAssessment, version.subjective_assessment_id)
        if assessment is None or assessment.organisation_id != context.organisation_id:
            raise RubricError("subjective assessment is not available in this organisation")
        rubric = session.get(RubricVersion, version.rubric_version_id)
        if rubric is None:
            raise RubricError("rubric version not found")
        RubricService.assert_executable(rubric)
        entries = proposal.get("criteria")
        if not isinstance(entries, list):
            raise RubricError("AI proposal must contain structured criteria")
        expected = SubjectiveAssessmentService._criterion_map(rubric)
        if {str(item.get("criterion_code")) for item in entries} != set(expected):
            raise RubricError("AI proposal criterion coverage does not match rubric")
        score_values: dict[str, Decimal] = {}
        confidence: dict[str, str] = {}
        disagreement: dict[str, Any] = {}
        for entry in entries:
            code = str(entry["criterion_code"])
            criterion = expected[code]
            points = _decimal(entry.get("proposed_points"))
            maximum = _decimal(criterion["max_points"])
            allowed_levels = {str(level.get("code")) for level in rubric.performance_levels}
            if (
                points < 0
                or points > maximum
                or str(entry.get("proposed_level")) not in allowed_levels
            ):
                raise RubricError("AI proposal exceeds rubric bounds")
            if str(entry.get("confidence")) not in {item.value for item in SubjectiveConfidence}:
                raise RubricError("AI proposal confidence is invalid")
            evidence = entry.get("evidence")
            if not isinstance(evidence, list):
                raise RubricError("AI proposal evidence must be structured")
            score_values[code] = points
            confidence[code] = str(entry["confidence"])
            disagreement[code] = entry.get("disagreement", {})
        version.proposed_score = SubjectiveAssessmentService._aggregate(rubric, score_values)
        version.confidence_summary = confidence
        version.disagreement_summary = disagreement
        version.provenance = {
            "candidate_text_untrusted": True,
            "ai_run_id": str(ai_run_id) if ai_run_id else None,
            "tool_allowlist": [
                "KNOWLEDGE_RETRIEVAL",
                "STANDARD_PACK_READ",
                "RULES_EVALUATE",
                "CITATION_RESOLVE",
                "RUBRIC_READ",
            ],
        }
        version.ai_run_id = ai_run_id
        if ai_run_id is not None:
            ai_run = session.get(AIRun, ai_run_id)
            if (
                ai_run is None
                or ai_run.workflow_version_id is None
                or ai_run.agent_version_id is None
            ):
                raise RubricError("AI run lacks exact workflow and agent provenance")
            agent = session.get(AgentVersion, ai_run.agent_version_id)
            if agent is None:
                raise RubricError("AI run agent provenance is missing")
            version.workflow_version_id = ai_run.workflow_version_id
            version.agent_version_id = agent.id
            version.prompt_version_id = agent.prompt_version_id
            version.model_profile_id = agent.model_profile_id
        version.status = SubjectiveAssessmentStatus.AI_PROPOSED
        for entry in entries:
            session.add(
                SubjectiveCriterionAssessment(
                    subjective_assessment_version_id=version.id,
                    criterion_code=str(entry["criterion_code"]),
                    proposed_level=str(entry["proposed_level"]),
                    proposed_points=_decimal(entry["proposed_points"]),
                    confidence=SubjectiveConfidence(str(entry["confidence"])),
                    evidence=entry["evidence"],
                    rationale=str(entry.get("rationale", ""))[:2000],
                    uncertainties=entry.get("uncertainties", []),
                    disagreement=entry.get("disagreement", {}),
                    status=SubjectiveCriterionStatus.PROPOSED,
                )
            )
        session.flush()
        version.checksum = canonical_checksum(
            {
                "response_checksum": version.response_checksum,
                "rubric_checksum": version.rubric_checksum,
                "proposed_score": str(version.proposed_score),
                "criteria": proposal["criteria"],
                "provenance": version.provenance,
                "workflow_version_id": str(version.workflow_version_id)
                if version.workflow_version_id
                else None,
                "agent_version_id": str(version.agent_version_id)
                if version.agent_version_id
                else None,
                "prompt_version_id": str(version.prompt_version_id)
                if version.prompt_version_id
                else None,
                "model_profile_id": str(version.model_profile_id)
                if version.model_profile_id
                else None,
            }
        )
        review = HumanReviewRequest(
            review_type="SUBJECTIVE_ASSESSMENT",
            entity_type="SUBJECTIVE_ASSESSMENT_VERSION",
            entity_id=version.id,
            ai_run_id=ai_run_id,
            status=HumanReviewStatus.PENDING,
            requested_by=context.user_id,
            requested_at=datetime.now(timezone.utc),
            reviewed_checksum=version.checksum,
        )
        session.add(review)
        session.flush()
        SubjectiveAssessmentService._audit(
            session, context, "subjective_assessment.ai_proposed", version
        )
        SubjectiveAssessmentService._audit(
            session, context, "subjective_assessment.review_requested", version
        )
        return version

    @staticmethod
    def approve(
        session: Session,
        context: TenantContext,
        version_id: uuid.UUID,
        corrections: dict[str, dict[str, Any]] | None = None,
    ) -> SubjectiveAssessmentVersion:
        AuthorizationService.require_permission(
            context, "subjective.review", context.organisation_id
        )
        version = session.get(SubjectiveAssessmentVersion, version_id)
        if version is None or version.status is not SubjectiveAssessmentStatus.AI_PROPOSED:
            raise RubricError("subjective assessment is not awaiting review")
        assessment = session.get(SubjectiveAssessment, version.subjective_assessment_id)
        if assessment is None or assessment.organisation_id != context.organisation_id:
            raise RubricError("subjective assessment is not available in this organisation")
        rubric = session.get(RubricVersion, version.rubric_version_id)
        review = session.scalar(
            select(HumanReviewRequest).where(
                HumanReviewRequest.entity_type == "SUBJECTIVE_ASSESSMENT_VERSION",
                HumanReviewRequest.entity_id == version.id,
                HumanReviewRequest.status == HumanReviewStatus.PENDING,
            )
        )
        if rubric is None or review is None or review.reviewed_checksum != version.checksum:
            raise RubricError("stale subjective assessment review")
        values: dict[str, Decimal] = {}
        for criterion in version.criterion_assessments:
            correction = (corrections or {}).get(criterion.criterion_code, {})
            points = _decimal(correction.get("approved_points", criterion.proposed_points))
            maximum = _decimal(
                next(
                    item["max_points"]
                    for item in rubric.criteria
                    if item["code"] == criterion.criterion_code
                )
            )
            if points < 0 or points > maximum:
                raise RubricError("human correction exceeds rubric bounds")
            criterion.approved_points = points
            criterion.approved_level = correction.get("approved_level", criterion.proposed_level)
            criterion.status = SubjectiveCriterionStatus.APPROVED
            values[criterion.criterion_code] = points
        version.approved_score = SubjectiveAssessmentService._aggregate(rubric, values)
        version.status = SubjectiveAssessmentStatus.APPROVED
        version.checksum = canonical_checksum(
            {
                "base": version.checksum,
                "approved_score": str(version.approved_score),
                "corrections": corrections or {},
            }
        )
        review.status = HumanReviewStatus.APPROVED
        review.decided_by = context.user_id
        review.decided_at = datetime.now(timezone.utc)
        review.decision = "APPROVED"
        session.flush()
        SubjectiveAssessmentService._audit(
            session, context, "subjective_assessment.approved", version
        )
        return version

    @staticmethod
    def decide(
        session: Session,
        context: TenantContext,
        version_id: uuid.UUID,
        decision: HumanReviewStatus,
        reason: str | None = None,
    ) -> SubjectiveAssessmentVersion:
        AuthorizationService.require_permission(
            context, "subjective.review", context.organisation_id
        )
        version = session.get(SubjectiveAssessmentVersion, version_id)
        if version is None or version.status is not SubjectiveAssessmentStatus.AI_PROPOSED:
            raise RubricError("subjective assessment is not awaiting review")
        assessment = session.get(SubjectiveAssessment, version.subjective_assessment_id)
        review = session.scalar(
            select(HumanReviewRequest).where(
                HumanReviewRequest.entity_type == "SUBJECTIVE_ASSESSMENT_VERSION",
                HumanReviewRequest.entity_id == version.id,
                HumanReviewRequest.status == HumanReviewStatus.PENDING,
            )
        )
        if assessment is None or assessment.organisation_id != context.organisation_id:
            raise RubricError("subjective assessment is not available in this organisation")
        if review is None or review.reviewed_checksum != version.checksum:
            raise RubricError("stale subjective assessment review")
        if decision not in {HumanReviewStatus.REJECTED, HumanReviewStatus.CHANGES_REQUESTED}:
            raise RubricError("unsupported subjective review decision")
        version.status = (
            SubjectiveAssessmentStatus.REJECTED
            if decision is HumanReviewStatus.REJECTED
            else SubjectiveAssessmentStatus.CHANGES_REQUESTED
        )
        review.status = decision
        review.decided_by = context.user_id
        review.decided_at = datetime.now(timezone.utc)
        review.decision = decision.value
        review.reason = reason
        session.flush()
        event = (
            "subjective_assessment.rejected"
            if decision is HumanReviewStatus.REJECTED
            else "subjective_assessment.changes_requested"
        )
        SubjectiveAssessmentService._audit(session, context, event, version)
        return version
