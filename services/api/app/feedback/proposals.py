"""Governed pattern snapshots and proposals for future evaluation."""

from __future__ import annotations

import uuid
from datetime import timezone
from typing import Any, Literal

from pydantic import Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.authorization import AuthorizationError, TenantContext
from app.evaluation import service as evaluation
from app.evaluation.contracts import Contract
from app.evaluation.regression_comparison import regression_projection
from app.evaluation.regression_registry import verify_regression_suite
from app.feedback.common import (
    FeedbackConflict,
    Pin,
    latest,
    operation,
    record,
    require_platform,
    utcnow,
)
from app.feedback.intake import requires_reference
from app.feedback.targets import TargetPin, resolve_target
from app.models import EvaluationDataset, EvaluationDatasetVersion, EvaluationRun
from app.models.evaluation import REGRESSION_ADAPTER
from app.models.feedback import (
    FeedbackOperation,
    FeedbackPattern,
    GovernedFeedback,
    ImprovementProposal,
)
from app.writing_assessment.service import canonical_checksum

GROUP_LIMIT = 100
SCOPE_KEYS = ("assessment_family", "skill", "language", "cefr_level")
METHOD_KEYS = ("evaluation_method", "evaluation_method_version")
ARTIFACT_KEYS = {
    "STANDARD_PACK_VERSION": ("standard_pack_version_id", "standard_pack_checksum"),
    "RUBRIC_VERSION": ("rubric_version_id", "rubric_checksum"),
    "QUESTION_BANK_ITEM_VERSION": ("question_bank_item_version_id", "item_checksum"),
}
RUN_ARTIFACT_KEYS: dict[str, tuple[str, str | None]] = {
    **ARTIFACT_KEYS,
    "PROMPT_VERSION": ("prompt_version_id", None),
    "WORKFLOW_VERSION": ("workflow_version_id", None),
    "MODEL_PROFILE": ("model_profile_id", "model_version"),
    "TRANSCRIPTION_PROFILE": ("model_profile_id", "profile_version"),
}
APPROVE = "APPROVE_FOR_EVALUATION"
DECISIONS = {APPROVE: "APPROVED_FOR_EVALUATION", "REJECT": "REJECTED", "CLOSE": "CLOSED"}


class PatternInput(Contract):
    operation_id: uuid.UUID
    anchor: Pin


class ProposalInput(Contract):
    operation_id: uuid.UUID
    pattern: Pin
    target: TargetPin
    change_category: Literal[
        "VALIDATION_GUARD",
        "CONTENT_CORRECTION",
        "RUBRIC_CLARIFICATION",
        "PROMPT_REVISION",
        "MODEL_CONFIGURATION",
        "WORKFLOW_REVISION",
    ]
    hypothesis_code: Literal[
        "PREVENT_RECORDED_FAILURE",
        "IMPROVE_REFERENCE_AGREEMENT",
        "IMPROVE_EVIDENCE_GROUNDING",
        "IMPROVE_RECOVERY_RELIABILITY",
    ]
    suite_version: Pin | None = None
    baseline_run: Pin | None = None
    candidate_run: Pin | None = None
    predecessor: Pin | None = None


class ProposalDecisionInput(Contract):
    operation_id: uuid.UUID
    expected_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    action: Literal["APPROVE_FOR_EVALUATION", "REJECT", "CLOSE"]
    reason_code: Literal[
        "EVALUATION_PLAN_SUFFICIENT",
        "INSUFFICIENT_EVIDENCE",
        "OUT_OF_SCOPE",
        "DUPLICATE",
        "SUPERSEDED",
        "WITHDRAWN",
    ]


def _pin(
    row: FeedbackPattern | ImprovementProposal | FeedbackOperation | EvaluationDatasetVersion,
) -> dict[str, str]:
    return {"id": str(row.id), "checksum": row.checksum}


def _global(row: FeedbackPattern | ImprovementProposal | GovernedFeedback | None) -> None:
    if row is None or row.organisation_id is not None:
        raise AuthorizationError("FEEDBACK_PLATFORM_EVIDENCE_REQUIRED")


def _group_key(row: GovernedFeedback, receipt: FeedbackOperation) -> str:
    return canonical_checksum(
        {
            "scope_key": row.scope_key,
            "category": receipt.state["category"],
            "evidence_class": row.evidence_class,
            "privacy_class": row.privacy_class,
        }
    )


