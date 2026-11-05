"""An approved feedback proposal can authorize one sealed, offline PromptVersion draft."""

from __future__ import annotations

import copy
import uuid
from datetime import timezone
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai_orchestration.service import AIOrchestrationService
from app.ai_orchestration.service import _checksum as prompt_checksum
from app.authorization import AuthorizationError, TenantContext
from app.evaluation import service as evaluation
from app.evaluation.contracts import Contract
from app.evaluation.regression_registry import METHOD_KEYS, verify_regression_suite
from app.evaluation.replay import SOURCE_KEYS
from app.feedback import common, proposals
from app.feedback.common import FeedbackConflict, Pin
from app.feedback.targets import CURRENT, TargetPin, resolve_target
from app.models import EvaluationDataset, EvaluationDatasetVersion, EvaluationRun, PromptVersion
from app.models.enums import AIArtifactStatus
from app.models.feedback import (
    FeedbackOperation,
    FeedbackPattern,
    GovernedChangeCandidate,
    GovernedFeedback,
    ImprovementProposal,
)
from app.writing_assessment.service import canonical_checksum

KIND = "CHANGE_CANDIDATE"
CREATED = "change_candidate_created"
CHANGE = "CLEAR_LEGACY_INSTRUCTIONS"
HYPOTHESIS = "PREVENT_RECORDED_FAILURE"
PURPOSE = "WRITING_ASSESSMENT"
ADAPTER = "OFFLINE_PROMPT_POLICY_EVAL_V1"
SCOPE = ("assessment_family", "skill", "language", "cefr_level")


class PromptPin(Pin):
    type: Literal["PROMPT_VERSION"]


class ChangeCandidateInput(Contract):
    operation_id: uuid.UUID
    proposal: Pin
    approval: Pin
    target: PromptPin
    suite_version: Pin
    baseline_run: Pin


def _require(session: Session, context: TenantContext, *permissions: str) -> TenantContext:
    if context.organisation_id is not None:
        raise AuthorizationError("CHANGE_CANDIDATE_PLATFORM_CONTEXT_REQUIRED")
    return common.require_platform(session, context, *permissions)


def _pin(row: Any) -> dict[str, str]:
    return {"id": str(row.id), "checksum": row.checksum}


def _prompt_payload(row: PromptVersion) -> dict[str, Any]:
    return {
        "system_template": row.system_template,
        "instruction_template": row.instruction_template,
        "configuration": row.configuration,
        "input_schema_version": row.input_schema_version,
        "output_schema_version": row.output_schema_version,
    }


def _read_prompt(session: Session, pin: Pin) -> PromptVersion:
    prompt = session.get(PromptVersion, pin.id)
    if (
        prompt is None
        or prompt.checksum != pin.checksum
        or prompt_checksum(_prompt_payload(prompt)) != pin.checksum
        or prompt.definition.purpose != PURPOSE
        or (prompt.input_schema_version, prompt.output_schema_version) != ("1", "1")
    ):
        raise FeedbackConflict("CHANGE_CANDIDATE_PROMPT_PIN_MISMATCH")
    return prompt


def _target_projection(prompt: PromptVersion) -> dict[str, str]:
    return {**_pin(prompt), "type": "PROMPT_VERSION", "version_label": prompt.version_label}


def _proposal_basis(
    session: Session, proposal_pin: Pin, approval_pin: Pin, *, current: bool, lock: bool = True
) -> tuple[ImprovementProposal, FeedbackPattern]:
    proposal = session.get(
        ImprovementProposal,
        proposal_pin.id,
        with_for_update=current and lock,
        populate_existing=current,
    )
    if proposal is None or proposal.checksum != proposal_pin.checksum:
        raise FeedbackConflict("CHANGE_CANDIDATE_PROPOSAL_PIN_MISMATCH")
    proposals._verify_proposal(proposal)
    approval = session.get(FeedbackOperation, approval_pin.id)
    if (
        approval is None
        or approval.checksum != approval_pin.checksum
        or approval.entity_kind != "PROPOSAL"
        or approval.entity_id != proposal.id
        or approval.organisation_id is not None
        or approval.actor_id == proposal.created_by
        or approval.action != "proposal_approved_for_evaluation"
        or approval.state.get("status") != "APPROVED_FOR_EVALUATION"
        or approval.state.get("proposal_checksum") != proposal.checksum
        or approval.state.get("reason_code") != "EVALUATION_PLAN_SUFFICIENT"
    ):
        raise FeedbackConflict("CHANGE_CANDIDATE_APPROVAL_PIN_MISMATCH")
    common.verify_receipt(approval)
    if current and (
        not proposals._is_latest(session, proposal)
        or common.latest(session, "PROPOSAL", proposal.id).id != approval.id
    ):
        raise FeedbackConflict("CHANGE_CANDIDATE_APPROVAL_STALE")
    pattern = session.get(FeedbackPattern, proposal.pattern_id)
    if pattern is None:
        raise FeedbackConflict("CHANGE_CANDIDATE_PATTERN_PIN_MISMATCH")
    proposals._verify_pattern(pattern, proposal.body["pattern"]["checksum"])
    if (
        proposal.body["pattern"] != _pin(pattern)
        or proposal.body["source_feedback"] != pattern.body["members"]
    ):
        raise FeedbackConflict("CHANGE_CANDIDATE_PATTERN_PIN_MISMATCH")
    if current:
        proposals._current_members(session, pattern, lock=lock)
    return proposal, pattern


