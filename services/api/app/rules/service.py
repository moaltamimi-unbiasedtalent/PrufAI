from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import AuditService
from app.authorization import TenantContext
from app.models import (
    KnowledgeChunk,
    KnowledgeDocumentPage,
    KnowledgeDocumentVersion,
    RuleDefinition,
    RuleEvidenceBinding,
    RuleVersion,
    StandardPackRuleBinding,
    StandardPackVersion,
)
from app.models.enums import (
    KnowledgeScope,
    RuleCategory,
    RuleEvidenceType,
    RuleSeverity,
    RuleStatus,
)
from app.rules.engine import (
    EvaluationContext,
    RuleEvaluationReport,
    RuleExpressionError,
    RulesEngine,
    validate_expression,
)
from app.rules.facts import pack_evaluation_context
from app.standard_packs.service import StandardPackService, normative_source_is_eligible


@dataclass(frozen=True)
class RuleValidationIssue:
    code: str
    severity: str
    path: str
    message: str


@dataclass(frozen=True)
class RuleValidationResult:
    status: str
    issues: tuple[RuleValidationIssue, ...]
    checksum: Optional[str]
    tests_passed: bool


_TRANSITIONS: dict[RuleStatus, set[RuleStatus]] = {
    RuleStatus.DRAFT: {
        RuleStatus.VALIDATION_REQUIRED,
        RuleStatus.HUMAN_REVIEW,
        RuleStatus.REJECTED,
    },
    RuleStatus.VALIDATION_REQUIRED: {RuleStatus.HUMAN_REVIEW, RuleStatus.REJECTED},
    RuleStatus.HUMAN_REVIEW: {RuleStatus.APPROVED, RuleStatus.REJECTED},
    RuleStatus.APPROVED: {RuleStatus.PUBLISHED, RuleStatus.REJECTED},
    RuleStatus.PUBLISHED: {RuleStatus.ACTIVE, RuleStatus.RETIRED},
    RuleStatus.ACTIVE: {RuleStatus.RETIRED, RuleStatus.SUPERSEDED},
    RuleStatus.RETIRED: set(),
    RuleStatus.REJECTED: set(),
    RuleStatus.SUPERSEDED: set(),
}


