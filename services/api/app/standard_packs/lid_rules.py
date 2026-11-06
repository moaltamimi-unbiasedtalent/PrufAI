"""Source-pinned Rules-as-Code previews for the two LiD numerical benchmarks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.authorization import TenantContext
from app.lid_policy import LID_DRAFT_PACK_CODE, LID_DRAFT_SCHEMA, LID_RULE_PREFIX, LID_STRUCTURE
from app.models import (
    KnowledgeDocumentVersion,
    KnowledgeSource,
    RuleDefinition,
    RuleEvidenceBinding,
    RuleVersion,
    StandardPackVersion,
)
from app.models.enums import (
    RuleCategory,
    RuleEvaluationStatus,
    RuleEvidenceType,
    RuleSeverity,
    RuleStatus,
    StandardPackStatus,
)
from app.rules.engine import EvaluationContext, RulesEngine
from app.rules.service import RulesService
from app.standard_packs.lid_sources import (
    EINBTESTV,
    EINBTESTV_AGREEMENTS,
    EINBTESTV_FORMAT,
    INTTESTV,
    INTTESTV_RESULTS,
    LID_ENTRIES,
    lid_berlin_date,
    lid_source_citation,
    lid_source_snapshots,
    lid_utc,
    resolve_lid_sources,
)

LID_ORIENTATION_PURPOSE = "ORIENTATION_COURSE_CONTEXT"
LID_NATURALIZATION_PURPOSE = "NATURALIZATION_KNOWLEDGE_CONTEXT"
LID_ORIENTATION_RULE = f"{LID_RULE_PREFIX}{LID_ORIENTATION_PURPOSE}"
LID_NATURALIZATION_RULE = f"{LID_RULE_PREFIX}{LID_NATURALIZATION_PURPOSE}"

_TOTAL = LID_STRUCTURE["total_items"]
_BOUNDARIES = (0, 14, 15, 16, 17, 32, 33)
_DRAFT_MESSAGE = "The numerical practice threshold was not met."
_REVIEW_REQUIRED = "REVIEW_REQUIRED"


@dataclass(frozen=True)
class _Rule:
    purpose: str
    code: str
    name: str
    threshold: int
    legal_key: str
    references: tuple[tuple[str, str, str], ...]

    @property
    def effective_from(self) -> datetime | None:
        return lid_utc(LID_ENTRIES[self.legal_key].effective_from)


_RULES = (
    _Rule(
        LID_ORIENTATION_PURPOSE,
        LID_ORIENTATION_RULE,
        "LiD orientation-course numerical benchmark",
        15,
        INTTESTV,
        ((INTTESTV, "IntTestV § 10(2)", INTTESTV_RESULTS),),
    ),
    _Rule(
        LID_NATURALIZATION_PURPOSE,
        LID_NATURALIZATION_RULE,
        "Einbürgerungstest numerical benchmark for LiD practice comparison",
        17,
        EINBTESTV,
        (
            (EINBTESTV, "EinbTestV § 1(3)", EINBTESTV_FORMAT),
            (INTTESTV, "IntTestV § 10(3), conditional LiD certificate", INTTESTV_RESULTS),
            (EINBTESTV, "EinbTestV § 2(1), Land–BAMF agreements", EINBTESTV_AGREEMENTS),
        ),
    ),
)


def _score_facts(correct: int, total: int) -> dict[str, Any]:
    return {"score": {"raw_correct_count": correct, "total_item_count": total}}


def _expression(threshold: int) -> dict[str, Any]:
    return {
        "op": "ALL",
        "args": [
            {
                "op": "EQ",
                "left": {"fact": "score.total_item_count"},
                "right": {"value": _TOTAL},
            },
            {
                "op": "GTE",
                "left": {"fact": "score.raw_correct_count"},
                "right": {"value": threshold},
            },
        ],
    }


def _test_cases(threshold: int) -> list[dict[str, Any]]:
    return [
        {
            "facts": _score_facts(correct, _TOTAL),
            "expected_status": "PASS" if correct >= threshold else "FAIL",
        }
        for correct in _BOUNDARIES
    ] + [{"facts": _score_facts(32, 32), "expected_status": "FAIL"}]


def _failure_code(spec: _Rule) -> str:
    return f"{spec.code}_BELOW_THRESHOLD"


def _projection(
    rule: RuleVersion, spec: _Rule, snapshots: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    effective = lid_utc(rule.effective_from)
    return {
        "id": str(rule.id),
        "code": spec.code,
        "name": spec.name,
        "purpose": spec.purpose,
        "threshold": spec.threshold,
        "total_item_count": _TOTAL,
        "version_label": rule.version_label,
        "checksum": rule.checksum,
        "status": rule.status.value,
        "review_status": _REVIEW_REQUIRED,
        "legal_effective_from": effective.isoformat() if effective else None,
        "legal_effective_date": lid_berlin_date(effective),
        "citations": [
            lid_source_citation(snapshots, key, locator, url)
            for key, locator, url in spec.references
        ],
    }


def create_lid_rules(
    session: Session,
    context: TenantContext,
    pack: StandardPackVersion,
    documents: dict[str, KnowledgeDocumentVersion],
) -> list[dict[str, Any]]:
    """Create two drafts within the caller's sealed pack transaction."""
    now = datetime.now(timezone.utc)
    snapshots = lid_source_snapshots(documents)
    projections = []
    for spec in _RULES:
        definition = session.scalar(
            select(RuleDefinition).where(RuleDefinition.code == spec.code).with_for_update()
        )
        if definition is None:
            definition = RuleDefinition(
                code=spec.code,
                name=spec.name,
                description="Numerical practice comparison. Independent expert review is required.",
                category=RuleCategory.RESULT,
                status=RuleStatus.DRAFT,
                created_at=now,
                updated_at=now,
            )
            session.add(definition)
        if (
            definition.status is not RuleStatus.DRAFT
            or definition.category is not RuleCategory.RESULT
            or definition.name != spec.name
        ):
            raise ValueError("LID_DRAFT_RULE_IDENTITY_CONFLICT")
        rule = RuleVersion(
            definition=definition,
            version_label=pack.version_label,
            expression_schema_version="1",
            expression=_expression(spec.threshold),
            severity=RuleSeverity.BLOCKING,
            failure_code=_failure_code(spec),
            failure_message_template=_DRAFT_MESSAGE,
            effective_from=spec.effective_from,
            status=RuleStatus.DRAFT,
            created_at=now,
            created_by_user_id=context.user_id,
            internal_policy=False,
            test_cases=_test_cases(spec.threshold),
        )
        session.add(rule)
        for key in sorted({key for key, _, _ in spec.references}):
            document = documents[key]
            rule.evidence_bindings.append(
                RuleEvidenceBinding(
                    knowledge_document_version_id=document.id,
                    evidence_type=RuleEvidenceType.LEGAL,
                    notes=document.metadata_checksum,
                    created_at=now,
                )
            )
        session.flush()
        rule.checksum = RulesService.checksum(rule)
        if not RulesService.run_tests(rule):
            raise ValueError("LID_DRAFT_RULE_CASES_FAILED")
        projections.append(_projection(rule, spec, snapshots))
    return projections


