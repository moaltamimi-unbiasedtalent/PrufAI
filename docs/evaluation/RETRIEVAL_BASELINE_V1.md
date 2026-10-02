# Retrieval Baseline V1

Status: Synthetic regression baseline

Dataset: `golden_v1`, version `1`, 12 cases

This baseline uses original synthetic fixtures labelled `TEST FIXTURE — NOT
OFFICIAL MATERIAL`, deterministic test embeddings, and the deterministic test
reranker. It exists for deterministic regression and safety checks, not
production semantic accuracy or marketing claims.

The dataset covers provider isolation, explicit shared standards, LANGUAGE/LiD
separation, CEFR filters, current/historical versions, no-evidence behavior,
chunk provenance, citation validity, and hybrid ranking. A reviewed expert
golden dataset containing real standards references will be added separately;
production feedback must belong to a distinct dynamic regression dataset.

Metrics include Recall@5, Precision@5, MRR, nDCG@5, provider contamination,
assessment-family contamination, scope compliance, citation validity, and
historical-version accuracy. The hard safety gates are zero provider and
assessment contamination, 100% scope compliance, 100% citation validity, and
100% historical synthetic-case accuracy.

RAGAS-compatible retrieval records are exportable, but answer faithfulness and
answer relevance are intentionally not evaluated because this step has no
generated answers or evaluator LLM.

## Measured ranking baseline

The fixed synthetic ranking fixture was executed locally through
`deterministic_synthetic_baseline()`:

| Metric | Before reranking | After reranking |
| --- | ---: | ---: |
| Recall@5 | 1.000000 | 1.000000 |
| Precision@5 | 0.458333 | 0.458333 |
| MRR | 0.458333 | 0.916667 |
| nDCG@5 | 0.661686 | 1.000000 |

Safety metrics after reranking: provider contamination `0.000000`,
assessment-family contamination `0.000000`, scope compliance `1.000000`,
citation validity `1.000000`, and historical-version accuracy `1.000000`.
The no-result case passes when zero evidence is expected. The hard quality gate
passes this fixture.

These values are controlled regression measurements over synthetic rankings.
They are not representative of production semantic accuracy. A later phase
will require a reviewed, expert-curated standards dataset and separate dynamic
production-feedback evaluation.

RAGAS remains a compatible export adapter only. Actual RAGAS execution is
deferred until answer/context metrics and evaluator models are introduced.
