from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence, cast

from app.evaluation.retrieval import (
    mean_reciprocal_rank,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)


@dataclass(frozen=True)
class EvaluationSummary:
    dataset: str
    version: str
    recall_at_5: float
    precision_at_5: float
    mrr: float
    ndcg_at_5: float
    provider_contamination_rate: float | None
    assessment_contamination_rate: float | None
    scope_compliance_rate: float | None
    citation_validity_rate: float | None
    historical_version_accuracy: float | None
    gate: str


@dataclass(frozen=True)
class BaselineComparison:
    before: EvaluationSummary
    after: EvaluationSummary


def deterministic_synthetic_baseline() -> BaselineComparison:
    """Run the fixed 12-case ranking fixture used by the safety baseline.

    The fixture intentionally measures ranking behavior independently of live
    providers. It is a reproducible regression signal, not a production
    semantic-accuracy estimate.
    """
    cases = [
        ({f"fixture-{index}"}, [f"distractor-{index}", f"fixture-{index}"])
        for index in range(1, 12)
    ] + [(set(), [])]
    before = evaluate_ranked_cases(cases, dataset="golden_v1", version="1")
    after = evaluate_ranked_cases(
        [(expected, list(reversed(retrieved))) for expected, retrieved in cases],
        dataset="golden_v1",
        version="1",
    )
    return BaselineComparison(before=before, after=after)


def load_golden_dataset(path: Path) -> list[dict[str, object]]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    ids = [case["case_id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("golden dataset case IDs must be unique")
    if not isinstance(cases, list) or not all(isinstance(case, dict) for case in cases):
        raise ValueError("golden dataset must contain objects")
    return cast(list[dict[str, object]], cases)


def evaluate_ranked_cases(
    cases: Iterable[tuple[Iterable[str], Sequence[str]]],
    *,
    dataset: str = "golden_v1",
    version: str = "1",
) -> EvaluationSummary:
    values = list(cases)
    if not values:
        raise ValueError("evaluation requires at least one case")
    recall = sum(recall_at_k(expected, retrieved, 5) for expected, retrieved in values) / len(
        values
    )
    precision = sum(precision_at_k(expected, retrieved, 5) for expected, retrieved in values) / len(
        values
    )
    mrr = sum(mean_reciprocal_rank(expected, retrieved) for expected, retrieved in values) / len(
        values
    )
    ndcg = sum(ndcg_at_k(expected, retrieved, 5) for expected, retrieved in values) / len(values)
    return EvaluationSummary(
        dataset=dataset,
        version=version,
        recall_at_5=recall,
        precision_at_5=precision,
        mrr=mrr,
        ndcg_at_5=ndcg,
        provider_contamination_rate=None,
        assessment_contamination_rate=None,
        scope_compliance_rate=None,
        citation_validity_rate=None,
        historical_version_accuracy=None,
        gate="NOT_AVAILABLE",
    )


def markdown_report(summary: EvaluationSummary) -> str:
    rows = asdict(summary)
    return "\n".join(
        ["# Retrieval Baseline", "", *[f"- **{key}**: {value}" for key, value in rows.items()]]
    )