def _pack_source_pins(session: Session, pack: StandardPackVersion) -> dict[str, tuple[UUID, str]]:
    source_codes = {entry.source_code: key for key, entry in LID_ENTRIES.items()}
    sources = {
        source_id: source_code
        for source_id, source_code in session.execute(
            select(KnowledgeSource.id, KnowledgeSource.source_code).where(
                KnowledgeSource.source_code.in_(source_codes)
            )
        )
    }
    pins = {}
    if len(pack.source_bindings) != len(LID_ENTRIES):
        raise ValueError("LID_DRAFT_RULE_SOURCE_BINDING_INVALID")
    for binding in pack.source_bindings:
        identifier = binding.document_version_id
        checksum = binding.source_metadata_checksum
        source_id = binding.knowledge_source_id
        key = source_codes.get(sources.get(source_id, "")) if source_id else None
        if not key or not identifier or not checksum or binding.document_checksum is not None:
            raise ValueError("LID_DRAFT_RULE_SOURCE_BINDING_INVALID")
        pins[key] = (identifier, checksum)
    return pins


def _binding_pins(rule: RuleVersion) -> set[tuple[UUID | None, str | None]]:
    bindings = rule.evidence_bindings
    if any(
        binding.evidence_type is not RuleEvidenceType.LEGAL
        or binding.document_checksum is not None
        or binding.knowledge_page_id is not None
        or binding.knowledge_chunk_id is not None
        for binding in bindings
    ):
        return set()
    return {(binding.knowledge_document_version_id, binding.notes) for binding in bindings}


