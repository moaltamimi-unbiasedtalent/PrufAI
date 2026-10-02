from __future__ import annotations

from typing import Any, Iterable


def to_ragas_compatible_records(
    query: str,
    retrieved_contexts: Iterable[str],
    reference_contexts: Iterable[str],
) -> dict[str, Any]:
    """Export retrieval-only records; answer-generation metrics are not fabricated."""
    return {
        "user_input": query,
        "retrieved_contexts": list(retrieved_contexts),
        "reference_contexts": list(reference_contexts),
        "metadata": {"evaluation_mode": "retrieval_only", "ragas_adapter": "compatible-v1"},
    }
