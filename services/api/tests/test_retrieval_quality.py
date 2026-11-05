import uuid
from datetime import datetime, timezone

from app.evaluation.retrieval import (
    QualityGate,
    mean_reciprocal_rank,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)
from app.evaluation.runner import deterministic_synthetic_baseline
from app.knowledge.citations import CitationBuilder
from app.knowledge.reranking import DeterministicTestReranker, RerankingProfile
from app.services.retrieval import CitationReadyResult


def _result(text: str, page: int = 2) -> CitationReadyResult:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return CitationReadyResult(
        chunk_id=uuid.uuid4(),
        text=text,
        authority_id=uuid.uuid4(),
        authority_code="TELC",
        authority_name="Synthetic Authority",
        source_id=uuid.uuid4(),
        source_title="Synthetic Standard",
        document_version_id=uuid.uuid4(),
        version_label="1",
        document_checksum="a" * 64,
        page_number=page,
        section_title=None,
        assessment_family="LANGUAGE",
        exam_family=None,
        language="de",
        cefr_level="B1",
        skill=None,
        publication_date=now,
        effective_from=now,
        effective_until=None,
        source_url=None,
        legal_summary={"redistribution_allowed": None},
        chunker_name="test",
        chunker_version="1",
    )


def test_reranking_preserves_candidates_and_changes_only_order() -> None:
    candidates = [_result("unrelated"), _result("query query relevant")]
    profile = RerankingProfile("test", "test", "1", "test-deterministic", "v1", final_limit=2)
    ranked = DeterministicTestReranker().rerank("query", candidates, profile)
    assert {item.chunk_id for item in ranked} == {item.chunk_id for item in candidates}
    assert ranked[0].rerank_rank == 1
    assert ranked[0].rerank_score is not None


def test_citation_is_stable_and_rights_aware() -> None:
    result = _result("synthetic")
    citation = CitationBuilder().build(result)
    assert citation.citation_id == CitationBuilder().build(result).citation_id
    assert citation.page_number == 2
    assert citation.redistribution_status == "UNKNOWN"
    assert citation.text_display_allowed is False
    assert "p. 2" in citation.display_text


def test_retrieval_metrics_and_quality_gate() -> None:
    assert recall_at_k({"a", "b"}, ["a", "c"], 2) == 0.5
    assert precision_at_k({"a"}, ["a", "c"], 2) == 0.5
    assert mean_reciprocal_rank({"b"}, ["a", "b"]) == 0.5
    assert ndcg_at_k({"a"}, ["a"], 1) == 1.0
    assert not QualityGate().passed()
    assert QualityGate(0.0, 0.0, 1.0, 1.0, 1.0).passed()
    assert not QualityGate(provider_contamination_rate=0.1).passed()
    assert not QualityGate(assessment_contamination_rate=0.1).passed()
    assert not QualityGate(scope_compliance_rate=0.99).passed()
    assert not QualityGate(citation_validity_rate=0.99).passed()
    assert not QualityGate(historical_version_accuracy=0.99).passed()
    assert recall_at_k(set(), [], 5) == 1.0


def test_synthetic_baseline_is_reproducible() -> None:
    baseline = deterministic_synthetic_baseline()
    assert baseline.before.recall_at_5 == 1.0
    assert baseline.after.recall_at_5 == 1.0
    assert baseline.before.mrr == 0.4583333333333333
    assert baseline.after.mrr == 0.9166666666666666
    assert baseline.before.ndcg_at_5 == 0.6616856074405029
    assert baseline.after.ndcg_at_5 == 1.0
    assert baseline.after.gate == "NOT_AVAILABLE"
    assert baseline.after.citation_validity_rate is None
    assert baseline.after.historical_version_accuracy is None