def _current_rules(
    session: Session,
    pack: StandardPackVersion,
    rules: list[dict[str, Any]],
    documents: dict[str, KnowledgeDocumentVersion] | None = None,
) -> list[RuleVersion] | None:
    if (
        pack.status is not StandardPackStatus.DRAFT
        or pack.standard_pack.status is not StandardPackStatus.DRAFT
        or pack.standard_pack.code != LID_DRAFT_PACK_CODE
        or pack.configuration_schema_version != LID_DRAFT_SCHEMA
        or pack.rule_bindings
        or len(rules) != len(_RULES)
    ):
        return None
    try:
        if documents is None:
            documents = resolve_lid_sources(session, _pack_source_pins(session, pack))
        snapshots = lid_source_snapshots(documents)
        verified = []
        for item, spec in zip(rules, _RULES, strict=True):
            expected_sources = {
                (documents[key].id, documents[key].metadata_checksum)
                for key, _, _ in spec.references
            }
            rule = session.scalar(
                select(RuleVersion)
                .options(
                    joinedload(RuleVersion.definition),
                    selectinload(RuleVersion.evidence_bindings),
                    selectinload(RuleVersion.pack_bindings),
                )
                .where(RuleVersion.id == UUID(item["id"]))
                .execution_options(populate_existing=True)
            )
            if (
                rule is None
                or rule.status is not RuleStatus.DRAFT
                or rule.definition.status is not RuleStatus.DRAFT
                or rule.definition.code != spec.code
                or rule.definition.name != spec.name
                or rule.definition.category is not RuleCategory.RESULT
                or rule.version_label != pack.version_label
                or rule.expression_schema_version != "1"
                or rule.expression != _expression(spec.threshold)
                or rule.test_cases != _test_cases(spec.threshold)
                or lid_utc(rule.effective_from) != spec.effective_from
                or rule.effective_until is not None
                or rule.internal_policy
                or rule.reviewed_at is not None
                or rule.reviewed_by_user_id is not None
                or rule.approved_at is not None
                or rule.approved_by_user_id is not None
                or rule.published_at is not None
                or rule.published_by_user_id is not None
                or rule.supersedes_rule_version_id is not None
                or rule.pack_bindings
                or rule.severity is not RuleSeverity.BLOCKING
                or rule.failure_code != _failure_code(spec)
                or rule.failure_message_template != _DRAFT_MESSAGE
                or len(rule.evidence_bindings) != len(expected_sources)
                or _binding_pins(rule) != expected_sources
                or rule.checksum != RulesService.checksum(rule)
                or item != _projection(rule, spec, snapshots)
            ):
                return None
            verified.append(rule)
    except (AttributeError, KeyError, TypeError, ValueError):
        return None
    return verified


def lid_rules_current(
    session: Session,
    pack: StandardPackVersion,
    rules: list[dict[str, Any]],
    documents: dict[str, KnowledgeDocumentVersion] | None = None,
) -> bool:
    return _current_rules(session, pack, rules, documents) is not None


def evaluate_lid_preview(
    session: Session,
    pack: StandardPackVersion,
    rules: list[dict[str, Any]],
    *,
    raw_correct_count: int | None,
    total_item_count: int,
    as_of: datetime,
) -> dict[str, Any]:
    """Compare a practice score without making an official determination."""
    moment = lid_utc(as_of)
    usable_score = (
        type(raw_correct_count) is int
        and 0 <= raw_correct_count <= _TOTAL
        and type(total_item_count) is int
        and total_item_count == _TOTAL
    )
    current = _current_rules(session, pack, rules)
    context_rows = []
    for index, spec in enumerate(_RULES):
        pin = rules[index] if current is not None else {}
        numerical = "UNDETERMINED"
        reason = "LID_DRAFT_RULE_UNAVAILABLE"
        if current is not None and moment is not None:
            reason = "LID_DRAFT_RULE_NOT_APPLICABLE"
            starts = [
                instant
                for key, _, _ in spec.references
                if (instant := lid_utc(LID_ENTRIES[key].effective_from)) is not None
            ]
            if all(start <= moment for start in starts):
                reason = "LID_PRACTICE_SCORE_INCOMPLETE"
                if usable_score and raw_correct_count is not None:
                    rule = current[index]
                    result = RulesEngine().evaluate_rule(
                        rule.definition,
                        rule,
                        EvaluationContext(
                            _score_facts(raw_correct_count, total_item_count),
                            standard_pack_version_id=str(pack.id),
                            standard_pack_checksum=pack.configuration_checksum,
                        ),
                    )
                    reason = "LID_DRAFT_RULE_EVALUATION_ERROR"
                    if result.status is RuleEvaluationStatus.PASS:
                        numerical = "THRESHOLD_MET"
                        reason = "LID_PRACTICE_NUMERICAL_COMPARISON"
                    elif result.status is RuleEvaluationStatus.FAIL:
                        numerical = "BELOW_THRESHOLD"
                        reason = "LID_PRACTICE_NUMERICAL_COMPARISON"
        context_rows.append(
            {
                "purpose": spec.purpose,
                "threshold": spec.threshold,
                "total_item_count": total_item_count,
                "raw_correct_count": raw_correct_count,
                "numerical_outcome": numerical,
                "reason": reason,
                "rule_version_id": pin.get("id"),
                "rule_version_label": pin.get("version_label"),
                "rule_checksum": pin.get("checksum"),
                "legal_effective_from": pin.get("legal_effective_from"),
                "legal_effective_date": pin.get("legal_effective_date"),
                "citations": pin.get("citations", []),
                "rule_review_status": _REVIEW_REQUIRED,
                "eligibility_status": "UNVERIFIED_OFFICIAL_CONTEXT",
                "official_outcome": "UNDETERMINED",
                "administrative_recognition": "NOT_ESTABLISHED",
            }
        )
    return {
        "execution_class": "DRAFT_PREVIEW",
        "raw_correct_count": raw_correct_count,
        "total_item_count": total_item_count,
        "official_outcome": "UNDETERMINED",
        "administrative_recognition": "NOT_ESTABLISHED",
        "contexts": context_rows,
    }