def _pattern_checksum(group_key: str, version: int, body: dict[str, Any]) -> str:
    return canonical_checksum({"group_key": group_key, "version_number": version, "body": body})


def _verify_pattern(row: FeedbackPattern, expected_checksum: str | None = None) -> None:
    _global(row)
    if (
        expected_checksum is not None and row.checksum != expected_checksum
    ) or row.checksum != _pattern_checksum(row.group_key, row.version_number, row.body):
        raise FeedbackConflict("FEEDBACK_PATTERN_PIN_MISMATCH")


def pattern_summary(
    session: Session, context: TenantContext, row: FeedbackPattern
) -> dict[str, Any]:
    require_platform(session, context, evaluation.READ)
    _verify_pattern(row)
    return {
        **_pin(row),
        "organisation_id": None,
        "group_key": row.group_key,
        "version_number": row.version_number,
        "status": "IDENTIFIED",
        **{
            key: row.body[key]
            for key in (
                "category",
                "scope",
                "evidence_class",
                "privacy_class",
                "artifact_pins",
                "members",
                "member_count",
                "distinct_source_count",
                "requires_reference",
                "required_checks",
            )
        },
    }


def _group_members(
    session: Session, anchor: GovernedFeedback, category: str
) -> list[tuple[GovernedFeedback, FeedbackOperation]]:
    newest = (
        select(
            FeedbackOperation.entity_id,
            FeedbackOperation.state,
            func.row_number()
            .over(
                partition_by=FeedbackOperation.entity_id,
                order_by=FeedbackOperation.revision.desc(),
            )
            .label("position"),
        )
        .where(FeedbackOperation.entity_kind == "FEEDBACK")
        .subquery()
    )
    rows = list(
        session.scalars(
            select(GovernedFeedback)
            .join(newest, newest.c.entity_id == GovernedFeedback.id)
            .where(
                GovernedFeedback.organisation_id.is_(None),
                GovernedFeedback.scope_key == anchor.scope_key,
                GovernedFeedback.evidence_class == anchor.evidence_class,
                GovernedFeedback.privacy_class == anchor.privacy_class,
                newest.c.position == 1,
                newest.c.state["status"].as_string() == "CONFIRMED",
                newest.c.state["category"].as_string() == category,
            )
            .order_by(GovernedFeedback.id)
            .limit(GROUP_LIMIT + 1)
            .with_for_update(of=GovernedFeedback)
        )
    )
    if len(rows) > GROUP_LIMIT:
        raise FeedbackConflict("FEEDBACK_PATTERN_MEMBER_LIMIT_EXCEEDED")
    members = [(row, latest(session, "FEEDBACK", row.id)) for row in rows]
    return [
        (row, receipt)
        for row, receipt in members
        if receipt.state["status"] == "CONFIRMED" and receipt.state["category"] == category
    ]