class RulesService:
    @staticmethod
    def create_definition(
        session: Session,
        *,
        code: str,
        name: str,
        category: RuleCategory,
        description: Optional[str] = None,
        context: Optional[TenantContext] = None,
    ) -> RuleDefinition:
        definition = RuleDefinition(
            code=code,
            name=name,
            category=category,
            description=description,
            status=RuleStatus.DRAFT,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(definition)
        session.flush()
        RulesService._audit(session, context, "rule.created", definition, {})
        return definition

    @staticmethod
    def create_version(
        session: Session,
        definition: RuleDefinition,
        *,
        version_label: str,
        expression: dict[str, Any],
        severity: RuleSeverity,
        failure_code: str,
        failure_message_template: str,
        effective_from: Optional[datetime] = None,
        effective_until: Optional[datetime] = None,
        created_by_user_id: Optional[uuid.UUID] = None,
        supersedes_rule_version_id: Optional[uuid.UUID] = None,
        internal_policy: bool = False,
        test_cases: Optional[list[dict[str, Any]]] = None,
        context: Optional[TenantContext] = None,
    ) -> RuleVersion:
        version = RuleVersion(
            rule_definition_id=definition.id,
            version_label=version_label,
            expression_schema_version="1",
            expression=expression,
            severity=severity,
            failure_code=failure_code,
            failure_message_template=failure_message_template,
            effective_from=effective_from,
            effective_until=effective_until,
            status=RuleStatus.DRAFT,
            created_at=datetime.now(timezone.utc),
            created_by_user_id=created_by_user_id,
            supersedes_rule_version_id=supersedes_rule_version_id,
            internal_policy=internal_policy,
            test_cases=test_cases or [],
        )
        session.add(version)
        session.flush()
        RulesService._audit(session, context, "rule.version.created", version, {})
        return version

    @staticmethod
    def bind_evidence(
        session: Session,
        version: RuleVersion,
        *,
        document_version_id: uuid.UUID,
        evidence_type: RuleEvidenceType,
        page_id: Optional[uuid.UUID] = None,
        chunk_id: Optional[uuid.UUID] = None,
        notes: Optional[str] = None,
        context: Optional[TenantContext] = None,
    ) -> RuleEvidenceBinding:
        RulesService.ensure_mutable(version)
        document = session.get(KnowledgeDocumentVersion, document_version_id)
        if document is None or document.source.scope is not KnowledgeScope.PLATFORM_SHARED:
            raise ValueError("document version not found")
        if evidence_type is RuleEvidenceType.NORMATIVE and not normative_source_is_eligible(
            document
        ):
            raise ValueError("source version is not eligible as normative evidence")
        if page_id is not None:
            page = session.get(KnowledgeDocumentPage, page_id)
            if page is None or page.document_version_id != document.id:
                raise ValueError("rule evidence page does not belong to the source version")
        if chunk_id is not None:
            chunk = session.get(KnowledgeChunk, chunk_id)
            if (
                chunk is None
                or chunk.document_version_id != document.id
                or (page_id is not None and chunk.document_page_id != page_id)
            ):
                raise ValueError("rule evidence chunk does not match the exact source reference")
        binding = RuleEvidenceBinding(
            rule_version_id=version.id,
            knowledge_document_version_id=document_version_id,
            knowledge_page_id=page_id,
            knowledge_chunk_id=chunk_id,
            evidence_type=evidence_type,
            document_checksum=document.sha256,
            notes=notes,
            created_at=datetime.now(timezone.utc),
        )
        session.add(binding)
        session.flush()
        RulesService._audit(
            session, context, "rule.evidence_bound", version, {"binding_id": str(binding.id)}
        )
        return binding

    @staticmethod
    def bind_to_pack(
        session: Session,
        pack_version: StandardPackVersion,
        rule_version: RuleVersion,
        *,
        required: bool = True,
        scope: Optional[str] = None,
        sequence: int = 0,
        context: Optional[TenantContext] = None,
    ) -> StandardPackRuleBinding:
        StandardPackService.ensure_mutable(pack_version)
        if rule_version.status not in {
            RuleStatus.APPROVED,
            RuleStatus.PUBLISHED,
            RuleStatus.ACTIVE,
        }:
            raise ValueError("pack bindings require an approved or published rule version")
        binding = StandardPackRuleBinding(
            standard_pack_version_id=pack_version.id,
            rule_version_id=rule_version.id,
            required=required,
            scope=scope,
            sequence=sequence,
            created_at=datetime.now(timezone.utc),
        )
        session.add(binding)
        session.flush()
        RulesService._audit(
            session,
            context,
            "rule.bound_to_standard_pack",
            rule_version,
            {"pack_version_id": str(pack_version.id)},
        )
        return binding

    @staticmethod
    def validate(session: Session, version: RuleVersion) -> RuleValidationResult:
        issues: list[RuleValidationIssue] = []
        try:
            validate_expression(version.expression)
        except RuleExpressionError as exc:
            issues.append(
                RuleValidationIssue("RULE.INVALID_EXPRESSION", "FAIL", "expression", str(exc))
            )
        if not version.internal_policy and not any(
            item.evidence_type is RuleEvidenceType.NORMATIVE for item in version.evidence_bindings
        ):
            issues.append(
                RuleValidationIssue(
                    "RULE.MISSING_NORMATIVE_EVIDENCE",
                    "FAIL",
                    "evidence_bindings",
                    "external rules require normative evidence",
                )
            )
        for binding in version.evidence_bindings:
            if binding.evidence_type is not RuleEvidenceType.NORMATIVE:
                continue
            document = session.get(KnowledgeDocumentVersion, binding.knowledge_document_version_id)
            if document is None or not normative_source_is_eligible(document):
                issues.append(
                    RuleValidationIssue(
                        "RULE.NORMATIVE_SOURCE_NOT_ELIGIBLE",
                        "FAIL",
                        "evidence_bindings",
                        "The exact source version is not eligible as normative evidence.",
                    )
                )
        if not version.test_cases:
            issues.append(
                RuleValidationIssue(
                    "RULE.MISSING_TEST_CASES",
                    "FAIL",
                    "test_cases",
                    "at least one deterministic rule test is required",
                )
            )
        test_results = RulesService.run_tests(version)
        if not test_results:
            issues.append(
                RuleValidationIssue(
                    "RULE.TEST_CASE_FAILED",
                    "FAIL",
                    "test_cases",
                    "one or more rule test cases failed",
                )
            )
        checksum = RulesService.checksum(version)
        failed = any(item.severity == "FAIL" for item in issues)
        return RuleValidationResult(
            "FAIL" if failed else "PASS", tuple(issues), checksum, test_results
        )

    @staticmethod
    def run_tests(version: RuleVersion) -> bool:
        if not version.test_cases:
            return False
        definition = version.definition
        engine = RulesEngine()
        for case in version.test_cases:
            context = EvaluationContext(case.get("facts", {}))
            report = engine.evaluate_rule(definition, version, context)
            if report.status.value != case.get("expected_status"):
                return False
        return True

    @staticmethod
    def transition(
        session: Session,
        version: RuleVersion,
        target: RuleStatus,
        *,
        actor_id: Optional[uuid.UUID] = None,
        context: Optional[TenantContext] = None,
    ) -> RuleValidationResult:
        if target not in _TRANSITIONS.get(version.status, set()):
            raise ValueError(f"invalid rule transition: {version.status.value} -> {target.value}")
        result = RulesService.validate(session, version)
        if (
            target in {RuleStatus.HUMAN_REVIEW, RuleStatus.APPROVED, RuleStatus.PUBLISHED}
            and result.status == "FAIL"
        ):
            raise ValueError("rule validation failed; transition is blocked")
        now = datetime.now(timezone.utc)
        version.status = target
        if target is RuleStatus.HUMAN_REVIEW:
            version.reviewed_at, version.reviewed_by_user_id = now, actor_id
        elif target is RuleStatus.APPROVED:
            version.approved_at, version.approved_by_user_id = now, actor_id
        elif target is RuleStatus.PUBLISHED:
            version.published_at, version.published_by_user_id = now, actor_id
        version.checksum = result.checksum
        session.flush()
        event = {
            RuleStatus.HUMAN_REVIEW: "rule.review_requested",
            RuleStatus.APPROVED: "rule.approved",
            RuleStatus.PUBLISHED: "rule.published",
            RuleStatus.ACTIVE: "rule.activated",
            RuleStatus.RETIRED: "rule.retired",
            RuleStatus.REJECTED: "rule.rejected",
            RuleStatus.SUPERSEDED: "rule.version.superseded",
            RuleStatus.VALIDATION_REQUIRED: "rule.validation.completed",
        }[target]
        RulesService._audit(session, context, event, version, {"checksum": version.checksum})
        return result

    @staticmethod
    def ensure_mutable(version: RuleVersion) -> None:
        if version.status in {
            RuleStatus.PUBLISHED,
            RuleStatus.ACTIVE,
            RuleStatus.RETIRED,
            RuleStatus.SUPERSEDED,
        }:
            raise ValueError("published rule versions are immutable; create a new version")

    @staticmethod
    def evaluate_pack(session: Session, pack_version: StandardPackVersion) -> RuleEvaluationReport:
        rules = []
        for binding in sorted(pack_version.rule_bindings, key=lambda item: item.sequence):
            definition = binding.rule_version.definition
            rules.append((definition, binding.rule_version))
        context = pack_evaluation_context(pack_version)
        return RulesEngine().evaluate(rules, context)

    @staticmethod
    def dry_run(version: RuleVersion, facts: dict[str, Any]) -> RuleEvaluationReport:
        return RulesEngine().evaluate([(version.definition, version)], EvaluationContext(facts))

    @staticmethod
    def canonical(version: RuleVersion) -> dict[str, Any]:
        return {
            "rule_definition_id": str(version.rule_definition_id),
            "version_label": version.version_label,
            "expression_schema_version": version.expression_schema_version,
            "expression": version.expression,
            "severity": version.severity.value,
            "failure_code": version.failure_code,
            "failure_message_template": version.failure_message_template,
            "effective_from": version.effective_from.isoformat()
            if version.effective_from
            else None,
            "effective_until": version.effective_until.isoformat()
            if version.effective_until
            else None,
            "internal_policy": version.internal_policy,
            "test_cases": version.test_cases,
            "evidence": [
                {
                    "document_version_id": str(item.knowledge_document_version_id),
                    "page_id": str(item.knowledge_page_id) if item.knowledge_page_id else None,
                    "chunk_id": str(item.knowledge_chunk_id) if item.knowledge_chunk_id else None,
                    "evidence_type": item.evidence_type.value,
                    "document_checksum": item.document_checksum,
                }
                for item in sorted(version.evidence_bindings, key=lambda value: str(value.id))
            ],
        }

    @staticmethod
    def checksum(version: RuleVersion) -> str:
        canonical = json.dumps(
            RulesService.canonical(version),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def export(version: RuleVersion) -> dict[str, Any]:
        result = RulesService.canonical(version)
        result["rule_code"] = version.definition.code
        result["category"] = version.definition.category.value
        result["checksum"] = version.checksum or RulesService.checksum(version)
        result["engine"] = {"name": "prufai_rules_engine", "version": "1.0"}
        return result

    @staticmethod
    def import_payload(payload: dict[str, Any]) -> dict[str, Any]:
        """Validate a portable rule payload without executing or persisting it."""
        if (
            not isinstance(payload, dict)
            or payload.get("engine", {}).get("name") != "prufai_rules_engine"
        ):
            raise ValueError("unsupported rule export")
        forbidden = {"code", "python", "javascript", "eval", "exec", "callback", "provider_logic"}
        if forbidden.intersection(payload):
            raise ValueError("executable or provider-specific rule fields are prohibited")
        expression = payload.get("expression")
        validate_expression(expression)
        required = {"rule_code", "category", "version_label", "checksum"}
        if not required.issubset(payload):
            raise ValueError("rule export is missing required identity fields")
        return {
            "rule_code": payload["rule_code"],
            "category": payload["category"],
            "version_label": payload["version_label"],
            "expression": expression,
            "checksum": payload["checksum"],
        }

    @staticmethod
    def diff(left: RuleVersion, right: RuleVersion) -> dict[str, bool]:
        first, second = RulesService.canonical(left), RulesService.canonical(right)
        return {
            "expression_changed": first["expression"] != second["expression"],
            "severity_changed": first["severity"] != second["severity"],
            "failure_configuration_changed": (
                first["failure_code"],
                first["failure_message_template"],
            )
            != (second["failure_code"], second["failure_message_template"]),
            "evidence_changed": first["evidence"] != second["evidence"],
            "effective_dates_changed": (first["effective_from"], first["effective_until"])
            != (second["effective_from"], second["effective_until"]),
        }

    @staticmethod
    def resolve(
        session: Session, definition_id: uuid.UUID, *, as_of: Optional[datetime] = None
    ) -> Optional[RuleVersion]:
        query = select(RuleVersion).where(RuleVersion.rule_definition_id == definition_id)
        if as_of is None:
            query = query.where(RuleVersion.status == RuleStatus.ACTIVE)
        else:
            query = (
                query.where(RuleVersion.effective_from <= as_of)
                .where(
                    (RuleVersion.effective_until.is_(None)) | (RuleVersion.effective_until > as_of)
                )
                .where(
                    RuleVersion.status.in_(
                        [
                            RuleStatus.ACTIVE,
                            RuleStatus.PUBLISHED,
                            RuleStatus.RETIRED,
                            RuleStatus.SUPERSEDED,
                        ]
                    )
                )
            )
        return session.scalars(query.order_by(RuleVersion.effective_from.desc()).limit(1)).first()

    @staticmethod
    def _audit(
        session: Session,
        context: Optional[TenantContext],
        event: str,
        entity: Any,
        metadata: dict[str, Any],
    ) -> None:
        if context is not None:
            AuditService.record(
                session,
                context,
                event_type=event,
                entity_type="rule",
                entity_id=str(entity.id),
                entity_version=getattr(entity, "version_label", None),
                metadata=metadata,
            )
