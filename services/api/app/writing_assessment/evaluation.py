from __future__ import annotations

from decimal import Decimal
from typing import Any

WRITING_WORKFLOW_STAGES = (
    "prepare_input",
    "evidence_analysis",
    "criterion_evaluation",
    "consistency_review",
    "deterministic_validation",
    "review_routing",
    "human_review_request",
)


def validate_stage_output(
    *,
    criteria: list[dict[str, Any]],
    rubric_criteria: list[dict[str, Any]],
    performance_levels: list[dict[str, Any]],
    response_text: str,
) -> None:
    """Strictly validate bounded AI stage output before persistence."""
    expected = {str(item["code"]): item for item in rubric_criteria}
    levels = {str(item["code"]) for item in performance_levels}
    if {str(item.get("criterion_code")) for item in criteria} != set(expected):
        raise ValueError("criterion coverage does not match rubric")
    for item in criteria:
        code = str(item.get("criterion_code"))
        if str(item.get("proposed_level")) not in levels:
            raise ValueError("unknown performance level")
        try:
            points = Decimal(str(item.get("proposed_points")))
        except Exception as exc:  # noqa: BLE001
            raise ValueError("invalid Decimal points") from exc
        if points < 0 or points > Decimal(str(expected[code]["max_points"])):
            raise ValueError("points exceed rubric bounds")
        for ref in item.get("evidence", []):
            if not isinstance(ref, dict):
                raise ValueError("evidence reference must be structured")
            start, end = ref.get("start"), ref.get("end")
            if (
                not isinstance(start, int)
                or not isinstance(end, int)
                or start < 0
                or end < start
                or end > len(response_text)
            ):
                raise ValueError("evidence reference is outside response")


def compare_independent_proposals(
    first: list[dict[str, Any]], second: list[dict[str, Any]]
) -> dict[str, Any]:
    """Return disagreement metadata without averaging or selecting a winner."""
    left = {str(item["criterion_code"]): item for item in first}
    right = {str(item["criterion_code"]): item for item in second}
    disagreements: dict[str, Any] = {}
    for code in sorted(set(left) | set(right)):
        if code not in left or code not in right:
            disagreements[code] = {"type": "MISSING", "priority": "BLOCKING_ISSUE"}
            continue
        if left[code].get("proposed_level") != right[code].get("proposed_level") or str(
            left[code].get("proposed_points")
        ) != str(right[code].get("proposed_points")):
            disagreements[code] = {
                "first": left[code],
                "second": right[code],
                "priority": "BLOCKING_ISSUE",
            }
    return disagreements


def evaluate_golden_dataset(
    golden: list[dict[str, Any]], predictions: list[dict[str, Any]]
) -> dict[str, Any]:
    """Return bounded engineering metrics for synthetic, non-candidate fixtures."""
    by_id = {str(item["id"]): item for item in predictions}
    schema_valid = 0
    criterion_coverage = 0
    evidence_validity = 0
    exact_label = 0
    within_one = 0
    review_triggered = 0
    adversarial_pass = 0
    absolute_distance = Decimal("0")
    cases = 0
    for expected in golden:
        actual = by_id.get(str(expected["id"]))
        if actual is None:
            continue
        cases += 1
        if isinstance(actual.get("criteria"), list) and isinstance(
            actual.get("score"), (str, int, float)
        ):
            schema_valid += 1
        expected_codes = {str(code) for code in expected.get("criterion_codes", [])}
        actual_codes = {str(item.get("criterion_code")) for item in actual.get("criteria", [])}
        if expected_codes == actual_codes:
            criterion_coverage += 1
        expected_levels = {str(k): str(v) for k, v in expected.get("labels", {}).items()}
        actual_levels = {
            str(item.get("criterion_code")): str(item.get("proposed_level"))
            for item in actual.get("criteria", [])
        }
        if expected_levels and expected_levels == actual_levels:
            exact_label += 1
        if expected_levels and all(
            abs(int(actual_levels.get(code, "0").split("_")[-1]) - int(level.split("_")[-1])) <= 1
            for code, level in expected_levels.items()
            if code in actual_levels
        ):
            within_one += 1
        if all(isinstance(item.get("evidence"), list) for item in actual.get("criteria", [])):
            evidence_validity += 1
        if bool(actual.get("review_required")) == bool(expected.get("review_required")):
            review_triggered += 1
        if expected.get("adversarial") and actual.get("policy_safe") is True:
            adversarial_pass += 1
        absolute_distance += abs(
            Decimal(str(actual.get("score", 0))) - Decimal(str(expected["score"]))
        )
    return {
        "cases": cases,
        "schema_valid_rate": schema_valid / cases if cases else 0.0,
        "criterion_coverage_rate": criterion_coverage / cases if cases else 0.0,
        "evidence_validity_rate": evidence_validity / cases if cases else 0.0,
        "exact_label_agreement_rate": exact_label / cases if cases else 0.0,
        "within_one_level_rate": within_one / cases if cases else 0.0,
        "mean_absolute_score_distance": str(absolute_distance / cases) if cases else "0",
        "aggregate_point_difference": str(absolute_distance),
        "designed_review_trigger_coverage": review_triggered / cases if cases else 0.0,
        "adversarial_policy_pass_rate": adversarial_pass / cases if cases else 0.0,
        "examiner_equivalence_claim": False,
    }
