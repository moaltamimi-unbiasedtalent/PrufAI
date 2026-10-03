from app.writing_assessment.evaluation import evaluate_golden_dataset


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
