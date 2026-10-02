# Retrieval Baseline V1

Status: Synthetic regression baseline

This baseline uses original synthetic fixtures labelled `TEST FIXTURE — NOT
OFFICIAL MATERIAL`. It exists for deterministic regression and safety checks,
not production-accuracy or marketing claims.

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