def _platform_dataset(session: Session, identifier: uuid.UUID) -> EvaluationDatasetVersion:
    version = evaluation._version(session, identifier)
    owner = session.get(EvaluationDataset, version.dataset_id)
    if owner is None or owner.organisation_id is not None:
        raise AuthorizationError("CHANGE_CANDIDATE_PLATFORM_EVIDENCE_REQUIRED")
    if version.status != "PUBLISHED" or version.provenance.get("split") == "HELD_OUT":
        raise FeedbackConflict("CHANGE_CANDIDATE_PUBLISHED_EVIDENCE_REQUIRED")
    return version


def _baseline_scope(
    session: Session,
    payload: ChangeCandidateInput,
    proposal: ImprovementProposal,
    pattern: FeedbackPattern,
) -> dict[str, Any]:
    run = session.get(EvaluationRun, payload.baseline_run.id)
    if (
        run is None
        or run.checksum != payload.baseline_run.checksum
        or run.status != "COMPLETED"
        or run.completed_at is None
        or run.hard_gate != "FAIL"
        or run.execution_class != "TEST_ONLY"
        or run.evidence_class not in {"TECHNICAL_TEST", "SYNTHETIC_TEST", "SYNTHETIC_REGRESSION"}
    ):
        raise FeedbackConflict("CHANGE_CANDIDATE_BASELINE_PIN_MISMATCH")
    config = run.configuration
    if (
        config.get("execution_adapter") != ADAPTER
        or config.get("prompt_version_id") != str(payload.target.id)
        or config.get("prompt_checksum") != payload.target.checksum
        or config.get("dataset_split") not in {"TEST", "VALIDATION"}
    ):
        raise FeedbackConflict("CHANGE_CANDIDATE_BASELINE_TARGET_MISMATCH")
    dataset = _platform_dataset(session, run.dataset_version_id)
    scope = {key: getattr(dataset, key) for key in SCOPE}
    if dataset.skill != "WRITING" or any(
        pattern.body["scope"].get(key) != value for key, value in scope.items()
    ):
        raise FeedbackConflict("CHANGE_CANDIDATE_EVALUATION_SCOPE_MISMATCH")
    results = {str(result.id): result for result in evaluation.verify_run(session, run)}
    suite = _platform_dataset(session, payload.suite_version.id)
    if suite.checksum != payload.suite_version.checksum:
        raise FeedbackConflict("CHANGE_CANDIDATE_SUITE_PIN_MISMATCH")
    verify_regression_suite(session, suite)
    if (
        any(getattr(suite, key) != value for key, value in scope.items())
        or suite.provenance.get("split") != config["dataset_split"]
        or any(suite.provenance.get(key) != config.get(key) for key in METHOD_KEYS)
        or any(suite.provenance["source_scope"].get(key) != config.get(key) for key in SOURCE_KEYS)
    ):
        raise FeedbackConflict("CHANGE_CANDIDATE_SUITE_SCOPE_MISMATCH")
    for member in proposal.body["source_feedback"]:
        feedback = session.get(GovernedFeedback, uuid.UUID(member["id"]))
        if feedback is None or feedback.source_kind != "EVALUATION_FAILURE":
            raise FeedbackConflict("CHANGE_CANDIDATE_BASELINE_SOURCE_MISMATCH")
        source = feedback.source
        result = results.get(source.get("result_id", ""))
        if (
            source.get("run_id") != str(run.id)
            or source.get("run_checksum") != run.checksum
            or result is None
            or result.checksum != source.get("result_checksum")
            or result.result.get("hard_fail") is not True
        ):
            raise FeedbackConflict("CHANGE_CANDIDATE_BASELINE_SOURCE_MISMATCH")
    for pin in suite.provenance["case_pins"]:
        case = evaluation._version(session, uuid.UUID(pin["case_version_id"]))
        source = case.provenance["source"]
        result = results.get(source["result_id"])
        if (
            source["run_id"] != str(run.id)
            or source["run_checksum"] != run.checksum
            or result is None
            or source["result_checksum"] != result.checksum
        ):
            raise FeedbackConflict("CHANGE_CANDIDATE_SUITE_SOURCE_MISMATCH")
    for key, expected in (
        ("suite_version", payload.suite_version),
        ("baseline_run", payload.baseline_run),
    ):
        linked = proposal.body["links"].get(key)
        if linked is not None and {
            name: linked.get(name) for name in ("id", "checksum")
        } != expected.model_dump(mode="json"):
            raise FeedbackConflict("CHANGE_CANDIDATE_PROPOSAL_EVIDENCE_MISMATCH")
    return scope