def create_pattern(
    session: Session, context: TenantContext, payload: PatternInput
) -> dict[str, Any]:
    trusted = require_platform(session, context, evaluation.READ, evaluation.RUN, evaluation.MANAGE)
    request = payload.model_dump(mode="json")
    action = "pattern_identified"
    repeated = operation(session, trusted, payload.operation_id, action, request)
    if repeated is not None:
        prior = session.get(FeedbackPattern, repeated.entity_id)
        if prior is None:
            raise FeedbackConflict("FEEDBACK_PATTERN_PIN_MISMATCH")
        return pattern_summary(session, trusted, prior)
    anchor = session.get(GovernedFeedback, payload.anchor.id)
    _global(anchor)
    assert anchor is not None
    receipt = latest(session, "FEEDBACK", anchor.id)
    if receipt.checksum != payload.anchor.checksum or receipt.state["status"] != "CONFIRMED":
        raise FeedbackConflict("FEEDBACK_CONFIRMED_ANCHOR_REQUIRED")
    group_key = _group_key(anchor, receipt)
    # Separate the advisory lock namespace from caller operation IDs.
    session.execute(
        text("SELECT pg_advisory_xact_lock(:namespace, :key)"),
        {
            "namespace": 4130033,
            "key": int(group_key[:8], 16) - 2**31,
        },
    )
    members = _group_members(session, anchor, receipt.state["category"])
    if not any(row.id == anchor.id and item.checksum == receipt.checksum for row, item in members):
        raise FeedbackConflict("FEEDBACK_CONFIRMED_ANCHOR_REQUIRED")
    if any(row.source.get("split") == "HELD_OUT" for row, _ in members):
        raise AuthorizationError("FEEDBACK_HELD_OUT_UNAVAILABLE")
    body = {
        "category": receipt.state["category"],
        "scope": {key: anchor.source.get(key) for key in (*SCOPE_KEYS, *METHOD_KEYS, "method")},
        "evidence_class": anchor.evidence_class,
        "privacy_class": anchor.privacy_class,
        "artifact_pins": anchor.source["artifact_pins"],
        "members": [
            {
                "id": str(row.id),
                "checksum": item.checksum,
                "confirmation_operation_id": str(item.id),
            }
            for row, item in members
        ],
        "member_count": len(members),
        "distinct_source_count": len(
            {(row.source_kind, row.source["source_id"]) for row, _ in members}
        ),
        "required_checks": sorted(
            {row.source["failed_check"] for row, _ in members if row.source.get("failed_check")}
        ),
        "requires_reference": any(
            requires_reference(row, item.state["category"]) for row, item in members
        ),
    }
    previous = session.scalar(
        select(FeedbackPattern)
        .where(FeedbackPattern.group_key == group_key)
        .order_by(FeedbackPattern.version_number.desc())
        .limit(1)
    )
    if previous is not None and previous.body == body:
        row = previous
        prior_receipt = latest(session, "PATTERN", row.id)
        change = "MATCHED"
    else:
        change = "UPDATED" if previous is not None else "CREATED"
        number = previous.version_number + 1 if previous is not None else 1
        row = FeedbackPattern(
            group_key=group_key,
            version_number=number,
            body=body,
            checksum=_pattern_checksum(group_key, number, body),
            created_by=trusted.user_id,
            created_at=utcnow(),
        )
        session.add(row)
        session.flush()
        prior_receipt = None
    record(
        session,
        trusted,
        payload.operation_id,
        action,
        request,
        entity_kind="PATTERN",
        entity_id=row.id,
        state={
            "status": "IDENTIFIED",
            "change": change,
            "pattern_checksum": row.checksum,
            "member_count": len(members),
        },
        previous=prior_receipt,
        audit_action=f"pattern_{change.lower()}",
    )
    return pattern_summary(session, trusted, row)


def _current_members(session: Session, pattern: FeedbackPattern) -> None:
    members = {uuid.UUID(pin["id"]): pin for pin in pattern.body["members"]}
    rows = list(
        session.scalars(
            select(GovernedFeedback)
            .where(GovernedFeedback.id.in_(members))
            .order_by(GovernedFeedback.id)
            .with_for_update()
        )
    )
    if not members or len(rows) != len(members) or len(rows) != pattern.body["member_count"]:
        raise FeedbackConflict("FEEDBACK_PATTERN_MEMBERSHIP_STALE")
    for row in rows:
        _global(row)
        pin = members[row.id]
        receipt = latest(session, "FEEDBACK", row.id)
        if (
            receipt.state["status"] != "CONFIRMED"
            or receipt.checksum != pin["checksum"]
            or str(receipt.id) != pin["confirmation_operation_id"]
            or _group_key(row, receipt) != pattern.group_key
            or row.source.get("split") == "HELD_OUT"
        ):
            raise FeedbackConflict("FEEDBACK_PATTERN_MEMBERSHIP_STALE")


def _dataset(
    session: Session, identifier: uuid.UUID, scope: dict[str, Any]
) -> EvaluationDatasetVersion:
    row = evaluation._version(session, identifier)
    owner = session.get(EvaluationDataset, row.dataset_id)
    if owner is None or owner.organisation_id is not None:
        raise AuthorizationError("FEEDBACK_PLATFORM_EVIDENCE_REQUIRED")
    if row.status != "PUBLISHED" or any(getattr(row, key) != scope[key] for key in SCOPE_KEYS):
        raise FeedbackConflict("FEEDBACK_EVALUATION_SCOPE_MISMATCH")
    return row


