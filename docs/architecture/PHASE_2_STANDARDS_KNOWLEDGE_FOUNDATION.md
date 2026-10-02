# Phase 2 / Step 1: Standards and Knowledge Foundation

## Scope

This step establishes the platform-owned standards registry and a fail-closed
knowledge ingestion boundary. It does not implement RAG retrieval, scoring,
Standard Packs, provider integrations, or tenant-authored knowledge.

```text
Platform principal
       |
       v
Authority -> Source -> DocumentVersion -> ObjectStorage
                                      |
                                      v
                              IngestionJob -> Page provenance
                                      |
                                      v
                         ReviewRequired -> Approved/Superseded
```

## Registry and taxonomy

Authorities are controlled identities (for example Council of Europe, BAMF,
Goethe, telc, and PrüfAI). A source identifies a canonical standard, exam,
provider, or curriculum within one authority. Source records carry the
root assessment family (`LANGUAGE` or `INTEGRATION_KNOWLEDGE`), document type,
language, CEFR level, skill, scope, and canonical URL. Provider,
framework/curriculum, exam family, language, skill, and CEFR are separate
metadata dimensions. LiD uses `INTEGRATION_KNOWLEDGE`,
`exam_family=LEBEN_IN_DEUTSCHLAND`, and a NULL CEFR level.

## Versions, dates, and legal metadata

Every source has immutable-at-approval document versions. A version records its
publication/effective interval, source URL, superseded predecessor, and
content checksum. The database rejects an end date that is not after its start.
Approval is explicit and a later approved version can supersede an older one;
approved records cannot receive new files. Each version receives a legal
metadata row whose unknown values remain explicit rather than inferred.

## Current and historical resolution

`get_current_approved_versions` returns approved, currently effective versions
and excludes retired/superseded content and versions replaced by an effective
successor. `get_approved_versions_as_of` resolves approved or superseded
versions using approval state and effective intervals. A superseded version can
remain valid historical evidence while never being current. Draft, ingesting,
ingested, review-required, rejected, and retired content is never
retrieval-eligible.

## Storage and ingestion

The service depends on an `ObjectStorage` protocol. Development/tests may use
an explicit in-memory adapter; production requires the S3-compatible adapter
with a private bucket, including MinIO-compatible endpoints. There is no
production memory fallback. Uploads are basename-sanitised,
size-limited to 25 MiB, restricted to PDF/plain-text/Markdown, and SHA-256
hashed before registration. Parsers are selected by MIME type and emit pages
with normalized text, content hashes, extraction status, parser identity, and
character counts. No OCR or external download is performed in this step.

## Approval boundary and audit

Only platform-scoped principals with the standards permissions can create or
review records. Ingestion ends in `REVIEW_REQUIRED`; only a reviewer can mark a
version approved. Audit events cover registry creation, upload registration,
ingestion start/success/failure, approval, rejection, supersession, and legal
metadata changes. Future RAG and Standard Pack work must consume only approved,
effective, non-superseded versions and retain page-level provenance.

## Known limitations

The local API adapter is process-local and is not a production persistence
implementation. PostgreSQL/MinIO end-to-end execution and external legal-rights
verification require CI or configured infrastructure. Seeded authorities are
registry identities only; no external standard is copied into the repository.