def _checksum(row: GovernedChangeCandidate) -> str:
    return canonical_checksum(
        {"id": str(row.id), "created_by": str(row.created_by), "body": row.body}
    )


def verify_change_candidate(session: Session, row: GovernedChangeCandidate) -> None:
    if row.organisation_id is not None:
        raise AuthorizationError("CHANGE_CANDIDATE_PLATFORM_EVIDENCE_REQUIRED")
    body = row.body
    if (
        row.checksum != _checksum(row)
        or body["schema_version"] != "1"
        or body["change_code"] != CHANGE
        or body["risk"] != "ASSESSMENT_AFFECTING"
        or body["target"]["id"] != str(row.baseline_prompt_version_id)
        or body["draft"]["id"] != str(row.draft_prompt_version_id)
        or body["proposal"]["id"] != str(row.proposal_version_id)
        or body["suite_version"]["id"] != str(row.suite_version_id)
    ):
        raise FeedbackConflict("CHANGE_CANDIDATE_PIN_MISMATCH")
    proposal, pattern = _proposal_basis(
        session,
        Pin.model_validate(body["proposal"]),
        Pin.model_validate(body["approval"]),
        current=False,
    )
    baseline = _read_prompt(
        session, Pin(id=row.baseline_prompt_version_id, checksum=body["target"]["checksum"])
    )
    draft = _read_prompt(
        session, Pin(id=row.draft_prompt_version_id, checksum=body["draft"]["checksum"])
    )
    if (
        body["target"] != _target_projection(baseline)
        or body["target"] != proposal.body["target"]
        or body["draft"] != _target_projection(draft)
        or body["pattern"] != _pin(pattern)
        or body["source_feedback"] != proposal.body["source_feedback"]
        or body["change_category"] != proposal.body["change_category"]
        or body["evaluation_requirements"] != proposal.body["evaluation_requirements"]
        or baseline.id == draft.id
        or not (baseline.instruction_template or "").strip()
        or draft.prompt_definition_id != baseline.prompt_definition_id
        or draft.created_by != row.created_by
        or draft.status != AIArtifactStatus.DRAFT
        or any(
            (
                draft.reviewed_at,
                draft.reviewed_by,
                draft.approved_at,
                draft.approved_by,
                draft.published_at,
                draft.published_by,
            )
        )
        or _prompt_payload(draft) != {**_prompt_payload(baseline), "instruction_template": None}
    ):
        raise FeedbackConflict("CHANGE_CANDIDATE_DRAFT_PROVENANCE_INVALID")
    suite = session.get(EvaluationDatasetVersion, row.suite_version_id)
    baseline_run = session.get(EvaluationRun, uuid.UUID(body["baseline_run"]["id"]))
    if (
        suite is None
        or _pin(suite) != body["suite_version"]
        or baseline_run is None
        or _pin(baseline_run) != body["baseline_run"]
    ):
        raise FeedbackConflict("CHANGE_CANDIDATE_EVIDENCE_PIN_MISMATCH")


def require_current_candidate_authority(
    session: Session, context: TenantContext, row: GovernedChangeCandidate, *, lock: bool = True
) -> None:
    _require(session, context, evaluation.READ)
    if row.organisation_id is not None or row.checksum != _checksum(row):
        raise FeedbackConflict("CHANGE_CANDIDATE_PIN_MISMATCH")
    _proposal_basis(
        session,
        Pin.model_validate(row.body["proposal"]),
        Pin.model_validate(row.body["approval"]),
        current=True,
        lock=lock,
    )
    target = TargetPin.model_validate(
        {key: row.body["target"][key] for key in ("type", "id", "checksum")}
    )
    if lock:
        current_target = resolve_target(session, target)
    else:
        prompt = _read_prompt(session, target)
        if prompt.status.value not in CURRENT:
            raise FeedbackConflict("FEEDBACK_TARGET_VERSION_MISMATCH")
        current_target = _target_projection(prompt)
    if current_target != row.body["target"]:
        raise FeedbackConflict("FEEDBACK_TARGET_VERSION_MISMATCH")


