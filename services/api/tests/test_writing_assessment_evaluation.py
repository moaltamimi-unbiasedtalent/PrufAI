import pytest

from app.writing_assessment.evaluation import (
    WRITING_WORKFLOW_STAGES,
    compare_independent_proposals,
    evaluate_golden_dataset,
    validate_stage_output,
)


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
