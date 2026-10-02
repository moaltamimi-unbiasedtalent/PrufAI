# Phase 2 / Step 2: Hybrid Knowledge Retrieval

Status: Accepted for MVP baseline

```text
approved pages -> deterministic page-local chunks -> index profile/job
                                      |                 |
                                      +-> PostgreSQL FTS + pgvector
                                                        |
                              validated RetrievalScope -> RRF -> citations
```

The retrieval layer is not an answer-generation or agent system. It provides
approved, provenance-preserving candidates for later RAG and Standard Pack
work.

## Chunking and indexing

`prufai_page_chunker` version `1.0` segments each page independently, preserving
source text and recording hashes, character offsets, token counts, and page
links. Chunk identity and content hashes make repeat indexing idempotent for a
profile. Index jobs are durable and only approved or historically superseded
versions may become READY. Failures remain FAILED and never become retrievable.

## Embeddings and pgvector

The MVP uses fixed 384-dimensional cosine vectors. `EmbeddingProvider` isolates
providers. The deterministic provider is TEST/DEV only; production requires
the configured OpenAI-compatible adapter, API key, model, timeout, batch input,
and bounded retries. Profiles preserve chunker/model/dimension/metric metadata,
so a new model creates a new representation rather than overwriting old
vectors. PostgreSQL loads `vector`; lexical search uses the `simple` text
configuration and a GIN expression index. Vector retrieval uses exact cosine
distance for correctness; ANN can be added after scale evidence.

## Scope and isolation

`RetrievalScope` is typed and explicit. Provider codes, authority codes, source,
assessment family, exam family, language, CEFR, skill, document type, and
as-of-date filters are applied before ranking. Provider widening never happens
automatically. Shared authorities such as CEFR must be explicitly included.
LANGUAGE and INTEGRATION_KNOWLEDGE remain isolated; LiD is integration
knowledge with `LEBEN_IN_DEUTSCHLAND` and NULL CEFR.

Current queries use effective approved READY content. Historical queries use
approved/superseded versions whose effective interval matches the requested
date. Draft, ingesting, ingested, review-required, rejected, and retired
content is excluded.

## Hybrid and citations

Lexical and vector channels retain their own rank and score. Hybrid search uses
deterministic Reciprocal Rank Fusion with deduplication and matched-channel
flags. Results include authority/source/version/page/checksum/effective dates,
legal summary, chunker, embedding profile, and source URL; private object
storage URLs and secrets are never exposed.

## Authorization and observability

Indexing and retrieval remain platform-controlled through existing standards
permissions. Administrative indexing events use AuditService without chunk text.
Operational retrieval telemetry should record request IDs, scope summaries,
durations, candidate counts, and profile identifiers while avoiding raw query or
document content.

## Known limitations

Live PostgreSQL/pgvector execution depends on CI or configured infrastructure.
The production embedding adapter is intentionally not exercised by ordinary
unit tests. No LLM reranking, answer synthesis, Standard Packs, or UI is part
of this step.