def _run_link(
    session: Session,
    pin: Pin | None,
    scope: dict[str, Any],
    *,
    target: TargetPin | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if pin is None:
        return None, None
    row = session.get(EvaluationRun, pin.id)
    if (
        row is None
        or row.status != "COMPLETED"
        or row.completed_at is None
        or row.checksum != pin.checksum
    ):
        raise FeedbackConflict("FEEDBACK_EVALUATION_RUN_PIN_MISMATCH")
    version = _dataset(session, row.dataset_version_id, scope)
    if (
        row.configuration.get("dataset_split") == "HELD_OUT"
        or version.provenance.get("split") == "HELD_OUT"
    ):
        raise AuthorizationError("FEEDBACK_HELD_OUT_UNAVAILABLE")
    results = evaluation.verify_run(session, row)
    regression = (
        regression_projection(row, results)
        if row.configuration.get("execution_adapter") == REGRESSION_ADAPTER
        else None
    )
    if any(
        scope[key] is not None and row.configuration.get(key) != scope[key] for key in METHOD_KEYS
    ):
        raise FeedbackConflict("FEEDBACK_EVALUATION_METHOD_MISMATCH")
    keys = RUN_ARTIFACT_KEYS.get(target.type) if target is not None else None
    if keys is not None and target is not None:
        id_key, checksum_key = keys
        if (
            row.configuration.get(id_key) != str(target.id)
            or checksum_key is not None
            and row.configuration.get(checksum_key) != target.checksum
        ):
            raise FeedbackConflict("FEEDBACK_EVALUATION_BASELINE_TARGET_MISMATCH")
    return {
        **pin.model_dump(mode="json"),
        "dataset_version": _pin(version),
        "hard_gate": row.hard_gate,
    }, regression


def _evaluation_links(
    session: Session, payload: ProposalInput, pattern: FeedbackPattern
) -> dict[str, Any]:
    scope = pattern.body["scope"]
    suite_pin = None
    if payload.suite_version is not None:
        version = _dataset(session, payload.suite_version.id, scope)
        metadata = version.provenance
        if metadata.get("split") == "HELD_OUT":
            raise AuthorizationError("FEEDBACK_HELD_OUT_UNAVAILABLE")
        if version.checksum != payload.suite_version.checksum:
            raise FeedbackConflict("FEEDBACK_EVALUATION_SUITE_PIN_MISMATCH")
        verify_regression_suite(session, version)
        source = metadata["source_scope"]
        checks = {check for pin in metadata["case_pins"] for check in pin["required_checks"]}
        for pin in pattern.body["artifact_pins"]:
            keys = ARTIFACT_KEYS.get(pin["type"])
            if keys is not None and (source.get(keys[0]), source.get(keys[1])) != (
                pin["id"],
                pin["checksum"],
            ):
                raise FeedbackConflict("FEEDBACK_EVALUATION_SUITE_INCOMPATIBLE")
        if (
            any(scope[key] is not None and metadata.get(key) != scope[key] for key in METHOD_KEYS)
            or not set(pattern.body["required_checks"]) <= checks
        ):
            raise FeedbackConflict("FEEDBACK_EVALUATION_SUITE_INCOMPATIBLE")
        suite_pin = _pin(version)
    baseline, baseline_comparison = _run_link(
        session, payload.baseline_run, scope, target=payload.target
    )
    candidate, candidate_comparison = _run_link(session, payload.candidate_run, scope)
    if baseline_comparison is not None or (
        baseline is not None and candidate is not None and baseline["id"] == candidate["id"]
    ):
        raise FeedbackConflict("FEEDBACK_EVALUATION_RUN_PAIR_MISMATCH")
    quality_gate = "NOT_EVALUATED"
    if candidate_comparison is not None:
        if (
            suite_pin is None
            or baseline is None
            or candidate_comparison["suite_version"] != suite_pin
            or candidate_comparison["baseline_run"]
            != {key: baseline[key] for key in ("id", "checksum")}
        ):
            raise FeedbackConflict("FEEDBACK_EVALUATION_COMPARISON_PIN_MISMATCH")
        quality_gate = candidate_comparison["decision"]
    return {
        "suite_version": suite_pin,
        "baseline_run": baseline,
        "candidate_run": candidate,
        "quality_gate": quality_gate,
    }


def _proposal_checksum(proposal_id: uuid.UUID, version: int, body: dict[str, Any]) -> str:
    return canonical_checksum(
        {"proposal_id": str(proposal_id), "version_number": version, "body": body}
    )


def _verify_proposal(row: ImprovementProposal) -> None:
    _global(row)
    if (
        row.checksum != _proposal_checksum(row.proposal_id, row.version_number, row.body)
        or str(row.pattern_id) != row.body["pattern"]["id"]
    ):
        raise FeedbackConflict("FEEDBACK_PROPOSAL_PIN_MISMATCH")


def _is_latest(session: Session, row: ImprovementProposal) -> bool:
    return (
        session.scalar(
            select(ImprovementProposal.id)
            .where(ImprovementProposal.proposal_id == row.proposal_id)
            .order_by(ImprovementProposal.version_number.desc())
            .limit(1)
        )
        == row.id
    )


def proposal_summary(
    session: Session,
    context: TenantContext,
    row: ImprovementProposal,
    *,
    receipt: FeedbackOperation | None = None,
) -> dict[str, Any]:
    trusted = require_platform(session, context, evaluation.READ)
    _verify_proposal(row)
    state = receipt or latest(session, "PROPOSAL", row.id)
    if state.state["proposal_checksum"] != row.checksum:
        raise FeedbackConflict("FEEDBACK_PROPOSAL_PIN_MISMATCH")
    status = state.state["status"]
    allowed_actions: list[str] = []
    if (
        {evaluation.MANAGE, evaluation.REVIEW} <= trusted.platform_permissions
        and trusted.user_id != row.created_by
        and _is_latest(session, row)
    ):
        allowed_actions = (
            [APPROVE, "REJECT"]
            if status == "PENDING_REVIEW"
            else ["CLOSE"]
            if status in {"APPROVED_FOR_EVALUATION", "REJECTED"}
            else []
        )
    return {
        **_pin(row),
        "proposal_id": str(row.proposal_id),
        "version_number": row.version_number,
        "organisation_id": None,
        "created_by": str(row.created_by),
        "created_at": row.created_at.astimezone(timezone.utc).isoformat(),
        "status": status,
        "receipt": _pin(state),
        "allowed_actions": allowed_actions,
        "decision_reason_code": state.state.get("reason_code"),
        "reviewed_by": str(state.actor_id) if status != "PENDING_REVIEW" else None,
        **{
            key: row.body[key]
            for key in (
                "pattern",
                "target",
                "change_category",
                "hypothesis_code",
                "source_feedback",
                "evidence_class",
                "privacy_class",
                "evaluation_requirements",
                "links",
                "predecessor",
            )
        },
        "release_readiness": "NOT_READY",
        "production_activation": False,
    }


def _target(session: Session, pattern: FeedbackPattern, pin: TargetPin) -> dict[str, Any]:
    if pin.model_dump(mode="json") not in pattern.body["artifact_pins"]:
        raise FeedbackConflict("FEEDBACK_TARGET_NOT_PINNED_IN_PATTERN")
    return resolve_target(session, pin)


def create_proposal(
    session: Session, context: TenantContext, payload: ProposalInput
) -> dict[str, Any]:
    trusted = require_platform(session, context, evaluation.READ, evaluation.RUN, evaluation.MANAGE)
    request = payload.model_dump(mode="json")
    action = "proposal_created"
    repeated = operation(session, trusted, payload.operation_id, action, request)
    if repeated is not None:
        existing = session.get(ImprovementProposal, repeated.entity_id)
        if existing is None:
            raise FeedbackConflict("FEEDBACK_PROPOSAL_PIN_MISMATCH")
        return proposal_summary(session, trusted, existing, receipt=repeated)
    pattern = session.get(FeedbackPattern, payload.pattern.id)
    _global(pattern)
    assert pattern is not None
    _verify_pattern(pattern, payload.pattern.checksum)
    proposal_id, number = uuid.uuid4(), 1
    if payload.predecessor is not None:
        previous = session.get(ImprovementProposal, payload.predecessor.id, with_for_update=True)
        _global(previous)
        assert previous is not None
        _verify_proposal(previous)
        if previous.created_by != trusted.user_id:
            raise AuthorizationError("FEEDBACK_PROPOSAL_PREDECESSOR_AUTHOR_REQUIRED")
        previous_state = latest(session, "PROPOSAL", previous.id)
        prior_pattern = session.get(FeedbackPattern, previous.pattern_id)
        if (
            previous.checksum != payload.predecessor.checksum
            or not _is_latest(session, previous)
            or previous_state.state["proposal_checksum"] != previous.checksum
            or previous_state.state["status"] not in {"REJECTED", "CLOSED"}
            or any(
                previous.body["target"][key] != value
                for key, value in payload.target.model_dump(mode="json").items()
            )
            or prior_pattern is None
            or prior_pattern.group_key != pattern.group_key
        ):
            raise FeedbackConflict("FEEDBACK_PROPOSAL_PREDECESSOR_MISMATCH")
        proposal_id, number = previous.proposal_id, previous.version_number + 1
    _current_members(session, pattern)
    target = _target(session, pattern, payload.target)
    links = _evaluation_links(session, payload, pattern)
    needs_reference = (
        pattern.body["requires_reference"]
        or payload.hypothesis_code == "IMPROVE_REFERENCE_AGREEMENT"
    )
    body = {
        "pattern": _pin(pattern),
        "target": target,
        "change_category": payload.change_category,
        "hypothesis_code": payload.hypothesis_code,
        "source_feedback": pattern.body["members"],
        "evidence_class": pattern.body["evidence_class"],
        "privacy_class": pattern.body["privacy_class"],
        "predecessor": payload.predecessor.model_dump(mode="json") if payload.predecessor else None,
        "evaluation_requirements": {
            "status": "REGRESSION_SUITE_LINKED" if links["suite_version"] else "REQUIRE_EVALUATION",
            "required_checks": pattern.body["required_checks"],
            "reference_evidence": "REQUIRE_APPROVED_REFERENCE"
            if needs_reference
            else "NO_ADDITIONAL_REFERENCE_REQUIRED",
        },
        "links": links,
    }
    row = ImprovementProposal(
        proposal_id=proposal_id,
        version_number=number,
        pattern_id=pattern.id,
        body=body,
        checksum=_proposal_checksum(proposal_id, number, body),
        created_by=trusted.user_id,
        created_at=utcnow(),
    )
    session.add(row)
    session.flush()
    receipt = record(
        session,
        trusted,
        payload.operation_id,
        action,
        request,
        entity_kind="PROPOSAL",
        entity_id=row.id,
        state={"status": "PENDING_REVIEW", "proposal_checksum": row.checksum},
    )
    return proposal_summary(session, trusted, row, receipt=receipt)


def decide_proposal(
    session: Session,
    context: TenantContext,
    version_id: uuid.UUID,
    payload: ProposalDecisionInput,
) -> dict[str, Any]:
    trusted = require_platform(
        session, context, evaluation.READ, evaluation.MANAGE, evaluation.REVIEW
    )
    request = {"version_id": str(version_id), **payload.model_dump(mode="json")}
    action = {
        APPROVE: "proposal_approved_for_evaluation",
        "REJECT": "proposal_rejected",
        "CLOSE": "proposal_closed",
    }[payload.action]
    repeated = operation(session, trusted, payload.operation_id, action, request)
    if repeated is not None:
        existing = session.get(ImprovementProposal, repeated.entity_id)
        if existing is None:
            raise FeedbackConflict("FEEDBACK_PROPOSAL_PIN_MISMATCH")
        return proposal_summary(session, trusted, existing, receipt=repeated)
    row = session.get(ImprovementProposal, version_id, with_for_update=True)
    _global(row)
    assert row is not None
    _verify_proposal(row)
    if row.created_by == trusted.user_id:
        raise AuthorizationError("FEEDBACK_INDEPENDENT_PROPOSAL_REVIEW_REQUIRED")
    current = latest(session, "PROPOSAL", row.id)
    statuses = (
        {"APPROVED_FOR_EVALUATION", "REJECTED"} if payload.action == "CLOSE" else {"PENDING_REVIEW"}
    )
    if (
        not _is_latest(session, row)
        or current.checksum != payload.expected_checksum
        or current.state["status"] not in statuses
    ):
        raise FeedbackConflict("FEEDBACK_PROPOSAL_STATE_STALE")
    if (payload.action == APPROVE) != (payload.reason_code == "EVALUATION_PLAN_SUFFICIENT"):
        raise ValueError("FEEDBACK_PROPOSAL_DECISION_REASON_MISMATCH")
    pattern = session.get(FeedbackPattern, row.pattern_id)
    _global(pattern)
    assert pattern is not None
    _verify_pattern(pattern, row.body["pattern"]["checksum"])
    _current_members(session, pattern)
    _target(
        session,
        pattern,
        TargetPin.model_validate(
            {key: row.body["target"][key] for key in ("type", "id", "checksum")}
        ),
    )
    receipt = record(
        session,
        trusted,
        payload.operation_id,
        action,
        request,
        entity_kind="PROPOSAL",
        entity_id=row.id,
        state={
            "status": DECISIONS[payload.action],
            "reason_code": payload.reason_code,
            "proposal_checksum": row.checksum,
        },
        previous=current,
    )
    return proposal_summary(session, trusted, row, receipt=receipt)
