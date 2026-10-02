# Phase 2 / Step 3 Implementation Report

## Executive Summary

Added retrieval quality and evidence controls over the merged Phase 2 retrieval
engine: constrained reranking, stable citations, platform Standards Explorer
read APIs, synthetic golden cases, deterministic metrics, quality gates, and a
retrieval-only RAGAS-compatible export.

## Delivered

- Versioned reranker abstraction with deterministic test and production HTTP adapters.
- Citation/evidence contract with stable IDs and rights-aware display policy.
- Paginated authority/source/version/page/provenance read APIs.
- Migration `0006_retrieval_evaluation` for reranking profiles.
- Synthetic golden dataset and deterministic Recall@K, Precision@K, MRR, nDCG,
  isolation, scope, citation, and historical metrics.
- Hard quality-gate model and RAGAS-compatible retrieval record adapter.
- ADR-032 through ADR-036 and baseline evaluation documentation.

## Validation boundary

Local API, mypy, lint, frontend, AI, Alembic, Compose, citation, reranker, and
evaluation tests pass. PostgreSQL/pgvector and production reranker execution
remain CI/deployment gates. No LLM answer-generation or RAG faithfulness claim
is made.

## Final CI and Security Closure

The all-dependency npm audit after the targeted Vitest update reports 0
Critical, 1 High, and 3 Moderate findings. The production-only audit reports
0 Critical, 1 High, and 1 Moderate. Vitest is development-only; its prior
Critical advisory was remediated with the compatible `3.2.7` update, leaving a
Moderate advisory whose fix requires a major Vitest upgrade. PostCSS is a
transitive production dependency of Next.js `15.5.27`; its High findings and
the related Moderate Next.js finding require the deferred Next.js `16.3.8`
major upgrade. No force fix was used.

The Python security audit is not available in the approved environment because
`pip-audit` is not installed. No RAGAS or other new Python runtime dependency
was introduced; the reranker production adapter uses the standard library HTTP
client. RAGAS status remains **compatible adapter only**: retrieval-only
records are exportable, but the package and answer-quality metrics are not
claimed or installed.

The deterministic `golden_v1` baseline (version `1`, 12 synthetic cases) was
executed with deterministic test embeddings and the deterministic test
reranker. Before/after reranking values are:

| Metric | Before | After |
| --- | ---: | ---: |
| Recall@5 | 1.000000 | 1.000000 |
| Precision@5 | 0.458333 | 0.458333 |
| MRR | 0.458333 | 0.916667 |
| nDCG@5 | 0.661686 | 1.000000 |

After-reranking safety metrics are provider contamination `0.000000`,
assessment-family contamination `0.000000`, scope compliance `1.000000`,
citation validity `1.000000`, and historical-version accuracy `1.000000`.
Quality-gate regression tests assert every hard-gate failure mode and confirm
that an expected no-result case passes.

GitHub Actions is configured with pgvector PostgreSQL, health checks,
`DATABASE_URL`, Alembic upgrade, extension verification, explicit database
integration-test invocations, and the deterministic evaluation gate. Remote
execution remains **not verified** in this environment. Local PostgreSQL/
pgvector is unavailable, so the database-backed integration boundary is also
not locally verified.

## Recommendation for next phase

Keep retrieval/evidence contracts stable while a later phase adds controlled
RAG consumers, Standard Packs, and answer-generation evaluation with explicit
human governance.
