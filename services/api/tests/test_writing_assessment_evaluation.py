import pytest

from app.writing_assessment.evaluation import (
    WRITING_WORKFLOW_STAGES,
    bounded_structured_retry,
    compare_independent_proposals,
    evaluate_golden_dataset,
    validate_stage_output,
)


def test_bounded_retry_recovers_and_exhausts_without_coercion():
    calls: list[int] = []

    def recovers(attempt: int) -> dict[str, int]:
        calls.append(attempt)
        if attempt == 1:
            raise ValueError("malformed")
        return {"attempt": attempt}

    assert bounded_structured_retry(recovers) == ({"attempt": 2}, 2)
    assert calls == [1, 2]
    with pytest.raises(ValueError, match="INVALID_MODEL_OUTPUT"):
        bounded_structured_retry(lambda _attempt: (_ for _ in ()).throw(ValueError("bad")))


def test_writing_workflow_has_bounded_stages_and_preserves_disagreement():
    assert WRITING_WORKFLOW_STAGES == (
        "prepare_input",
        "evidence_analysis",
        "criterion_evaluation",
        "consistency_review",
        "deterministic_validation",
        "review_routing",
        "human_review_request",
    )
    disagreement = compare_independent_proposals(
        [{"criterion_code": "C1", "proposed_level": "LEVEL_2", "proposed_points": "2"}],
        [{"criterion_code": "C1", "proposed_level": "LEVEL_4", "proposed_points": "4"}],
    )
    assert disagreement["C1"]["priority"] == "BLOCKING_ISSUE"


def test_stage_output_rejects_unknown_and_out_of_bounds_values():
    with pytest.raises(ValueError):
        validate_stage_output(
            criteria=[
                {
                    "criterion_code": "C9",
                    "proposed_level": "LEVEL_1",
                    "proposed_points": "1",
                    "evidence": [],
                }
            ],
            rubric_criteria=[{"code": "C1", "max_points": "4"}],
            performance_levels=[{"code": "LEVEL_1"}],
            response_text="text",
        )


def test_engineering_golden_gates_cover_adversarial_and_boundary_cases():
    cases = [
        {"id": "strong", "criterion_codes": ["C1"], "score": "4", "labels": {"C1": "LEVEL_4"}},
        {"id": "weak", "criterion_codes": ["C1"], "score": "1", "labels": {"C1": "LEVEL_1"}},
        {
            "id": "off-topic",
            "criterion_codes": ["C1"],
            "score": "0",
            "labels": {"C1": "LEVEL_0"},
            "review_required": True,
        },
        {
            "id": "very-short",
            "criterion_codes": ["C1"],
            "score": "0",
            "labels": {"C1": "LEVEL_0"},
            "review_required": True,
        },
        {
            "id": "mixed-quality",
            "criterion_codes": ["C1"],
            "score": "2",
            "labels": {"C1": "LEVEL_2"},
        },
        {
            "id": "borderline",
            "criterion_codes": ["C1"],
            "score": "2",
            "labels": {"C1": "LEVEL_2"},
            "review_required": True,
        },
        {
            "id": "injection",
            "criterion_codes": ["C1"],
            "score": "1",
            "labels": {"C1": "LEVEL_1"},
            "adversarial": True,
            "policy_safe": True,
            "review_required": True,
        },
        {
            "id": "invalid-evidence",
            "criterion_codes": ["C1"],
            "score": "1",
            "labels": {"C1": "LEVEL_1"},
            "review_required": True,
        },
        {
            "id": "malformed",
            "criterion_codes": ["C1"],
            "score": "1",
            "labels": {"C1": "LEVEL_1"},
            "review_required": True,
        },
    ]
    predictions = [
        {
            "id": item["id"],
            "criteria": [
                {"criterion_code": "C1", "proposed_level": item["labels"]["C1"], "evidence": []}
            ],
            "score": item["score"],
            "review_required": item.get("review_required", False),
            "policy_safe": item.get("policy_safe", True),
        }
        for item in cases
    ]
    metrics = evaluate_golden_dataset(cases, predictions)
    assert metrics["schema_valid_rate"] == 1.0
    assert metrics["criterion_coverage_rate"] == 1.0
    assert metrics["evidence_validity_rate"] == 1.0
    assert metrics["adversarial_policy_pass_rate"] == 1.0


@pytest.mark.parametrize(
    "entry",
    [
        {
            "criterion_code": "C1",
            "proposed_level": "LEVEL_1",
            "proposed_points": "NaN",
            "evidence": [],
        },
        {
            "criterion_code": "C1",
            "proposed_level": "UNKNOWN",
            "proposed_points": "1",
            "evidence": [],
        },
        {
            "criterion_code": "C1",
            "proposed_level": "LEVEL_1",
            "proposed_points": "5",
            "evidence": [],
        },
        {
            "criterion_code": "C9",
            "proposed_level": "LEVEL_1",
            "proposed_points": "1",
            "evidence": [],
        },
        {
            "criterion_code": "C1",
            "proposed_level": "LEVEL_1",
            "proposed_points": "1",
            "evidence": [{"start": 4, "end": 2}],
        },
    ],
)
def test_invalid_model_output_matrix_is_rejected(entry):
    with pytest.raises(ValueError):
        validate_stage_output(
            criteria=[entry],
            rubric_criteria=[{"code": "C1", "max_points": "4"}],
            performance_levels=[{"code": "LEVEL_1"}],
            response_text="abc",
        )


def test_synthetic_golden_metrics_are_schema_bounded_and_non_claiming():
    metrics = evaluate_golden_dataset(
        [{"id": "synthetic-1", "criterion_codes": ["TASK"], "score": "2"}],
        [
            {
                "id": "synthetic-1",
                "score": "3",
                "criteria": [{"criterion_code": "TASK", "evidence": [{"start": 0, "end": 4}]}],
            }
        ],
    )
    assert metrics["cases"] == 1
    assert metrics["schema_valid_rate"] == 1.0
    assert metrics["criterion_coverage_rate"] == 1.0
    assert metrics["evidence_validity_rate"] == 1.0
    assert metrics["mean_absolute_score_distance"] == "1"
    assert metrics["examiner_equivalence_claim"] is False
