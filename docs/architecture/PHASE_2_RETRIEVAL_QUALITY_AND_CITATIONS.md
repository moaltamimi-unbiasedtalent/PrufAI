# Phase 2 / Step 3: Retrieval Quality and Citations

Status: Accepted for MVP baseline

```text
validated scope -> hybrid candidates -> deterministic/API reranker
       -> evidence result -> citation builder -> evaluation metrics -> quality gate
```

This step evaluates retrieval and evidence integrity. It does not generate
answers, run agents, or publish Standard Packs.

## Reranking and evidence

Reranking is constrained to the already scope-filtered candidate set. Profiles
capture provider/model/version, candidate and final limits, and configuration.
The deterministic reranker is TEST/DEV only; production requires the configured
HTTP-compatible reranking adapter and failures are controlled. Original lexical,
vector, RRF, and rerank signals remain visible.

`CitationBuilder` creates stable evidence IDs from document version, page, and
chunk identifiers. Citations omit missing sections/URLs rather than inventing
them. Rights remain explicit: unknown rights permit citation metadata but do not
permit broad text display or redistribution.

## Standards Explorer

Platform-authorized read APIs browse authorities, sources, versions, pages, and
provenance with pagination. Version history remains visible for superseded
records. Pages expose extraction metadata rather than a bulk source-content
export endpoint.

## Evaluation

The synthetic golden dataset is versioned and contains provider, shared-standard,
CEFR/LiD, current/historical, forbidden-provider, and no-evidence cases. It is
original test content, not official provider material. Deterministic metrics are
Recall@K, Precision@K, MRR, nDCG, provider/assessment contamination, scope
compliance, citation validity, and historical-version accuracy. Hard gates are
zero provider/assessment contamination and 100% scope, citation, and historical
accuracy for synthetic cases.

RAGAS-compatible records can export retrieval contexts and references. Answer
faithfulness/relevance are not evaluated because no generated answers exist.

## Security and limitations

Platform RBAC remains mandatory. Queries are represented in telemetry by a hash
and length; document text, credentials, and private object URLs are not logged
or exposed. PostgreSQL/pgvector and external reranker execution remain CI or
deployment gates. The baseline is a regression/safety harness, not a production
accuracy claim.
