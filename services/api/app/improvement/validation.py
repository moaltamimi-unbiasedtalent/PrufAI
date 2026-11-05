"""Isolated Writing request and test-adapter execution for an exact selection."""

from __future__ import annotations

import json
import re
import uuid
from datetime import timezone
from typing import Any, Literal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.authorization import AuthorizationError, TenantContext
from app.config import Settings
from app.evaluation import prompt_policy
from app.evaluation import service as evaluation
from app.evaluation.contracts import Contract, EvaluationCase
from app.feedback import common
from app.feedback.common import FeedbackConflict, Pin
from app.improvement.boundary import NONPRODUCTION, require_validation_environment
from app.models import EvaluationRun, PromptVersion
from app.models.feedback import FeedbackOperation
from app.writing_assessment.service import canonical_checksum
from app.writing_execution.execution import WritingExecutionService
from app.writing_execution.provider import build_output_schema
from app.writing_execution.runtime import (
    SYSTEM_BOUNDARY,
    TestOnlyWritingProvider,
    build_request,
    validate_execution_configuration,
)

KIND = "PROMPT_VALIDATE"
ACTION = "prompt_validation_completed"
TEST_CONFIGURATION = {"schema_version": "1", "adapter_version": "1", "scenario": "success"}


class ValidationInput(Contract):
    operation_id: uuid.UUID
    environment: Literal["NONPRODUCTION_VALIDATION", "PRODUCTION"]
    selection: Pin


def _pin(row: FeedbackOperation) -> dict[str, str]:
    return {"id": str(row.id), "checksum": row.checksum}


