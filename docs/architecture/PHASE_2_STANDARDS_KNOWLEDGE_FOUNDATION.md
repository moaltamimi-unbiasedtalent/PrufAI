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
assessment family, document type, language, CEFR level, skill, scope, and
canonical URL. The implemented families are LiD, language, provider, and
curriculum; LiD is represented as a first-class assessment family with
`provider_code`, `exam_family`, `cefr_level`, and `skill` fields available for
future structured catalogues.

## Versions, dates, and legal metadata

Every source has immutable-at-approval document versions. A version records its
publication/effective interval, source URL, superseded predecessor, and
content checksum. The database rejects an end date that is not after its start.
Approval is explicit and a later approved version can supersede an older one;
approved records cannot receive new files. Each version receives a legal
metadata row whose unknown values remain explicit rather than inferred.

## Storage and ingestion

The service depends on an `ObjectStorage` protocol. The current local adapter
is an in-memory implementation for tests and development; production must bind
an encrypted, access-controlled object store. Uploads are basename-sanitised,
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
