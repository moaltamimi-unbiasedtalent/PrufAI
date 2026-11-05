"""Persisted comparisons of governed references and exact historical executions.

This service never accepts caller scores, invokes a provider or creates an
assessment. Reference provenance is revalidated under the acting user's current
authority before a comparison is executed. Stored results contain safe metadata
and arithmetic only, and use the existing immutable evaluation run registry.
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, NoReturn

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.authorization import TenantContext, build_tenant_context
from app.evaluation import expert_service as expert
from app.evaluation import service as foundation
from app.evaluation.calibration import METHOD_VERSION, compare_criterion_scores
from app.evaluation.contracts import EvaluationCase, MetricConfiguration
from app.evaluation.expert_evidence import validate_judgment_evidence
from app.evaluation.expert_requests import CalibrationInput
from app.evaluation.metrics import aggregate_case_results, evaluate_case
from app.evaluation.replay import SOURCE_KEYS, load_pinned_observation
from app.models import (
    EvaluationCaseResult,
    EvaluationDataset,
    EvaluationDatasetVersion,
    EvaluationRun,
)
from app.models.expert_evaluation import (
    EvaluationAdjudication,
    EvaluationExaminerQualification,
    EvaluationExpertJudgment,
    EvaluationReference,
)
from app.models.writing_assessment import RubricVersion
from app.writing_assessment.service import RubricService, canonical_checksum

ADAPTER = "GOVERNED_REFERENCE_CALIBRATION_V1"
_CONFIGURATION_KEYS = {
    "execution_adapter",
    "evaluation_method_version",
    "implementation_version",
    "dataset_split",
    "metric_configuration",
    "metric_configuration_checksum",
    "comparison_kind",
    "request",
    "pins",
    "side_receipts",
    "empirical_thresholds",
    "production_activation",
}


@dataclass(frozen=True)
class _ReferencePopulation:
    source: EvaluationReference
    rubric: RubricVersion | None
    judgments: list[EvaluationExpertJudgment]
    adjudication: EvaluationAdjudication
    cases: list[dict[str, Any]]


def _conflict(reason: str) -> NoReturn:
    raise foundation.EvaluationConflict(reason)


def _qualification_integrity(
    session: Session,
    source: EvaluationReference,
    decision: EvaluationExpertJudgment | EvaluationAdjudication,
) -> EvaluationExaminerQualification:
    qualification = session.get(EvaluationExaminerQualification, decision.qualification_id)
    actor = (
        decision.examiner_id
        if isinstance(decision, EvaluationExpertJudgment)
        else decision.adjudicator_id
    )
    checksum = (
        decision.qualification_checksum
        if isinstance(decision, EvaluationExpertJudgment)
        else decision.body.get("qualification_checksum")
    )
    if (
        qualification is None
        or qualification.status != "APPROVED"
        or qualification.checksum != checksum
        or canonical_checksum(qualification.body) != qualification.checksum
        or qualification.examiner_id != actor
        or qualification.organisation_id != source.organisation_id
        or str(decision.body.get("qualification_id")) != str(qualification.id)
        or decision.body.get("qualification_checksum") != checksum
        or decision.body.get("expected_checksum") != source.checksum
        or qualification.approved_by in {actor, qualification.created_by}
        or qualification.approved_at is None
        or qualification.approval_reference is None
        or qualification.evidence_class not in {"SYNTHETIC_TEST", "VERIFIED_CREDENTIAL"}
    ):
        _conflict("EVALUATION_COMPARISON_QUALIFICATION_INVALID")
    assert qualification is not None
    if any(
        str(qualification.body.get(key)) != str(source.body.get(key))
        for key in (
            "kind",
            "language",
            "rubric_version_id",
            "rubric_checksum",
            "standard_pack_version_id",
            "standard_pack_checksum",
        )
    ):
        _conflict("EVALUATION_COMPARISON_QUALIFICATION_SCOPE_MISMATCH")
    expiry = datetime.fromisoformat(qualification.body["expires_at"].replace("Z", "+00:00"))
    if not qualification.approved_at <= decision.created_at < expiry:
        _conflict("EVALUATION_COMPARISON_QUALIFICATION_TIMING_INVALID")
    return qualification


def _population(
    session: Session,
    dataset: EvaluationDatasetVersion,
    context: TenantContext | None,
    *,
    lock_evidence: bool = True,
) -> _ReferencePopulation:
    provenance = dataset.provenance
    if dataset.status != "PUBLISHED" or provenance.get("origin") != "GOVERNED_REFERENCE":
        _conflict("EVALUATION_PUBLISHED_GOVERNED_REFERENCE_REQUIRED")
    if dataset.skill not in {"WRITING", "SPEAKING", "TRANSCRIPTION"}:
        raise ValueError("EVALUATION_CALIBRATION_SKILL_NOT_SUPPORTED")
    source_id = uuid.UUID(provenance["reference_id"])
    source = (
        expert._reference(session, context, source_id, lock=lock_evidence)
        if context is not None
        else session.get(EvaluationReference, source_id)
    )
    owner = session.get(EvaluationDataset, dataset.dataset_id)
    if (
        source is None
        or source.dataset_id != dataset.dataset_id
        or source.status != "APPROVED"
        or source.checksum != provenance.get("reference_checksum")
        or canonical_checksum(source.body) != source.checksum
        or source.approved_by is None
        or source.approved_by == source.created_by
        or source.approved_at is None
        or not source.approval_reference
        or owner is None
        or owner.organisation_id != source.organisation_id
        or source.split != provenance.get("split")
        or source.kind != dataset.skill
        or source.body.get("assessment_family") != dataset.assessment_family
        or source.body.get("language") != dataset.language
        or source.body.get("cefr_level") != dataset.cefr_level
        or provenance.get("purpose") != "EVALUATION_ONLY"
        or provenance.get("evidence_class") != dataset.evidence_class
        or source.evidence_class not in {"SYNTHETIC_TEST", "APPROVED_REFERENCE"}
        or any(
            provenance.get("source_scope", {}).get(key) != source.body.get(key)
            for key in SOURCE_KEYS
        )
    ):
        _conflict("EVALUATION_COMPARISON_REFERENCE_PROVENANCE_INVALID")
    assert source is not None
    rubric = (
        session.get(RubricVersion, uuid.UUID(source.body["rubric_version_id"]))
        if source.body.get("rubric_version_id")
        else None
    )
    if dataset.skill in {"WRITING", "SPEAKING"} and (
        rubric is None
        or rubric.checksum != source.body["rubric_checksum"]
        or canonical_checksum(RubricService.checksum_payload(rubric)) != rubric.checksum
        or rubric.status.value not in {"PUBLISHED", "ACTIVE", "RETIRED", "SUPERSEDED"}
    ):
        _conflict("EVALUATION_COMPARISON_RUBRIC_INVALID")
    judgments = expert._judgments(session, source.id)
    if (
        len(judgments) != 2
        or len({row.examiner_id for row in judgments}) != 2
        or any(row.examiner_id == source.created_by for row in judgments)
        or {(str(row.id), row.checksum) for row in judgments}
        != {(str(pin["id"]), pin["checksum"]) for pin in provenance.get("judgment_pins", [])}
    ):
        _conflict("EVALUATION_COMPARISON_EXACT_INDEPENDENT_JUDGMENTS_REQUIRED")
    adjudication = session.get(EvaluationAdjudication, uuid.UUID(provenance["adjudication_id"]))
    if (
        adjudication is None
        or adjudication.reference_id != source.id
        or adjudication.checksum != provenance.get("adjudication_checksum")
        or canonical_checksum(adjudication.body) != adjudication.checksum
        or adjudication.adjudicator_id
        in {source.created_by, *(row.examiner_id for row in judgments)}
        or {(str(pin["id"]), pin["checksum"]) for pin in adjudication.body.get("judgment_pins", [])}
        != {(str(row.id), row.checksum) for row in judgments}
    ):
        _conflict("EVALUATION_COMPARISON_EXACT_ADJUDICATION_REQUIRED")
    assert adjudication is not None
    qualifications = []
    decisions: list[EvaluationExpertJudgment | EvaluationAdjudication] = [*judgments, adjudication]
    for decision in decisions:
        if decision.reference_id != source.id or decision.reference_checksum != source.checksum:
            _conflict("EVALUATION_COMPARISON_REFERENCE_BINDING_INVALID")
        qualification = _qualification_integrity(session, source, decision)
        qualifications.append(qualification)
        if context is not None:
            actor = (
                decision.examiner_id
                if isinstance(decision, EvaluationExpertJudgment)
                else decision.adjudicator_id
            )
            expert._qualification(
                session,
                build_tenant_context(session, actor),
                source,
                qualification.id,
                qualification.checksum,
                lock=lock_evidence,
            )
        validate_judgment_evidence(
            source.body,
            rubric,
            decision.body["criteria"],
            decision.body.get("transcript"),
            decision.body.get("relevance", []),
        )
    synthetic = source.evidence_class == "SYNTHETIC_TEST" or any(
        qualification.evidence_class == "SYNTHETIC_TEST" for qualification in qualifications
    )
    if dataset.evidence_class != ("SYNTHETIC_TEST" if synthetic else "EXPERT_LABELLED"):
        _conflict("EVALUATION_COMPARISON_EVIDENCE_CLASS_INVALID")
    # Current publication derives one case from one source. Never copy one
    # judgment over additional evidence; a future multi-source publication must
    # supply one exact source/decision binding per case before it is eligible.
    cases = copy.deepcopy(dataset.cases)
    if len(cases) != 1:
        _conflict("EVALUATION_MULTI_SOURCE_CALIBRATION_BINDINGS_REQUIRED")
    for case in cases:
        parsed = EvaluationCase.model_validate(case)
        if (
            parsed.case_id != "reference-" + str(source.id)
            or parsed.split != source.split
            or parsed.kind != source.kind
            or canonical_checksum(parsed.input.model_dump(mode="json"))
            != canonical_checksum(source.body["input"])
            or parsed.reference.evidence_identity != parsed.input.evidence_identity
            or parsed.execution_job_id != provenance.get("execution_job_id")
            or parsed.reference.label_kind
            != (
                "SYNTHETIC_EXPECTATION"
                if dataset.evidence_class == "SYNTHETIC_TEST"
                else "ADJUDICATED_EXPERT"
            )
            or parsed.synthetic_observation is not None
        ):
            _conflict("EVALUATION_COMPARISON_EXACT_CASE_BINDING_REQUIRED")
        reference_scores = {row.code: row.points for row in parsed.reference.criteria}
        adjudicated_scores = {
            row["criterion_code"]: row["points"] for row in adjudication.body["criteria"]
        }
        if reference_scores.keys() != adjudicated_scores.keys() or any(
            value != Decimal(str(adjudicated_scores[key]))
            for key, value in reference_scores.items()
        ):
            _conflict("EVALUATION_COMPARISON_ADJUDICATED_SCORE_DRIFT")
        if dataset.skill == "TRANSCRIPTION" and parsed.reference.text != adjudication.body.get(
            "transcript"
        ):
            _conflict("EVALUATION_COMPARISON_ADJUDICATED_TRANSCRIPT_DRIFT")
    return _ReferencePopulation(source, rubric, judgments, adjudication, cases)


def _human_side(
    population: _ReferencePopulation, identifier: uuid.UUID, checksum: str, *, adjudicated: bool
) -> tuple[dict[str, Any], dict[str, Any]]:
    candidates: list[EvaluationExpertJudgment | EvaluationAdjudication] = (
        [population.adjudication] if adjudicated else list(population.judgments)
    )
    row = next((candidate for candidate in candidates if candidate.id == identifier), None)
    if row is None or row.checksum != checksum:
        _conflict("EVALUATION_COMPARISON_EXACT_SIDE_REQUIRED")
    assert row is not None
    scores = [
        {
            "case_id": case["case_id"],
            "criterion_code": criterion["criterion_code"],
            "points": criterion["points"],
        }
        for case in population.cases
        for criterion in row.body["criteria"]
    ]
    score_side: dict[str, Any] = {
        "kind": "ADJUDICATION" if adjudicated else "HUMAN_JUDGMENT",
        "version_id": str(row.id),
        "checksum": row.checksum,
        "execution_class": "HUMAN",
        "scores": scores,
    }
    if population.source.kind == "TRANSCRIPTION":
        score_side["transcripts"] = {
            case["case_id"]: row.body.get("transcript") for case in population.cases
        }
    return (
        score_side,
        {
            "kind": "ADJUDICATION" if adjudicated else "HUMAN_JUDGMENT",
            "id": str(row.id),
            "checksum": row.checksum,
            "qualification_id": str(row.qualification_id),
            "qualification_checksum": row.body["qualification_checksum"],
        },
    )


def _same_input(case: dict[str, Any]) -> str:
    return canonical_checksum(EvaluationCase.model_validate(case).input.model_dump(mode="json"))


def _ai_side(
    session: Session,
    context: TenantContext,
    dataset: EvaluationDatasetVersion,
    population: _ReferencePopulation,
    identifier: uuid.UUID,
    checksum: str,
) -> tuple[dict[str, Any], dict[str, Any], EvaluationRun]:
    run = session.get(EvaluationRun, identifier)
    selected: EvaluationCaseResult | None = None
    if run is None:
        selected = session.get(EvaluationCaseResult, identifier)
        run = session.get(EvaluationRun, selected.run_id) if selected else None
    if (
        run is None
        or run.status != "COMPLETED"
        or run.hard_gate != "PASS"
        or run.configuration.get("execution_adapter") != "PINNED_EXECUTION_REPLAY_V1"
        or (selected.checksum if selected else run.checksum) != checksum
    ):
        _conflict("EVALUATION_COMPARISON_COMPLETED_PINNED_EXECUTION_REQUIRED")
    assert run is not None
    results = foundation.verify_run(session, run)
    original = foundation._version(session, run.dataset_version_id)
    original_owner = session.get(EvaluationDataset, original.dataset_id)
    if (
        original_owner is None
        or original_owner.organisation_id != population.source.organisation_id
        or original.skill != dataset.skill
        or original.language != dataset.language
        or original.assessment_family != dataset.assessment_family
        or original.cefr_level != dataset.cefr_level
        or any(run.configuration.get(key) != population.source.body.get(key) for key in SOURCE_KEYS)
        or run.configuration.get("dataset_split") != population.source.split
    ):
        _conflict("EVALUATION_COMPARISON_EXECUTION_SCOPE_MISMATCH")
    indexed = {row.case_id: row for row in results}
    candidate_cases = [
        case
        for case in original.cases
        if case["split"] == run.configuration["dataset_split"]
        and (selected is None or case["case_id"] == selected.case_id)
    ]
    expected = {_same_input(case): case for case in population.cases}
    actual = {_same_input(case): case for case in candidate_cases}
    if (
        len(actual) != len(candidate_cases)
        or len(expected) != len(population.cases)
        or actual.keys() != expected.keys()
    ):
        _conflict("EVALUATION_COMPARISON_EXACT_COHORT_REQUIRED")
    scores: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    execution_classes: set[str] = set()
    transcripts: dict[str, str | None] = {}
    for input_checksum, original_case in actual.items():
        result = indexed.get(original_case["case_id"])
        case, observation, receipt, execution_class = load_pinned_observation(
            session, context, original, original_case, run.configuration
        )
        if (
            result is None
            or result.input_checksum != canonical_checksum(case)
            or canonical_checksum(observation) != result.observation_checksum
            or canonical_checksum(receipt) != canonical_checksum(result.execution_reference)
            or result.result.get("hard_fail") is not False
            or case["kind"] != dataset.skill
        ):
            _conflict("EVALUATION_COMPARISON_EXECUTION_RECEIPT_MISMATCH")
        target = expected[input_checksum]
        scores.extend(
            {
                "case_id": target["case_id"],
                "criterion_code": row["criterion_code"],
                "points": row["proposed_points"],
            }
            for row in observation.get("criteria", [])
        )
        if dataset.skill == "TRANSCRIPTION":
            transcripts[target["case_id"]] = observation.get("text")
        receipts.append(
            {
                "case_result_id": str(result.id),
                "checksum": result.checksum,
                "receipt_checksum": canonical_checksum(receipt),
                "case_id": target["case_id"],
            }
        )
        execution_classes.add(execution_class)
    if len(execution_classes) != 1:
        _conflict("EVALUATION_COMPARISON_MIXED_EXECUTION_CLASSES")
    score_side: dict[str, Any] = {
        "kind": "AI_PROPOSAL",
        "version_id": str(identifier),
        "checksum": checksum,
        "execution_class": execution_classes.pop(),
        "scores": scores,
    }
    if dataset.skill == "TRANSCRIPTION":
        score_side["transcripts"] = transcripts
    return (
        score_side,
        {
            "kind": "AI_PROPOSAL",
            "id": str(identifier),
            "checksum": checksum,
            "run_id": str(run.id),
            "run_checksum": run.checksum,
            "case_results": receipts,
        },
        run,
    )


def _transcript_comparison(
    population: _ReferencePopulation,
    payload: CalibrationInput,
    sides: dict[str, dict[str, Any]],
    pins: dict[str, Any],
    evidence_class: str,
) -> dict[str, Any]:
    """Use Phase 1 text measurements without persisting private transcripts.

    Unlike signed score differences, edit distance is directional: the right
    side is the declared reference transcript and the left is the hypothesis.
    """
    measurements = []
    edit_budget = 0
    for original in population.cases:
        case = copy.deepcopy(original)
        left = sides["left"]["transcripts"].get(case["case_id"])
        right = sides["right"]["transcripts"].get(case["case_id"])
        edit_budget += len(left or "") * len(right or "")
        if edit_budget > 5_000_000:
            raise ValueError("EVALUATION_MEASUREMENT_BUDGET_EXCEEDED")
        case["reference"]["text"] = right
        case["reference"]["label_kind"] = (
            "SYNTHETIC_EXPECTATION"
            if evidence_class == "SYNTHETIC_TEST"
            else "ADJUDICATED_EXPERT"
            if sides["right"]["kind"] == "ADJUDICATION"
            else "SINGLE_EXPERT"
        )
        observation = {
            "text": left,
            "language": case["input"]["language"],
            "modality": "AUDIO",
            "audio_sha256": case["input"]["audio_sha256"],
            "evidence_identity": copy.deepcopy(case["reference"]["evidence_identity"]),
        }
        measurement = evaluate_case(
            case,
            observation,
            evidence_class=evidence_class,
            metric_configuration=MetricConfiguration(
                normalization=population.source.body["normalization"]
            ).model_dump(mode="json"),
        )
        if (
            not isinstance(left, str)
            or not left.strip()
            or not isinstance(right, str)
            or not right.strip()
        ):
            measurement["hard_fail"] = True
            measurement["failure_categories"].append("MISSING_PAIRED_REFERENCE")
        measurements.append(measurement)
    aggregate = aggregate_case_results(measurements)
    for metric in aggregate["metrics"].values():
        metric["evidence_class"] = evidence_class
    limitations = list(expert.LIMITATIONS) + [
        "Right transcript is the declared reference; left transcript is the hypothesis.",
        "WER/CER are descriptive edit measurements, "
        "not independent examiner reliability or CEFR validity.",
        "Empirical thresholds are UNCONFIGURED; "
        "uncertainty and sample adequacy are NOT ESTABLISHED.",
        "A model/model transcript comparison measures edit differences, "
        "not accuracy against a human reference.",
    ]
    return {
        "method_version": METHOD_VERSION,
        "comparison_kind": payload.comparison_kind,
        "evidence_class": evidence_class,
        "pins": pins,
        "left": {
            key: value
            for key, value in sides["left"].items()
            if key not in {"scores", "transcripts"}
        },
        "right": {
            key: value
            for key, value in sides["right"].items()
            if key not in {"scores", "transcripts"}
        },
        "case_count": len(population.cases),
        "criterion_count": 0,
        "criteria": [],
        "transcription_metrics": aggregate["metrics"],
        "case_measurements": measurements,
        "empirical_thresholds": "UNCONFIGURED",
        "quality_claim": "NOT_ESTABLISHED",
        "uncertainty": {
            "status": "NOT_ESTIMATED",
            "confidence_interval": None,
            "sample_sufficiency": "NOT_ESTABLISHED",
        },
        "limitations": limitations,
        "production_activation": False,
    }


def _criterion_metrics(comparison: dict[str, Any]) -> dict[str, Any]:
    if "transcription_metrics" in comparison:
        return comparison["transcription_metrics"]
    result = {}
    for criterion in comparison["criteria"]:
        count, paired = criterion["expected_count"], criterion["sample_size"]
        absolute_total = (
            str(
                sum(
                    (Decimal(row["absolute_difference"]) for row in criterion["score_differences"]),
                    Decimal(0),
                )
            )
            if paired
            else None
        )
        signed_total = (
            str(
                sum(
                    (Decimal(row["difference"]) for row in criterion["score_differences"]),
                    Decimal(0),
                )
            )
            if paired
            else None
        )
        kappa = criterion["quadratic_weighted_kappa"]
        for name, numerator, denominator in (
            ("agreement_rate", criterion["agreement_count"], paired),
            ("mean_absolute_error", absolute_total, paired),
            ("signed_mean_difference", signed_total, paired),
            ("paired_coverage", paired, count),
            ("left_coverage", count - criterion["missing_left"], count),
            ("right_coverage", count - criterion["missing_right"], count),
            (
                "quadratic_weighted_kappa",
                kappa["observed_disagreement"],
                kappa["expected_disagreement"],
            ),
        ):
            value = kappa["value"] if name == "quadratic_weighted_kappa" else criterion[name]
            key = criterion["criterion_code"] + "." + name
            result[key] = {
                "name": key,
                "value": value,
                "numerator": numerator,
                "denominator": denominator,
                "sample_size": paired,
                "missing_count": criterion["missing_either"],
                "evidence_class": comparison["evidence_class"],
                "limitations": comparison["limitations"],
                "unavailable_reason": kappa["unavailable_reason"]
                if name == "quadratic_weighted_kappa"
                else "MISSING_PAIRED_REFERENCE"
                if value is None
                else None,
            }
    return result


def verify_calibration_configuration(
    session: Session, run: EvaluationRun, dataset: EvaluationDatasetVersion
) -> None:
    """Verify pinned history without exposing or reauthorizing private evidence.

    Original credential eligibility is verified at its decision timestamp;
    expired credentials never rewrite an already completed measurement. New
    comparison execution separately enforces current credential eligibility.
    """
    configuration = run.configuration
    request = CalibrationInput.model_validate(configuration["request"])
    if (
        configuration.keys() != _CONFIGURATION_KEYS
        or configuration["execution_adapter"] != ADAPTER
        or configuration["evaluation_method_version"] != METHOD_VERSION
        or configuration["implementation_version"] != METHOD_VERSION
        or configuration["empirical_thresholds"] != "UNCONFIGURED"
        or configuration["production_activation"] is not False
        or MetricConfiguration.model_validate(configuration["metric_configuration"]).model_dump(
            mode="json"
        )
        != configuration["metric_configuration"]
        or canonical_checksum(configuration["metric_configuration"])
        != configuration["metric_configuration_checksum"]
        or request.operation_id != run.operation_id
        or request.dataset_version_id != run.dataset_version_id
        or request.dataset_checksum != run.dataset_checksum
        or request.dataset_checksum != dataset.checksum
        or canonical_checksum(request.model_dump(mode="json")) != run.request_checksum
        or configuration["comparison_kind"] != request.comparison_kind
    ):
        _conflict("EVALUATION_RUN_PROVENANCE_BROKEN")
    population = _population(session, dataset, None)
    pins = configuration["pins"]
    if (
        pins.get("dataset_version_id") != str(dataset.id)
        or pins.get("dataset_checksum") != dataset.checksum
        or pins.get("dataset_split") != population.source.split
        or configuration["dataset_split"] != population.source.split
        or configuration["metric_configuration"]["normalization"]
        != population.source.body["normalization"]
        or any(
            pins.get(key) != population.source.body.get(key)
            for key in (
                "rubric_version_id",
                "rubric_checksum",
                "standard_pack_version_id",
                "standard_pack_checksum",
            )
        )
    ):
        _conflict("EVALUATION_RUN_PROVENANCE_BROKEN")
    for side, identifier, checksum in (
        ("left", request.left_id, request.left_checksum),
        ("right", request.right_id, request.right_checksum),
    ):
        receipt = configuration["side_receipts"].get(side, {})
        if receipt.get("id") != str(identifier) or receipt.get("checksum") != checksum:
            _conflict("EVALUATION_RUN_PROVENANCE_BROKEN")
        if receipt.get("kind") == "AI_PROPOSAL":
            original = session.get(EvaluationRun, uuid.UUID(receipt["run_id"]))
            if original is None or original.checksum != receipt.get("run_checksum"):
                _conflict("EVALUATION_RUN_PROVENANCE_BROKEN")
            original_results = foundation.verify_run(session, original)
            allowed = {str(row.id): row for row in original_results}
            for pin in receipt.get("case_results", []):
                result = allowed.get(pin["case_result_id"])
                if (
                    result is None
                    or result.checksum != pin["checksum"]
                    or canonical_checksum(result.execution_reference) != pin["receipt_checksum"]
                ):
                    _conflict("EVALUATION_RUN_PROVENANCE_BROKEN")
            if str(identifier) == str(original.id):
                if original.checksum != checksum:
                    _conflict("EVALUATION_RUN_PROVENANCE_BROKEN")
            elif str(identifier) not in allowed or allowed[str(identifier)].checksum != checksum:
                _conflict("EVALUATION_RUN_PROVENANCE_BROKEN")
        else:
            _, actual = _human_side(
                population, identifier, checksum, adjudicated=receipt.get("kind") == "ADJUDICATION"
            )
            if actual != receipt:
                _conflict("EVALUATION_RUN_PROVENANCE_BROKEN")


def calibrate(session: Session, context: TenantContext, payload: CalibrationInput) -> EvaluationRun:
    """Persist a comparison using only exact authorized stored evidence."""
    trusted = foundation.require(session, context, foundation.MANAGE, foundation.RUN)
    # This is the same operation namespace and lock key as execute_run:
    # both endpoints create an EvaluationRun with a globally unique operation.
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": payload.operation_id.int % (2**63 - 1)},
        )
    dataset = foundation._version(session, payload.dataset_version_id, lock=True)
    if dataset.checksum != payload.dataset_checksum:
        _conflict("EVALUATION_DATASET_CHECKSUM_MISMATCH")
    population = _population(session, dataset, trusted)
    if payload.left_id == payload.right_id:
        _conflict("EVALUATION_COMPARISON_DISTINCT_SIDES_REQUIRED")
    request_checksum = canonical_checksum(payload.model_dump(mode="json"))
    previous = session.scalar(
        select(EvaluationRun).where(EvaluationRun.operation_id == payload.operation_id)
    )
    if previous is not None:
        if previous.created_by != trusted.user_id or previous.request_checksum != request_checksum:
            _conflict("EVALUATION_OPERATION_CONFLICT")
        foundation.verify_run(session, previous)
        return previous
    sides: dict[str, dict[str, Any]] = {}
    receipts: dict[str, dict[str, Any]] = {}
    ai_runs: dict[str, EvaluationRun] = {}
    for side, identifier, checksum in (
        ("left", payload.left_id, payload.left_checksum),
        ("right", payload.right_id, payload.right_checksum),
    ):
        ai = payload.comparison_kind == "VERSION_A_VS_B" or (
            payload.comparison_kind == "AI_PROPOSAL_VS_REFERENCE" and side == "left"
        )
        if ai:
            sides[side], receipts[side], ai_runs[side] = _ai_side(
                session, trusted, dataset, population, identifier, checksum
            )
        else:
            adjudicated = (
                payload.comparison_kind == "ADJUDICATED_VS_HUMAN" and side == "left"
            ) or (
                payload.comparison_kind == "AI_PROPOSAL_VS_REFERENCE"
                and identifier == population.adjudication.id
            )
            sides[side], receipts[side] = _human_side(
                population, identifier, checksum, adjudicated=adjudicated
            )
    pins = {
        "dataset_version_id": str(dataset.id),
        "dataset_checksum": dataset.checksum,
        "dataset_split": population.source.split,
        **{
            key: population.source.body[key]
            for key in (
                "rubric_version_id",
                "rubric_checksum",
                "standard_pack_version_id",
                "standard_pack_checksum",
            )
        },
    }
    for side, run in ai_runs.items():
        config = run.configuration
        if (
            not config.get("model_profile_id")
            or not config.get("model_version")
            or dataset.skill != "TRANSCRIPTION"
            and not config.get("prompt_version_id")
        ):
            _conflict("EVALUATION_COMPARISON_MODEL_PROMPT_PINS_REQUIRED")
        pins[side + "_model_version_id"] = (
            str(config["model_profile_id"]) + ":" + str(config["model_version"])
        )
        pins[side + "_prompt_version_id"] = config["prompt_version_id"] or "NOT_APPLICABLE_ASR"
    if payload.comparison_kind == "VERSION_A_VS_B":
        a, b = ai_runs["left"].configuration, ai_runs["right"].configuration
        if a["metric_configuration_checksum"] != b["metric_configuration_checksum"] or all(
            a.get(key) == b.get(key)
            for key in ("model_profile_id", "model_version", "prompt_version_id")
        ):
            _conflict("EVALUATION_COMPARISON_DISTINCT_COMPATIBLE_VERSIONS_REQUIRED")
    evidence_class = (
        "SYNTHETIC_TEST"
        if dataset.evidence_class == "SYNTHETIC_TEST"
        or any(side["execution_class"] == "TEST_ONLY" for side in sides.values())
        else "EXPERT_LABELLED"
    )
    if dataset.skill == "TRANSCRIPTION":
        comparison = _transcript_comparison(population, payload, sides, pins, evidence_class)
    else:
        assert population.rubric is not None
        criteria = [
            {
                key: criterion[key]
                for key in ("code", "max_points", "ordinal_scale")
                if key in criterion
            }
            for criterion in population.rubric.criteria
        ]
        comparison = compare_criterion_scores(
            comparison_kind=payload.comparison_kind,
            cases=[case["case_id"] for case in population.cases],
            criteria=criteria,
            left=sides["left"],
            right=sides["right"],
            pins=pins,
            evidence_class=evidence_class,
            independent_human_raters=payload.comparison_kind == "HUMAN_A_VS_HUMAN_B",
        )
    metric_configuration = MetricConfiguration(
        normalization=population.source.body["normalization"]
    ).model_dump(mode="json")
    configuration = {
        "execution_adapter": ADAPTER,
        "evaluation_method_version": METHOD_VERSION,
        "implementation_version": METHOD_VERSION,
        "dataset_split": population.source.split,
        "metric_configuration": metric_configuration,
        "metric_configuration_checksum": canonical_checksum(metric_configuration),
        "comparison_kind": payload.comparison_kind,
        "request": payload.model_dump(mode="json"),
        "pins": pins,
        "side_receipts": receipts,
        "empirical_thresholds": "UNCONFIGURED",
        "production_activation": False,
    }
    row = EvaluationRun(
        operation_id=payload.operation_id,
        dataset_version_id=dataset.id,
        dataset_checksum=dataset.checksum,
        request_checksum=request_checksum,
        configuration=configuration,
        configuration_checksum=canonical_checksum(configuration),
        metrics={},
        limitations=comparison["limitations"],
        evidence_class=evidence_class,
        execution_class="TEST_ONLY"
        if evidence_class == "SYNTHETIC_TEST"
        else "REAL_PROVIDER"
        if ai_runs
        else "HUMAN_REFERENCE",
        status="RUNNING",
        hard_gate="PENDING",
        case_count=len(population.cases),
        failure_count=0,
        created_by=trusted.user_id,
        created_at=foundation.utcnow(),
    )
    session.add(row)
    session.flush()
    result_rows, failure_count = [], 0
    for index, case in enumerate(population.cases):
        deltas, missing = [], []
        for criterion in comparison["criteria"]:
            pair = next(
                (
                    difference
                    for difference in criterion["score_differences"]
                    if difference["case_id"] == case["case_id"]
                ),
                None,
            )
            if pair is None:
                missing.append(criterion["criterion_code"])
            else:
                deltas.append({"criterion_code": criterion["criterion_code"], **pair})
        result = {
            "case_id": case["case_id"],
            "kind": "CALIBRATION",
            "method_version": METHOD_VERSION,
            "evidence_class": evidence_class,
            "checks": {
                "exact_provenance": "PASS",
                "reference_coverage": "FAIL" if missing else "PASS",
            },
            "metrics": {},
            "criterion_comparisons": deltas,
            "failure_categories": ["MISSING_PAIRED_REFERENCE"] if missing else [],
            "hard_fail": bool(missing),
            "quality_claim": "NOT_ESTABLISHED",
        }
        if dataset.skill == "TRANSCRIPTION":
            result = copy.deepcopy(comparison["case_measurements"][index])
            result["metric_method_version"] = result["method_version"]
            result["method_version"] = METHOD_VERSION
            result["quality_claim"] = "NOT_ESTABLISHED"
        if index == 0:
            result["calibration_summary"] = comparison
        data = {
            "input_checksum": canonical_checksum(case),
            "observation_checksum": canonical_checksum(
                {side: scores["transcripts"][case["case_id"]] for side, scores in sides.items()}
            )
            if dataset.skill == "TRANSCRIPTION"
            else canonical_checksum(deltas),
            "execution_reference": {"adapter": ADAPTER, "side_receipts": receipts},
            "result": result,
        }
        item = EvaluationCaseResult(
            run_id=row.id,
            case_id=case["case_id"],
            **data,
            checksum=canonical_checksum(data),
            created_at=foundation.utcnow(),
        )
        session.add(item)
        result_rows.append(item)
        failure_count += bool(result["hard_fail"])
    session.flush()
    row.metrics, row.failure_count = _criterion_metrics(comparison), failure_count
    row.hard_gate = "FAIL" if failure_count else "PASS"
    row.status, row.completed_at = "COMPLETED", foundation.utcnow()
    row.checksum = foundation._run_checksum(row, result_rows)
    session.flush()
    foundation._audit(session, trusted, "calibration_completed", row)
    return row
