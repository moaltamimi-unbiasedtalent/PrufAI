from __future__ import annotations

from decimal import Decimal
from typing import Any


def evaluate_golden_dataset(
    golden: list[dict[str, Any]], predictions: list[dict[str, Any]]
) -> dict[str, Any]:
    """Return bounded engineering metrics for synthetic, non-candidate fixtures."""
    by_id = {str(item["id"]): item for item in predictions}
    schema_valid = 0
    criterion_coverage = 0
    evidence_validity = 0
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
        if all(isinstance(item.get("evidence"), list) for item in actual.get("criteria", [])):
            evidence_validity += 1
        absolute_distance += abs(
            Decimal(str(actual.get("score", 0))) - Decimal(str(expected["score"]))
        )
    return {
        "cases": cases,
        "schema_valid_rate": schema_valid / cases if cases else 0.0,
        "criterion_coverage_rate": criterion_coverage / cases if cases else 0.0,
        "evidence_validity_rate": evidence_validity / cases if cases else 0.0,
        "mean_absolute_score_distance": str(absolute_distance / cases) if cases else "0",
        "examiner_equivalence_claim": False,
    }