def _execute_case(
    case: dict[str, Any], prompt: PromptVersion, operation_id: uuid.UUID, settings: Settings
) -> dict[str, Any]:
    # Reuse the strict original-synthetic fixture and request policy validation.
    _, _, _, policy = prompt_policy.measure_prompt_request(case, prompt)
    if policy["hard_fail"]:
        raise FeedbackConflict("PROMPT_VALIDATION_REQUEST_POLICY_FAILED")
    parsed = EvaluationCase.model_validate(case)
    execution_id = uuid.uuid5(operation_id, parsed.case_id)
    canary = parsed.input.text
    assert canary is not None
    rubric = {
        "criteria": [
            {"code": item.code, "max_points": str(item.max_points)}
            for item in parsed.reference.criteria
        ],
        "performance_levels": [{"code": "TECHNICAL_TEST"}],
        "evidence_policy": {"required": True, "minimum_references": 1},
        "aggregation_policy": {},
    }
    rubric_checksum = canonical_checksum(rubric)
    input_ = {
        "schema_version": "1",
        "execution_id": str(execution_id),
        "ai_run_id": str(uuid.uuid5(execution_id, "isolated-synthetic-envelope")),
        "source": {
            "response_revision_id": "synthetic:" + str(execution_id),
            "response_checksum": canonical_checksum(parsed.input.evidence_identity),
            "rubric_version_id": "synthetic:" + rubric_checksum[:32],
            "rubric_checksum": rubric_checksum,
        },
        "candidate_text": canary,
        "candidate_text_untrusted": True,
        "task_prompt": "Original synthetic request packaging exercise.",
        "language": parsed.input.language,
        "rubric": rubric,
        "approved_sources": [],
    }
    adapter = TestOnlyWritingProvider()
    snapshot: dict[str, Any] = {
        "provider": adapter.provider,
        "model_name": adapter.model_name,
        "adapter_version": adapter.adapter_version,
        "tool_policy": [],
        "configuration": TEST_CONFIGURATION,
        "system_template": prompt.system_template,
        "instruction_template": prompt.instruction_template,
    }
    if validate_execution_configuration(snapshot, settings)["availability"] != "TEST_ONLY":
        raise AuthorizationError("PROMPT_VALIDATION_TEST_ADAPTER_UNAVAILABLE")
    request = build_request(input_, snapshot)
    encoded = json.dumps(input_, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    instructions = request.get("instructions")
    output_format = request.get("text", {}).get("format", {})
    if (
        not isinstance(instructions, str)
        or instructions
        != SYSTEM_BOUNDARY
        + "\n"
        + prompt.system_template
        + "\n"
        + (prompt.instruction_template or "")
        or canary in instructions
        or request.get("model") != adapter.model_name
        or request.get("tools") != []
        or request.get("store") is not False
        or request.get("stream") is not False
        or request.get("input")
        != [{"role": "user", "content": [{"type": "input_text", "text": encoded}]}]
        or output_format.get("strict") is not True
        or output_format
        != {
            "type": "json_schema",
            "name": "writing_proposal_v1",
            "strict": True,
            "schema": build_output_schema(input_),
        }
    ):
        raise FeedbackConflict("PROMPT_VALIDATION_REQUEST_POLICY_FAILED")
    # Execute from the actual serialized user channel. The deterministic adapter
    # does not interpret trusted prompt semantics and cannot call a provider.
    native_input = json.loads(request["input"][0]["content"][0]["text"])
    input_checksum = canonical_checksum(native_input)
    result = adapter.generate(
        native_input,
        configuration=TEST_CONFIGURATION,
        limits={"max_input_bytes": 200000, "max_output_bytes": 250000},
    )
    if (
        (result.provider, result.model_name, result.adapter_version)
        != (adapter.provider, adapter.model_name, adapter.adapter_version)
        or result.metadata.get("test_only") is not True
        or result.metadata
        != {
            "execution_id": str(execution_id),
            "input_checksum": input_checksum,
            "output_schema_version": "1",
            "test_only": True,
        }
        or re.fullmatch(r"test-only-writing-[0-9a-f-]{36}", result.provider_request_id) is None
    ):
        raise FeedbackConflict("PROMPT_VALIDATION_TEST_RECEIPT_INVALID")
    validated = WritingExecutionService.validate_proposal(result.proposal, native_input)
    return {
        "case_id": parsed.case_id,
        "case_checksum": canonical_checksum(case),
        "input_checksum": input_checksum,
        "request_checksum": canonical_checksum(request),
        "output_checksum": canonical_checksum(validated),
        "adapter_receipt_checksum": canonical_checksum(
            {"provider_request_id": result.provider_request_id, "metadata": result.metadata}
        ),
        "validated_criteria_count": len(validated["criteria"]),
    }


def validate_selection(
    session: Session,
    context: TenantContext,
    settings: Settings,
    candidate_id: uuid.UUID,
    payload: ValidationInput,
) -> FeedbackOperation:
    from app.improvement.promotion import resolve_nonproduction_selection

    trusted = common.require_platform(
        session, context, evaluation.READ, evaluation.RUN, evaluation.MANAGE
    )
    if payload.environment != NONPRODUCTION:
        raise AuthorizationError("PROMPT_VALIDATION_NONPRODUCTION_ONLY")
    require_validation_environment(session, settings)
    request = {"candidate_id": str(candidate_id), **payload.model_dump(mode="json")}
    previous = common.operation(session, trusted, payload.operation_id, ACTION, request)
    if previous is not None:
        if previous.entity_kind != KIND or previous.entity_id != candidate_id:
            raise FeedbackConflict("PROMPT_VALIDATION_RECEIPT_MISMATCH")
        return previous
    # Separate lock namespace, serializing append-only validation revisions for this candidate.
    session.execute(
        text("SELECT pg_advisory_xact_lock(:namespace, :key)"),
        {
            "namespace": 4130035,
            "key": int(candidate_id.hex[:8], 16) - 2**31,
        },
    )
    selection, prompt = resolve_nonproduction_selection(
        session, trusted, settings, candidate_id, payload.selection
    )
    selected = selection.state["selected"]
    run_pin = Pin.model_validate(selected["candidate_run"])
    run = session.get(EvaluationRun, run_pin.id)
    if (
        run is None
        or run.checksum != run_pin.checksum
        or run.hard_gate != "PASS"
        or run.configuration.get("execution_adapter") != prompt_policy.ADAPTER
        or run.configuration.get("prompt_version_id") != str(prompt.id)
        or run.configuration.get("prompt_checksum") != prompt.checksum
        or run.configuration.get("change_candidate") != selected["candidate"]
    ):
        raise FeedbackConflict("PROMPT_VALIDATION_EVALUATION_PIN_MISMATCH")
    evaluation.verify_run(session, run)
    dataset = evaluation._version(session, run.dataset_version_id)
    cases = [
        _execute_case(case, prompt, payload.operation_id, settings)
        for case in prompt_policy._selected(session, dataset, run.configuration["dataset_split"])
    ]
    last = session.scalar(
        select(FeedbackOperation)
        .where(FeedbackOperation.entity_kind == KIND, FeedbackOperation.entity_id == candidate_id)
        .order_by(FeedbackOperation.revision.desc())
        .limit(1)
    )
    return common.record(
        session,
        trusted,
        payload.operation_id,
        ACTION,
        request,
        entity_kind=KIND,
        entity_id=candidate_id,
        previous=last,
        state={
            "environment": NONPRODUCTION,
            "status": "COMPLETED",
            "execution_class": "TEST_ONLY",
            "model_execution": "NOT_RUN",
            "selection": _pin(selection),
            "prompt": selected["prompt"],
            "dataset_version": {"id": str(dataset.id), "checksum": dataset.checksum},
            "case_count": len(cases),
            "cases": cases,
        },
    )


def validation_summary(row: FeedbackOperation) -> dict[str, Any]:
    common.verify_receipt(row)
    if row.entity_kind != KIND or row.action != ACTION or row.organisation_id is not None:
        raise FeedbackConflict("PROMPT_VALIDATION_RECEIPT_MISMATCH")
    return {
        **_pin(row),
        "created_at": row.created_at.astimezone(timezone.utc).isoformat(),
        **{
            key: row.state[key]
            for key in (
                "environment",
                "status",
                "execution_class",
                "model_execution",
                "selection",
                "prompt",
                "dataset_version",
                "case_count",
                "cases",
            )
        },
        "production_activation": False,
    }


def validation_history(session: Session, candidate_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(FeedbackOperation)
        .where(
            FeedbackOperation.entity_kind == KIND,
            FeedbackOperation.entity_id == candidate_id,
            FeedbackOperation.organisation_id.is_(None),
        )
        .order_by(FeedbackOperation.revision.desc())
        .limit(100)
    )
    return [validation_summary(row) for row in rows]
