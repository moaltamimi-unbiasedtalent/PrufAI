from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence


@dataclass(frozen=True)
class RetrievalCase:
    case_id: str
    query: str
    expected_ids: frozenset[str]
    forbidden_authorities: frozenset[str] = frozenset()
    as_of_date: Optional[str] = None
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class QualityGate:
    provider_contamination_rate: float | None = None
    assessment_contamination_rate: float | None = None
    scope_compliance_rate: float | None = None
    citation_validity_rate: float | None = None
    historical_version_accuracy: float | None = None

    def passed(self) -> bool:
        return (
            self.provider_contamination_rate == 0
            and self.assessment_contamination_rate == 0
            and self.scope_compliance_rate == 1
            and self.citation_validity_rate == 1
            and self.historical_version_accuracy == 1
        )


def recall_at_k(expected: Iterable[str], retrieved: Sequence[str], k: int) -> float:
    expected_set = set(expected)
    return (
        len(expected_set.intersection(retrieved[:k])) / len(expected_set) if expected_set else 1.0
    )


def precision_at_k(expected: Iterable[str], retrieved: Sequence[str], k: int) -> float:
    if k <= 0:
        return 0.0
    return (
        len(set(expected).intersection(retrieved[:k])) / min(k, len(retrieved))
        if retrieved
        else 0.0
    )


def mean_reciprocal_rank(expected: Iterable[str], retrieved: Sequence[str]) -> float:
    expected_set = set(expected)
    for index, item in enumerate(retrieved, 1):
        if item in expected_set:
            return 1.0 / index
    return 0.0


def ndcg_at_k(expected: Iterable[str], retrieved: Sequence[str], k: int) -> float:
    expected_set = set(expected)
    gains = [1.0 if item in expected_set else 0.0 for item in retrieved[:k]]
    dcg = sum(gain / math.log2(index + 2) for index, gain in enumerate(gains))
    ideal = sum(1.0 / math.log2(index + 2) for index in range(min(k, len(expected_set))))
    return dcg / ideal if ideal else 1.0


def provider_contamination(
    forbidden_authorities: Iterable[str], result_authorities: Iterable[str]
) -> float:
    forbidden = set(forbidden_authorities)
    results = list(result_authorities)
    return sum(authority in forbidden for authority in results) / len(results) if results else 0.0


def scope_compliance(expected_ids: Iterable[str], retrieved: Iterable[str]) -> float:
    expected = set(expected_ids)
    values = list(retrieved)
    return sum(value in expected for value in values) / len(values) if values else 1.0