def create_change_candidate(
    session: Session, context: TenantContext, payload: ChangeCandidateInput
) -> GovernedChangeCandidate:
    trusted = _require(session, context, evaluation.READ, evaluation.RUN, evaluation.MANAGE)
    request = payload.model_dump(mode="json")
    previous = common.operation(session, trusted, payload.operation_id, CREATED, request)
    if previous is not None:
        existing = session.get(GovernedChangeCandidate, previous.entity_id)
        if (
            existing is None
            or previous.entity_kind != KIND
            or previous.state["candidate_checksum"] != existing.checksum
        ):
            raise FeedbackConflict("CHANGE_CANDIDATE_PIN_MISMATCH")
        verify_change_candidate(session, existing)
        return existing
    proposal, pattern = _proposal_basis(session, payload.proposal, payload.approval, current=True)
    if session.scalar(
        select(GovernedChangeCandidate.id).where(
            GovernedChangeCandidate.proposal_version_id == proposal.id
        )
    ):
        raise FeedbackConflict("CHANGE_CANDIDATE_ALREADY_EXISTS")
    if proposal.body["hypothesis_code"] != HYPOTHESIS:
        raise FeedbackConflict("CHANGE_CANDIDATE_HYPOTHESIS_UNSUPPORTED")
    if (
        proposal.body["evaluation_requirements"]["reference_evidence"]
        != "NO_ADDITIONAL_REFERENCE_REQUIRED"
    ):
        raise FeedbackConflict("CHANGE_CANDIDATE_APPROVED_REFERENCE_REQUIRED")
    target = resolve_target(session, TargetPin.model_validate(payload.target.model_dump()))
    if target != proposal.body["target"] or proposal.body["change_category"] not in {
        "PROMPT_REVISION",
        "VALIDATION_GUARD",
    }:
        raise FeedbackConflict("CHANGE_CANDIDATE_PROPOSAL_TARGET_MISMATCH")
    baseline = _read_prompt(session, payload.target)
    if not (baseline.instruction_template or "").strip():
        raise FeedbackConflict("CHANGE_CANDIDATE_LEGACY_INSTRUCTIONS_UNAVAILABLE")
    scope = _baseline_scope(session, payload, proposal, pattern)
    identifier = uuid.uuid4()
    draft = AIOrchestrationService.create_prompt_version(
        session,
        baseline.definition,
        version_label="evaluation-draft-" + identifier.hex,
        system_template=baseline.system_template,
        configuration=copy.deepcopy(baseline.configuration),
        context=trusted,
    )
    row = GovernedChangeCandidate(
        id=identifier,
        organisation_id=None,
        proposal_version_id=proposal.id,
        baseline_prompt_version_id=baseline.id,
        draft_prompt_version_id=draft.id,
        suite_version_id=payload.suite_version.id,
        body={
            "schema_version": "1",
            "proposal": _pin(proposal),
            "approval": payload.approval.model_dump(mode="json"),
            "pattern": _pin(pattern),
            "source_feedback": proposal.body["source_feedback"],
            "target": target,
            "draft": _target_projection(draft),
            "suite_version": payload.suite_version.model_dump(mode="json"),
            "baseline_run": payload.baseline_run.model_dump(mode="json"),
            "change_category": proposal.body["change_category"],
            "change_code": CHANGE,
            "risk": "ASSESSMENT_AFFECTING",
            "scope": scope,
            "evaluation_requirements": proposal.body["evaluation_requirements"],
        },
        created_by=trusted.user_id,
        created_at=common.utcnow(),
    )
    row.checksum = _checksum(row)
    session.add(row)
    session.flush()
    common.record(
        session,
        trusted,
        payload.operation_id,
        CREATED,
        request,
        entity_kind=KIND,
        entity_id=row.id,
        state={
            "status": "AWAITING_EVALUATION",
            "candidate_checksum": row.checksum,
            "production_readiness": "NOT_READY",
            "evaluation": None,
        },
    )
    return row


def resolve_change_candidate(
    session: Session, context: TenantContext, pin: Pin
) -> GovernedChangeCandidate:
    _require(session, context, evaluation.READ)
    row = session.get(GovernedChangeCandidate, pin.id)
    if row is None or row.checksum != pin.checksum:
        raise FeedbackConflict("CHANGE_CANDIDATE_PIN_MISMATCH")
    verify_change_candidate(session, row)
    return row


def candidate_summary(
    session: Session, context: TenantContext, row: GovernedChangeCandidate
) -> dict[str, Any]:
    _require(session, context, evaluation.READ)
    verify_change_candidate(session, row)
    return {
        **_pin(row),
        **copy.deepcopy(row.body),
        "organisation_id": None,
        "created_by": str(row.created_by),
        "created_at": row.created_at.astimezone(timezone.utc).isoformat(),
        "production_activation": False,
    }
