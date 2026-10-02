# Phase 2 / Step 1 Implementation Report

## Outcome

Implemented the platform standards/source registry, document-version lifecycle,
legal metadata, controlled upload/checksum boundary, parser abstraction,
page-level provenance, and review/approval workflow on top of the Phase 1
authentication and platform authorization foundation.

## Delivered

- Migration `0004_standards_knowledge` with seven UUID-backed tables,
  foreign keys, uniqueness/index constraints, and effective-date validation.
- Authority/source/document-version models with LiD and assessment taxonomy.
- Legal metadata with explicit tri-state rights and review fields.
- Object storage protocol plus safe local adapter, filename/MIME/size checks,
  and SHA-256 registration.
- PDF/text/Markdown parser registry and durable page provenance.
- Platform-only API routes for registry, upload, ingestion, approval, and
  rejection; no tenant-private knowledge path.
- ADR-021 through ADR-025 and the architecture baseline document.

## Validation boundary

Local unit tests, Ruff, mypy, migration checks, and frontend checks are
appropriate for this step. PostgreSQL/MinIO integration, remote CI execution,
external rights verification, and production object-store configuration remain
environment-dependent and are not claimed as complete here.

## Forward boundary

Phase 2 / Step 2 must not start from this report. Future work may add approved
knowledge retrieval and Standard Packs only after consuming the approval,
effective-date, supersession, checksum, and page-provenance invariants defined
here.

## Final Architecture Closure

Remote ancestry was refreshed and verified: `origin/main` now contains the
merged Phase 1 Step 2 authentication/session implementation at `e1f6b67`. The
Phase 2 branch is based on the Phase 1 authentication head and contains only
the standards foundation plus this closure.

The root assessment taxonomy is intentionally limited to `LANGUAGE` and
`INTEGRATION_KNOWLEDGE`. Provider, framework/curriculum, exam family, language,
skill, and CEFR remain separate metadata; LiD is integration knowledge with a
NULL CEFR level.

Current and historical resolution are separate service methods. Current
resolution excludes superseded, retired, and replaced versions. As-of-date
resolution uses approval state and effective intervals, so a superseded version
can resolve as historical evidence without becoming current.

Production storage is now an S3-compatible private-bucket adapter using the
maintained boto3 client, compatible with AWS S3 and MinIO endpoints. Backend
selection is environment-driven; production cannot select memory storage and
storage failures surface as controlled upload/ingestion failures.

Remote GitHub Actions status remains manual because `gh` is unavailable. Local
frontend, API, AI, offline Alembic, storage, taxonomy, historical-resolution,
provenance, rights, and authorization validation passed. Live PostgreSQL/MinIO
execution remains infrastructure-dependent.

## Migration development convention

Alembic revision identifiers must be 32 characters or fewer because the
standard `alembic_version.version_num` column is `VARCHAR(32)`. Use concise,
stable identifiers such as `0005_knowledge_chunks`; keep longer explanations
in migration filenames, docstrings, or reports.
