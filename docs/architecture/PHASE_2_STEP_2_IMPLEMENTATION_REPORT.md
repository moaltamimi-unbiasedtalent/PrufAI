# Phase 2 / Step 2 Implementation Report

## Executive Summary

This step adds a provenance-preserving retrieval foundation over approved
standards: deterministic page-local chunks, versioned index profiles, durable
index jobs, PostgreSQL full-text search, pgvector embeddings, typed scope,
provider/assessment isolation, historical retrieval, and citation-ready hybrid
results. It does not add answer generation, agents, Standard Packs, or UI.

## Schema

Migration `0005_knowledge_retrieval` enables `vector` and creates
`knowledge_chunks`, `knowledge_index_profiles`, `knowledge_chunk_embeddings`,
and `knowledge_index_jobs`, with provenance, uniqueness, state, FTS, and vector
metadata constraints.

## Chunking and provenance

`prufai_page_chunker` 1.0 performs deterministic paragraph segmentation within a
single page. Source text is not translated, summarized, corrected, or rewritten.
Each chunk retains page/version links, offsets, hash, token count, chunker
configuration, and the original page provenance chain.

## Embedding profiles and indexing

Profiles capture chunker, embedding provider/model/dimension, metric, and active
state. The MVP dimension is 384 with cosine distance. Index jobs transition
RUNNING to SUCCEEDED/READY only after chunks and vectors commit together;
failures are FAILED and retryable. Reindexing replaces the same profile's
representation for a version while a new profile can coexist with old vectors.

## Retrieval

Lexical PostgreSQL retrieval uses `simple` `tsvector`/`tsquery` ranking and a GIN
index. Vector retrieval uses pgvector cosine distance. SQLite unit tests use a
deterministic local fallback; production PostgreSQL executes the database
operators. Hybrid retrieval deduplicates channels and applies deterministic RRF
while preserving lexical/vector ranks, scores, and match flags.

## Scope and safety

`RetrievalScope` explicitly filters providers, authorities, sources, assessment
family, exam family, language, CEFR, skill, document type, shared authorities,
and historical date. Provider widening is disabled. LANGUAGE and
INTEGRATION_KNOWLEDGE remain isolated; LiD is integration knowledge with NULL
CEFR. Only approved/current READY content is used for normal retrieval;
approved/superseded effective content is available for as-of-date retrieval.

## Citation contract and authorization

Results include authority, source, version, checksum, page, effective dates,
legal summary, chunker/profile identity, source URL, and channel signals. They
never expose private object-storage URLs or secrets. Indexing and retrieval use
platform standards permissions and administrative indexing events use the
existing audit service without chunk text.

## Tests and CI

The migration revision guard remains active and verifies IDs <=32 characters,
uniqueness, and parent resolution. CI now uses `pgvector/pgvector:pg16`, runs
the migration, confirms the vector extension, and runs the Python integration
suite. Local unit validation covers chunk determinism, embeddings, scope safety,
and existing standards behavior; live PostgreSQL/pgvector execution remains a
CI gate where local infrastructure is unavailable.

## Dependencies and security findings

Added `pgvector` 0.4.x and retained the existing boto3 embedding/storage stack.
No secrets or provider documents are committed. The existing Next.js/PostCSS
advisory remains documented; current registry audit refresh is environment
dependent.

## Known limitations and Step 3 recommendations

The current vector fallback is intentionally deterministic for SQLite tests and
must not be used in production. Query telemetry is represented architecturally
but should be connected to the deployment's metrics/tracing backend. Step 3 may
evaluate ANN indexing, richer section extraction, and controlled retrieval
consumers after CI validates PostgreSQL/pgvector behavior.
