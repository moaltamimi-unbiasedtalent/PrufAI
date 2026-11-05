"""Measure native Writing request packaging offline, without running a model."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Literal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.ai_orchestration.service import _checksum as prompt_checksum
from app.authorization import AuthorizationError, TenantContext
from app.evaluation import service as foundation
from app.evaluation.contracts import Contract, EvaluationCase, MetricConfiguration
from app.evaluation.metrics import _Measurements, aggregate_case_results
from app.feedback.common import Pin
from app.models import (
    EvaluationCaseResult,
    EvaluationDataset,
    EvaluationDatasetVersion,
    EvaluationRun,
)
from app.models.ai_orchestration import PromptVersion
from app.writing_assessment.service import canonical_checksum
from app.writing_execution.provider import build_output_schema
from app.writing_execution.registry import PURPOSE as WRITING_PURPOSE
from app.writing_execution.runtime import SYSTEM_BOUNDARY, build_request

ADAPTER = "OFFLINE_PROMPT_POLICY_EVAL_V1"
METHOD = "DETERMINISTIC_PROMPT_REQUEST_POLICY"
METHOD_VERSION = "governed-prompt-policy-v1"
EXECUTION_CLASS = "TEST_ONLY"
EVIDENCE_CLASS = "TECHNICAL_TEST"
CHECK_FAILURES = {
    "prompt_channel_isolation": "PROMPT_CHANNEL_ISOLATION_VIOLATION",
    "prompt_request_schema": "STRICT_REQUEST_SCHEMA_MISMATCH",
    "prompt_request_authority": "REQUEST_AUTHORITY_POLICY_MISMATCH",
    "prompt_request_input_integrity": "REQUEST_INPUT_IDENTITY_MISMATCH",
}
CANARY = re.compile(r"PRUFAI_SYNTHETIC_CANARY_[0-9a-f]{32}")
LIMITATIONS = [
    "Offline Writing request packaging only. No provider or model was invoked.",
    "Synthetic canaries measure exact channel isolation, not semantic jailbreak resistance.",
    "Model output, German assessment validity, and empirical release readiness were not evaluated.",
]


class PromptPolicyBaselineInput(Contract):
    operation_id: uuid.UUID
    prompt: Pin
    dataset: Pin
    standard_pack: Pin
    split: Literal["TEST", "VALIDATION"]


def _prompt(session: Session, pin: Pin, *, draft: bool | None = None) -> PromptVersion:
    row = session.get(PromptVersion, pin.id)
    if (
        row is None
        or row.definition.purpose != WRITING_PURPOSE
        or row.checksum != pin.checksum
        or row.input_schema_version != "1"
        or row.output_schema_version != "1"
        or not 1 <= len(row.system_template) <= 16000
        or len(row.instruction_template or "") > 16000
        or draft is True
        and row.status.value != "DRAFT"
        or draft is False
        and row.status.value not in {"ACTIVE", "PUBLISHED"}
        or prompt_checksum(
            {
                "system_template": row.system_template,
                "instruction_template": row.instruction_template,
                "configuration": row.configuration,
                "input_schema_version": row.input_schema_version,
                "output_schema_version": row.output_schema_version,
            }
        )
        != pin.checksum
    ):
        raise foundation.EvaluationConflict("PROMPT_POLICY_EXACT_WRITING_PROMPT_REQUIRED")
    return row


def _selected(
    session: Session,
    dataset: EvaluationDatasetVersion,
    split: str,
) -> list[dict[str, Any]]:
    from app.evaluation.regression_registry import _is_published

    owner = session.get(EvaluationDataset, dataset.dataset_id)
    if owner is None or owner.organisation_id is not None:
        raise AuthorizationError("PROMPT_POLICY_PUBLIC_SYNTHETIC_DATASET_REQUIRED")
    if split not in {"TEST", "VALIDATION"} or any(
        case["split"] == "HELD_OUT" for case in dataset.cases
    ):
        raise AuthorizationError("PROMPT_POLICY_HELD_OUT_UNAVAILABLE")
    provenance = dataset.provenance
    if (
        not _is_published(dataset)
        or dataset.assessment_family != "LANGUAGE"
        or dataset.skill != "WRITING"
        or dataset.evidence_class != EVIDENCE_CLASS
        or provenance.get("origin") != "ORIGINAL_SYNTHETIC"
        or provenance.get("source_legal_status") != "OWNED_ORIGINAL"
        or provenance.get("retention_classification") != "SYNTHETIC_TEST"
    ):
        raise AuthorizationError("PROMPT_POLICY_PUBLIC_SYNTHETIC_DATASET_REQUIRED")
    cases = [case for case in dataset.cases if case["split"] == split]
    if not cases:
        raise ValueError("PROMPT_POLICY_SELECTED_SPLIT_EMPTY")
    if len(cases) > 100 or len({case["input"].get("text") for case in cases}) != len(cases):
        raise ValueError("PROMPT_POLICY_DISTINCT_BOUNDED_CANARY_COHORT_REQUIRED")
    return cases


def measure_prompt_request(case: dict[str, Any], prompt: PromptVersion) -> foundation.PreparedCase:
    parsed = EvaluationCase.model_validate(case)
    input_, reference = parsed.input, parsed.reference
    canary = input_.text
    if (
        parsed.kind != "WRITING"
        or canary is None
        or CANARY.fullmatch(canary) is None
        or input_.modality != "TEXT"
        or not input_.adversarial
        or input_.audio_kind != "NONE"
        or input_.audio_sha256 is not None
        or input_.duration_ms is not None
        or reference.segments_supported
        or parsed.synthetic_observation is not None
        or parsed.execution_job_id is not None
        or reference.label_kind != "SYNTHETIC_EXPECTATION"
        or reference.text is not None
        or reference.sources
        or reference.citation_support
        or not 1 <= len(reference.criteria) <= 30
        or input_.evidence_identity != {"synthetic_fixture_id": "synthetic:" + canary}
        or reference.evidence_identity != input_.evidence_identity
    ):
        raise ValueError("PROMPT_POLICY_SYNTHETIC_CANARY_CASE_REQUIRED")
    model_input = {
        "candidate_text": canary,
        "source": {"response_checksum": canonical_checksum(input_.evidence_identity)},
        "rubric": {"criteria": [{"code": row.code} for row in reference.criteria]},
    }
    # An inert model name permits native serialization without loading any provider configuration.
    request = build_request(
        model_input,
        {
            "model_name": "offline-prompt-policy-only",
            "system_template": prompt.system_template,
            "instruction_template": prompt.instruction_template,
            "configuration": {},
        },
    )
    user_content = [
        {
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": json.dumps(
                        model_input, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    ),
                }
            ],
        }
    ]
    instructions = request.get("instructions")
    user_data_intact = request.get("input") == user_content
    format_ = request.get("text", {}).get("format", {})
    checks = {
        "prompt_channel_isolation": user_data_intact
        and isinstance(instructions, str)
        and canary not in instructions,
        "prompt_request_schema": format_
        == {
            "type": "json_schema",
            "name": "writing_proposal_v1",
            "strict": True,
            "schema": build_output_schema(model_input),
        },
        "prompt_request_authority": request.get("tools") == []
        and request.get("store") is False
        and request.get("stream") is False
        and isinstance(instructions, str)
        and instructions.startswith(SYSTEM_BOUNDARY + "\n"),
        "prompt_request_input_integrity": user_data_intact,
    }
    observation = {"request_checksum": canonical_checksum(request), "checks": checks}
    measured = _Measurements(parsed, EVIDENCE_CLASS)
    for code, passed in checks.items():
        measured.check(code, int(passed), 1, CHECK_FAILURES[code])
    result = {
        "case_id": parsed.case_id,
        "kind": parsed.kind,
        "method_version": METHOD_VERSION,
        "evidence_class": EVIDENCE_CLASS,
        "reference_class": parsed.reference.label_kind,
        "checks": measured.checks,
        "metrics": measured.metrics,
        "criterion_comparisons": [],
        "failure_categories": sorted(measured.failures),
        "hard_fail": bool(measured.failures),
        "quality_claim": "OFFLINE_REQUEST_POLICY_ONLY",
    }
    receipt = {
        "adapter": ADAPTER,
        "execution_class": EXECUTION_CLASS,
        "model_execution": "NOT_RUN",
        "prompt_version_id": str(prompt.id),
        "prompt_checksum": prompt.checksum,
        "request_checksum": observation["request_checksum"],
    }
    return case, observation, receipt, result


def _configuration(
    session: Session,
    dataset: EvaluationDatasetVersion,
    pack: Pin,
    prompt: Pin,
    split: str,
    *,
    candidate: Pin | None = None,
    baseline: Pin | None = None,
    suite: Pin | None = None,
) -> dict[str, Any]:
    # Reuse existing pack validity, published scope and metric configuration checks.
    base = foundation.validate_configuration(
        session,
        dataset,
        foundation.RunConfiguration(
            standard_pack_version_id=pack.id,
            standard_pack_checksum=pack.checksum,
            execution_adapter="SYNTHETIC_OBSERVATION_REPLAY_V1",
            evaluation_method="DETERMINISTIC_REFERENCE_COMPARISON",
            evaluation_method_version="1",
            metric_configuration=MetricConfiguration(),
        ),
    )
    return {
        **base,
        "dataset_split": split,
        "execution_adapter": ADAPTER,
        "prompt_version_id": str(prompt.id),
        "prompt_checksum": prompt.checksum,
        "evaluation_method": METHOD,
        "implementation_version": METHOD_VERSION,
        "change_candidate": candidate.model_dump(mode="json") if candidate else None,
        "baseline_run": baseline.model_dump(mode="json") if baseline else None,
        "suite_version": suite.model_dump(mode="json") if suite else None,
        "not_applicable_reasons": {
            key: "OFFLINE_REQUEST_POLICY_NO_MODEL_OR_ASSESSMENT_EXECUTION"
            for key in base["not_applicable_reasons"]
            if key != "prompt_version_id"
        },
    }


def _run_request_checksum(dataset: EvaluationDatasetVersion, config: dict[str, Any]) -> str:
    return canonical_checksum(
        {
            "dataset_version_id": str(dataset.id),
            "dataset_checksum": dataset.checksum,
            "configuration": config,
        }
    )


def _execute(
    session: Session,
    context: TenantContext,
    operation_id: uuid.UUID,
    dataset: EvaluationDatasetVersion,
    prompt: PromptVersion,
    config: dict[str, Any],
) -> EvaluationRun:
    request_checksum = _run_request_checksum(dataset, config)
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": operation_id.int % (2**63 - 1)}
        )
    previous = session.scalar(
        select(EvaluationRun).where(EvaluationRun.operation_id == operation_id)
    )
    if previous is not None:
        if previous.request_checksum != request_checksum or previous.created_by != context.user_id:
            raise foundation.EvaluationConflict("EVALUATION_OPERATION_CONFLICT")
        foundation.verify_run(session, previous)
        return previous
    prepared = [
        measure_prompt_request(case, prompt)
        for case in _selected(session, dataset, config["dataset_split"])
    ]
    return foundation.persist_prepared_run(
        session,
        context,
        operation_id,
        request_checksum,
        dataset,
        config,
        prepared,
        execution_class=EXECUTION_CLASS,
        limitations=LIMITATIONS,
    )


def execute_prompt_policy_baseline(
    session: Session,
    context: TenantContext,
    payload: PromptPolicyBaselineInput,
) -> EvaluationRun:
    trusted = foundation.require(
        session, context, foundation.READ, foundation.RUN, foundation.MANAGE
    )
    dataset = foundation._version(session, payload.dataset.id)
    if dataset.checksum != payload.dataset.checksum:
        raise foundation.EvaluationConflict("PROMPT_POLICY_DATASET_PIN_MISMATCH")
    _selected(session, dataset, payload.split)
    prompt = _prompt(session, payload.prompt, draft=False)
    config = _configuration(session, dataset, payload.standard_pack, payload.prompt, payload.split)
    return _execute(session, trusted, payload.operation_id, dataset, prompt, config)


def _baseline(session: Session, pin: Pin) -> EvaluationRun:
    row = session.get(EvaluationRun, pin.id)
    if (
        row is None
        or row.checksum != pin.checksum
        or row.status != "COMPLETED"
        or row.configuration.get("execution_adapter") != ADAPTER
        or row.configuration.get("change_candidate") is not None
    ):
        raise foundation.EvaluationConflict("PROMPT_POLICY_EXACT_BASELINE_REQUIRED")
    return row


def _source_pack(config: dict[str, Any]) -> Pin:
    return Pin(id=config["standard_pack_version_id"], checksum=config["standard_pack_checksum"])


def execute_prompt_policy_candidate(
    session: Session,
    context: TenantContext,
    *,
    operation_id: uuid.UUID,
    change_candidate_id: uuid.UUID,
    change_candidate_checksum: str,
    baseline_run: Pin,
) -> EvaluationRun:
    from app.improvement.candidates import resolve_change_candidate

    trusted = foundation.require(
        session, context, foundation.READ, foundation.RUN, foundation.MANAGE
    )
    candidate_pin = Pin(id=change_candidate_id, checksum=change_candidate_checksum)
    candidate = resolve_change_candidate(session, trusted, candidate_pin)
    body = candidate.body
    if Pin.model_validate(body["baseline_run"]) != baseline_run:
        raise foundation.EvaluationConflict("PROMPT_POLICY_EXACT_BASELINE_REQUIRED")
    baseline = _baseline(session, baseline_run)
    foundation.verify_run(session, baseline)
    dataset = foundation._version(session, baseline.dataset_version_id)
    draft = Pin(id=body["draft"]["id"], checksum=body["draft"]["checksum"])
    prompt = _prompt(session, draft, draft=True)
    config = _configuration(
        session,
        dataset,
        _source_pack(baseline.configuration),
        draft,
        baseline.configuration["dataset_split"],
        candidate=candidate_pin,
        baseline=baseline_run,
        suite=Pin.model_validate(body["suite_version"]),
    )
    return _execute(session, trusted, operation_id, dataset, prompt, config)


def verify_prompt_policy_run(
    session: Session,
    run: EvaluationRun,
    dataset: EvaluationDatasetVersion,
    results: list[EvaluationCaseResult],
) -> None:
    config = run.configuration
    prompt_pin = Pin(id=config["prompt_version_id"], checksum=config["prompt_checksum"])
    prompt = _prompt(session, prompt_pin)
    candidate_pin = (
        Pin.model_validate(config["change_candidate"]) if config["change_candidate"] else None
    )
    baseline_pin = None
    suite_pin = None
    if candidate_pin is not None:
        from app.improvement.candidates import verify_change_candidate
        from app.models.feedback import GovernedChangeCandidate

        candidate = session.get(GovernedChangeCandidate, candidate_pin.id)
        if candidate is None or candidate.checksum != candidate_pin.checksum:
            raise foundation.EvaluationConflict("PROMPT_POLICY_CHANGE_CANDIDATE_PIN_MISMATCH")
        verify_change_candidate(session, candidate)
        draft = candidate.body["draft"]
        baseline_pin = Pin.model_validate(candidate.body["baseline_run"])
        suite_pin = Pin.model_validate(candidate.body["suite_version"])
        baseline = _baseline(session, baseline_pin)
        if (
            prompt_pin != Pin(id=draft["id"], checksum=draft["checksum"])
            or dataset.id != baseline.dataset_version_id
            or dataset.checksum != baseline.dataset_checksum
            or _source_pack(config) != _source_pack(baseline.configuration)
            or config["dataset_split"] != baseline.configuration["dataset_split"]
        ):
            raise foundation.EvaluationConflict("PROMPT_POLICY_CHANGE_CANDIDATE_PIN_MISMATCH")
    expected_config = _configuration(
        session,
        dataset,
        _source_pack(config),
        prompt_pin,
        config["dataset_split"],
        candidate=candidate_pin,
        baseline=baseline_pin,
        suite=suite_pin,
    )
    prepared = [
        measure_prompt_request(case, prompt)
        for case in _selected(session, dataset, config["dataset_split"])
    ]
    by_case = {item.case_id: item for item in results}
    aggregate = aggregate_case_results([result for _, _, _, result in prepared])
    if (
        config != expected_config
        or len(by_case) != len(prepared)
        or run.status != "COMPLETED"
        or run.completed_at is None
        or run.execution_class != EXECUTION_CLASS
        or run.evidence_class != EVIDENCE_CLASS
        or run.limitations != LIMITATIONS
        or run.metrics != aggregate["metrics"]
        or run.failure_count != aggregate["failed_case_count"]
        or run.hard_gate != ("FAIL" if aggregate["hard_fail"] else "PASS")
        or run.request_checksum != _run_request_checksum(dataset, config)
    ):
        raise foundation.EvaluationConflict("PROMPT_POLICY_RUN_PROVENANCE_BROKEN")
    for case, observation, receipt, result in prepared:
        stored = by_case.get(case["case_id"])
        if (
            stored is None
            or stored.input_checksum != canonical_checksum(case)
            or stored.observation_checksum != canonical_checksum(observation)
            or stored.execution_reference != receipt
            or stored.result != result
        ):
            raise foundation.EvaluationConflict("PROMPT_POLICY_RUN_PROVENANCE_BROKEN")
