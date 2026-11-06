"""Persisted draft rule and rubric review records for the DTZ practice blueprint."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.authorization import TenantContext
from app.dtz_policy import DTZ_RUBRIC_SCHEMA, DTZ_RULE_PREFIX, is_dtz_pack
from app.models import (
    AssessmentRubric,
    KnowledgeDocumentVersion,
    QuestionBankItemVersion,
    RubricEvidenceBinding,
    RubricVersion,
    RuleDefinition,
    RuleEvidenceBinding,
    RuleVersion,
    StandardPackTaskSpecification,
    StandardPackVersion,
)
from app.models.enums import (
    AssessmentFamily,
    KnowledgeDocumentType,
    KnowledgeScope,
    KnowledgeVersionStatus,
    PackResponseMode,
    QuestionBankSkill,
    RubricVersionStatus,
    RuleCategory,
    RuleEvaluationStatus,
    RuleEvidenceType,
    RuleSeverity,
    RuleStatus,
)
from app.rules.engine import EvaluationContext, RulesEngine
from app.rules.service import RulesService
from app.scoring.service import objective_award
from app.services.knowledge import source_legal_decision_valid
from app.writing_assessment.service import RubricService, canonical_checksum

DTZ_OVERALL_B1 = f"{DTZ_RULE_PREFIX}OVERALL_B1"
DTZ_OBJECTIVE = f"{DTZ_RULE_PREFIX}OBJECTIVE_COUNTS"
DTZ_EFFECTIVE_FROM = datetime(2026, 11, 1, tzinfo=ZoneInfo("Europe/Berlin"))
_PROVIDER_SOURCES = ("gast-dtz-overview", "gast-dtz-faq", "gast-dtz-specimen-2024")
_B1_SOURCES = ("inttestv-2026-306", "gast-dtz-overview", "gast-dtz-faq")
_DIGEST = r"^[0-9a-f]{64}$"
Sha256 = Annotated[str, Field(pattern=_DIGEST)]
_DOMAINS = ("listening_reading", "writing", "speaking")


class DtzDomainLevel(str, Enum):
    BELOW_A2 = "BELOW_A2"
    A2 = "A2"
    B1 = "B1"
    UNDETERMINED = "UNDETERMINED"


class DtzPreviewDomains(BaseModel):
    model_config = ConfigDict(extra="forbid")

    listening_reading: DtzDomainLevel | None = None
    writing: DtzDomainLevel | None = None
    speaking: DtzDomainLevel | None = None


class DtzPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_pack_checksum: Sha256
    rule_version_id: uuid.UUID | None = None
    expected_rule_checksum: Sha256 | None = None
    as_of: date
    domains: DtzPreviewDomains = Field(default_factory=DtzPreviewDomains)
    objective_answer: Literal["A", "B", "C"] | None = None


def _compare(operator: str, fact: str, value: Any) -> dict[str, Any]:
    return {"op": operator, "left": {"fact": fact}, "right": {"value": value}}


_B1_EXPRESSION = {
    "op": "ALL",
    "args": [
        _compare("EQ", "result.speaking", "B1"),
        _compare("IN", "result.listening_reading", ["A2", "B1"]),
        _compare("IN", "result.writing", ["A2", "B1"]),
        {
            "op": "ANY",
            "args": [
                _compare("EQ", "result.listening_reading", "B1"),
                _compare("EQ", "result.writing", "B1"),
            ],
        },
    ],
}
_OBJECTIVE_EXPRESSION = {
    "op": "ALL",
    "args": [
        _compare("EQ", "score.listening_items", 20),
        _compare("EQ", "score.reading_items", 25),
        _compare("EQ", "score.points_per_correct_item", 1),
        _compare("EQ", "score.combined_maximum", 45),
        _compare("EQ", "score.response_mode", "SELECT_ONE"),
    ],
}
_OBJECTIVE_FACTS = {
    "listening_items": 20,
    "reading_items": 25,
    "points_per_correct_item": 1,
    "combined_maximum": 45,
    "response_mode": "SELECT_ONE",
}
_OBJECTIVE_FIXTURE = {
    "label": "ORIGINAL SYNTHETIC PRACTICE CONTENT",
    "prompt": "Im Nachbarschaftsgarten hängt ein Schild: Heute ist das Tor ab 17 Uhr offen. "
    "Wann kann man hinein?",
    "options": [
        {"option_id": "A", "label": "Ab 15 Uhr"},
        {"option_id": "B", "label": "Ab 17 Uhr"},
        {"option_id": "C", "label": "Ab 19 Uhr"},
    ],
    "correct_option_ids": ["B"],
    "scoring_metadata": {"unanswered_policy": {"type": "ZERO", "points": 0}},
}


def _b1_case(listening_reading: str, writing: str, speaking: str, expected: str) -> dict[str, Any]:
    return {
        "facts": {
            "result": {
                "listening_reading": listening_reading,
                "writing": writing,
                "speaking": speaking,
            }
        },
        "expected_status": expected,
    }


def _source_pin(document: KnowledgeDocumentVersion) -> dict[str, Any]:
    if (
        document.content_mode != "METADATA_ONLY"
        or not document.metadata_checksum
        or document.sha256 is not None
        or document.status is not KnowledgeVersionStatus.DRAFT
        or document.source.scope is not KnowledgeScope.PLATFORM_SHARED
        or document.source.status != "ACTIVE"
        or document.source.authority.status != "ACTIVE"
        or not source_legal_decision_valid(document)
    ):
        raise ValueError("DTZ_ARTIFACT_REQUIRES_METADATA_SOURCE")
    return {
        "source_version_id": str(document.id),
        "source_url": document.source_url,
        "metadata_checksum": document.metadata_checksum,
        "reference_kind": "BIBLIOGRAPHIC_METADATA",
    }


def _rule_projection(session: Session, rule: RuleVersion) -> dict[str, Any]:
    pins = []
    for evidence in sorted(
        rule.evidence_bindings, key=lambda binding: str(binding.knowledge_document_version_id)
    ):
        document = session.get(KnowledgeDocumentVersion, evidence.knowledge_document_version_id)
        if document is None:
            raise ValueError("DTZ_ARTIFACT_SOURCE_UNAVAILABLE")
        pin = _source_pin(document)
        if (
            evidence.document_checksum is not None
            or evidence.knowledge_page_id is not None
            or evidence.knowledge_chunk_id is not None
            or evidence.notes != pin["metadata_checksum"]
            or evidence.evidence_type not in {RuleEvidenceType.SUPPORTING, RuleEvidenceType.LEGAL}
        ):
            raise ValueError("DTZ_ARTIFACT_SOURCE_PIN_MISMATCH")
        pins.append(pin)
    return {
        "id": str(rule.id),
        "code": rule.definition.code,
        "name": rule.definition.name,
        "version_label": rule.version_label,
        "checksum": rule.checksum,
        "status": rule.status.value,
        "review_status": "REVIEW_REQUIRED",
        "expression": rule.expression,
        "effective_from": rule.effective_from.astimezone(timezone.utc).isoformat()
        if rule.effective_from
        else None,
        "effective_until": rule.effective_until.astimezone(timezone.utc).isoformat()
        if rule.effective_until
        else None,
        "sources": pins,
    }


def _rubric_projection(session: Session, rubric: RubricVersion) -> dict[str, Any]:
    pins = []
    for evidence in sorted(
        rubric.evidence_bindings, key=lambda binding: str(binding.knowledge_document_version_id)
    ):
        document = session.get(KnowledgeDocumentVersion, evidence.knowledge_document_version_id)
        if document is None:
            raise ValueError("DTZ_ARTIFACT_SOURCE_UNAVAILABLE")
        pin = _source_pin(document)
        if (
            evidence.source_checksum is not None
            or evidence.knowledge_page_id is not None
            or evidence.knowledge_chunk_id is not None
            or evidence.reference != pin
        ):
            raise ValueError("DTZ_ARTIFACT_SOURCE_PIN_MISMATCH")
        pins.append(pin)
    return {
        "id": str(rubric.id),
        "skill": rubric.skill.value,
        "task_spec_id": str(rubric.task_spec_id),
        "version_label": rubric.version_label,
        "status": rubric.status.value,
        "checksum": rubric.checksum,
        "review_status": "REVIEW_REQUIRED",
        "assessment_mode": "HUMAN_ONLY",
        "evidence_policy": rubric.evidence_policy,
        "sources": pins,
    }


def _create_rule(
    session: Session,
    context: TenantContext,
    pack: StandardPackVersion,
    documents: list[KnowledgeDocumentVersion],
    *,
    code: str,
    name: str,
    category: RuleCategory,
    expression: dict[str, Any],
    test_cases: list[dict[str, Any]],
    effective_from: datetime | None = None,
) -> RuleVersion:
    now = datetime.now(timezone.utc)
    definition = session.scalar(
        select(RuleDefinition).where(RuleDefinition.code == code).with_for_update()
    )
    if definition is None:
        definition = RuleDefinition(
            code=code,
            name=name,
            description="DTZ-informed practice preview. Human review is pending.",
            category=category,
            status=RuleStatus.DRAFT,
            created_at=now,
            updated_at=now,
        )
        session.add(definition)
    if definition.status is not RuleStatus.DRAFT or definition.category is not category:
        raise ValueError("DTZ_DRAFT_RULE_IDENTITY_CONFLICT")
    rule = RuleVersion(
        definition=definition,
        version_label=pack.version_label,
        expression_schema_version="1",
        expression=expression,
        severity=RuleSeverity.BLOCKING,
        failure_code=f"{code}_CONDITION_NOT_MET",
        failure_message_template="The recorded draft condition is unmet.",
        effective_from=effective_from,
        status=RuleStatus.DRAFT,
        created_at=now,
        created_by_user_id=context.user_id,
        internal_policy=False,
        test_cases=test_cases,
    )
    session.add(rule)
    for document in sorted(documents, key=lambda item: str(item.id)):
        pin = _source_pin(document)
        rule.evidence_bindings.append(
            RuleEvidenceBinding(
                knowledge_document_version_id=document.id,
                evidence_type=RuleEvidenceType.LEGAL
                if document.source.document_type is KnowledgeDocumentType.REGULATION
                else RuleEvidenceType.SUPPORTING,
                notes=pin["metadata_checksum"],
                created_at=now,
            )
        )
    session.flush()
    rule.checksum = RulesService.checksum(rule)
    if not RulesService.run_tests(rule):
        raise ValueError("DTZ_DRAFT_RULE_CASES_FAILED")
    return rule


def _create_rubric(
    session: Session,
    version: StandardPackVersion,
    task: StandardPackTaskSpecification,
    documents: list[KnowledgeDocumentVersion],
    skill: QuestionBankSkill,
) -> RubricVersion:
    code = f"{DTZ_RULE_PREFIX}{skill.value}_RUBRIC"
    identity = session.scalar(
        select(AssessmentRubric)
        .where(AssessmentRubric.code == code, AssessmentRubric.organisation_id.is_(None))
        .with_for_update()
    )
    if identity is None:
        identity = AssessmentRubric(
            code=code,
            name=f"DTZ-informed {skill.value.title()} review",
            organisation_id=None,
            status=RubricVersionStatus.VALIDATION_REQUIRED,
        )
        session.add(identity)
    if identity.status is not RubricVersionStatus.VALIDATION_REQUIRED:
        raise ValueError("DTZ_DRAFT_RUBRIC_IDENTITY_CONFLICT")
    speaking = skill is QuestionBankSkill.SPEAKING
    rubric = RubricVersion(
        rubric=identity,
        version_label=version.version_label,
        schema_version=DTZ_RUBRIC_SCHEMA,
        assessment_family=AssessmentFamily.LANGUAGE,
        language="de",
        cefr_levels=["A2", "B1"],
        skill=skill,
        standard_pack_version_id=version.id,
        task_spec_id=task.id,
        criteria=[],
        performance_levels=[],
        aggregation_policy={},
        evidence_policy=(
            {
                "mode": "AUDIO",
                "required": True,
                "minimum_references": 1,
                "verified_duration_required": True,
                "criterion_time_spans_required": True,
                "transcript_only_permitted": False,
            }
            if speaking
            else {"mode": "TEXT", "response_revision_checksum_required": True}
        ),
        ai_assessment_policy={"mode": "HUMAN_ONLY"},
        human_review_policy={"independent_reviewer_required": True},
        status=RubricVersionStatus.VALIDATION_REQUIRED,
        checksum="",
        created_at=datetime.now(timezone.utc),
    )
    session.add(rubric)
    for document in sorted(documents, key=lambda item: str(item.id)):
        rubric.evidence_bindings.append(
            RubricEvidenceBinding(
                knowledge_document_version_id=document.id,
                reference=_source_pin(document),
                purpose="DTZ_RUBRIC_TERMINOLOGY_REVIEW",
                required=True,
            )
        )
    session.flush()
    rubric.checksum = canonical_checksum(RubricService.checksum_payload(rubric))
    return rubric


def create_dtz_review_artifacts(
    session: Session,
    context: TenantContext,
    version: StandardPackVersion,
    documents: dict[str, KnowledgeDocumentVersion],
    writing_task: StandardPackTaskSpecification,
    speaking_task: StandardPackTaskSpecification,
) -> dict[str, Any]:
    objective = _create_rule(
        session,
        context,
        version,
        [documents[key] for key in _PROVIDER_SOURCES],
        code=DTZ_OBJECTIVE,
        name="Objective item counts and combined maximum",
        category=RuleCategory.SCORING,
        expression=_OBJECTIVE_EXPRESSION,
        test_cases=[
            {
                "facts": {"score": _OBJECTIVE_FACTS},
                "expected_status": "PASS",
                "original_synthetic_fixture": _OBJECTIVE_FIXTURE,
            },
            {
                "facts": {"score": {**_OBJECTIVE_FACTS, "listening_items": 19}},
                "expected_status": "FAIL",
            },
        ],
    )
    overall = _create_rule(
        session,
        context,
        version,
        [documents[key] for key in _B1_SOURCES],
        code=DTZ_OVERALL_B1,
        name="Overall B1 rule candidate",
        category=RuleCategory.RESULT,
        expression=_B1_EXPRESSION,
        effective_from=DTZ_EFFECTIVE_FROM.astimezone(timezone.utc),
        test_cases=[
            _b1_case("B1", "B1", "B1", "PASS"),
            _b1_case("A2", "B1", "B1", "PASS"),
            _b1_case("B1", "A2", "B1", "PASS"),
            _b1_case("B1", "BELOW_A2", "B1", "FAIL"),
            _b1_case("B1", "B1", "A2", "FAIL"),
            _b1_case("A2", "A2", "B1", "FAIL"),
        ],
    )
    rubric_sources = [documents[key] for key in ("gast-dtz-overview", "gast-dtz-faq")]
    writing = _create_rubric(
        session, version, writing_task, rubric_sources, QuestionBankSkill.WRITING
    )
    speaking = _create_rubric(
        session, version, speaking_task, rubric_sources, QuestionBankSkill.SPEAKING
    )
    session.flush()
    return {
        "rules": [_rule_projection(session, rule) for rule in (objective, overall)],
        "rubrics": [_rubric_projection(session, rubric) for rubric in (writing, speaking)],
    }


def dtz_review_artifacts_current(
    session: Session, version: StandardPackVersion, artifacts: dict[str, Any]
) -> bool:
    """Recheck exact draft, evidence and rubric pins before a preview can run."""
    if not is_dtz_pack(version) or version.rule_bindings:
        return False
    try:
        rules = artifacts["rules"]
        rubrics = artifacts["rubrics"]
        if len(rules) != 2 or len(rubrics) != 2:
            return False
        if {item["code"] for item in rules} != {DTZ_OBJECTIVE, DTZ_OVERALL_B1} or {
            item["skill"] for item in rubrics
        } != {"WRITING", "SPEAKING"}:
            return False
        pack_sources = {
            (str(binding.document_version_id), binding.source_metadata_checksum)
            for binding in version.source_bindings
        }
        if any(
            (source["source_version_id"], source["metadata_checksum"]) not in pack_sources
            for item in [*rules, *rubrics]
            for source in item["sources"]
        ):
            return False
        for item in rules:
            rule = session.get(RuleVersion, uuid.UUID(item["id"]))
            if (
                rule is None
                or rule.status is not RuleStatus.DRAFT
                or rule.definition.status is not RuleStatus.DRAFT
                or rule.version_label != version.version_label
                or rule.internal_policy
                or rule.approved_at is not None
                or rule.published_at is not None
                or rule.pack_bindings
                or rule.checksum != RulesService.checksum(rule)
                or item != _rule_projection(session, rule)
            ):
                return False
        for item in rubrics:
            rubric = session.get(RubricVersion, uuid.UUID(item["id"]))
            if (
                rubric is None
                or rubric.standard_pack_version_id != version.id
                or rubric.rubric.organisation_id is not None
                or rubric.status is not RubricVersionStatus.VALIDATION_REQUIRED
                or rubric.schema_version != DTZ_RUBRIC_SCHEMA
                or rubric.version_label != version.version_label
                or rubric.approved_at is not None
                or rubric.published_at is not None
                or rubric.criteria
                or rubric.performance_levels
                or rubric.aggregation_policy
                or rubric.ai_assessment_policy != {"mode": "HUMAN_ONLY"}
                or rubric.checksum != canonical_checksum(RubricService.checksum_payload(rubric))
                or item != _rubric_projection(session, rubric)
            ):
                return False
            task = session.get(StandardPackTaskSpecification, rubric.task_spec_id)
            if (
                task is None
                or task.section.module.standard_pack_version_id != version.id
                or task.section.module.skill != rubric.skill.value
            ):
                return False
    except (AttributeError, KeyError, TypeError, ValueError):
        return False
    return True


def _objective_preview(rule: RuleVersion, answer: str | None) -> dict[str, Any]:
    case = rule.test_cases[0]
    facts = case["facts"]
    result = RulesEngine().evaluate_rule(rule.definition, rule, EvaluationContext(facts=facts))
    if result.status is not RuleEvaluationStatus.PASS:
        raise ValueError("DTZ_OBJECTIVE_DRAFT_RULE_UNAVAILABLE")
    fixture = case["original_synthetic_fixture"]
    maximum = facts["score"]["points_per_correct_item"]
    item = QuestionBankItemVersion(
        response_mode=PackResponseMode.SELECT_ONE,
        content={
            "correct_option_ids": fixture["correct_option_ids"],
            "options": fixture["options"],
        },
        scoring_metadata=fixture["scoring_metadata"],
        max_points=Decimal(maximum),
    )
    status, award = objective_award(item, {"option_id": answer} if answer is not None else None)
    return {
        "label": fixture["label"],
        "prompt": fixture["prompt"],
        "options": fixture["options"],
        "status": status.value,
        "awarded_points": str(award) if award is not None else None,
        "maximum_points": str(maximum),
        "combined_maximum_points": facts["score"]["combined_maximum"],
        "rule_version_id": str(rule.id),
        "rule_checksum": rule.checksum,
    }


def _preview_result(
    version: StandardPackVersion, rule: dict[str, Any] | None, reason: str
) -> dict[str, Any]:
    return {
        "execution_class": "DRAFT_PREVIEW",
        "outcome": "B1" if reason == "B1_DRAFT_RULE_MATCH" else "UNDETERMINED",
        "reason": reason,
        "standard_pack_version_id": str(version.id),
        "standard_pack_checksum": version.configuration_checksum,
        "rule": rule,
    }


def evaluate_dtz_preview(
    session: Session,
    version: StandardPackVersion,
    artifacts: dict[str, Any],
    request: DtzPreviewRequest,
) -> dict[str, Any]:
    if (
        request.expected_pack_checksum != version.configuration_checksum
        or request.rule_version_id is None
        or request.expected_rule_checksum is None
        or not dtz_review_artifacts_current(session, version, artifacts)
    ):
        return _preview_result(version, None, "DRAFT_RULE_UNAVAILABLE")
    pin = next(
        (row for row in artifacts["rules"] if row["id"] == str(request.rule_version_id)),
        None,
    )
    if pin is None or pin["checksum"] != request.expected_rule_checksum:
        return _preview_result(version, None, "DRAFT_RULE_UNAVAILABLE")
    rule = session.get(RuleVersion, request.rule_version_id)
    if rule is None:
        return _preview_result(version, None, "DRAFT_RULE_UNAVAILABLE")
    berlin = ZoneInfo("Europe/Berlin")
    if (rule.effective_from and request.as_of < rule.effective_from.astimezone(berlin).date()) or (
        rule.effective_until and request.as_of >= rule.effective_until.astimezone(berlin).date()
    ):
        return _preview_result(version, pin, "DRAFT_RULE_NOT_APPLICABLE")
    objective_pin = next(row for row in artifacts["rules"] if row["code"] == DTZ_OBJECTIVE)
    objective = session.get(RuleVersion, uuid.UUID(objective_pin["id"]))
    if objective is None:
        return _preview_result(version, None, "DRAFT_RULE_UNAVAILABLE")
    try:
        technical_objective = _objective_preview(objective, request.objective_answer)
    except (AttributeError, KeyError, TypeError, ValueError):
        return _preview_result(version, None, "DRAFT_RULE_UNAVAILABLE")
    if pin["code"] == DTZ_OBJECTIVE:
        reason = "OBJECTIVE_TECHNICAL_PREVIEW_ONLY"
    else:
        domains = request.domains.model_dump(mode="json")
        if any(domains[domain] in {None, "UNDETERMINED"} for domain in _DOMAINS):
            reason = "MISSING_OR_UNDETERMINED_DOMAIN"
        else:
            result = RulesEngine().evaluate_rule(
                rule.definition,
                rule,
                EvaluationContext(
                    facts={"result": domains},
                    standard_pack_version_id=str(version.id),
                    standard_pack_checksum=version.configuration_checksum,
                ),
            )
            reason = {
                RuleEvaluationStatus.PASS: "B1_DRAFT_RULE_MATCH",
                RuleEvaluationStatus.FAIL: "B1_DRAFT_RULE_NOT_MET",
            }.get(result.status, "DRAFT_RULE_UNAVAILABLE")
    return {**_preview_result(version, pin, reason), "objective": technical_objective}
