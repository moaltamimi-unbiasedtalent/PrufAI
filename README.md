# PrüfAI

PrüfAI develops governed German-language assessment and practice workflows, including the separate Leben in Deutschland (LiD) assessment family.

The candidate and institution applications and the Control Centre have automated technical acceptance for versioned practice, role boundaries, scoring, human-controlled result release, audit and operational recovery. The first release proposal is a bounded non-official Berlin LiD practice pilot. Its scope, human decisions and production evidence are pending. See the [V1 scope proposal](docs/releases/v1-scope-proposal.md) and [readiness register](docs/releases/v1-readiness-register.md).

## Repository structure

```text
apps/web/             Candidate, institution and Control Centre web application
services/api/         FastAPI product APIs, workers and Alembic migrations
services/ai/          Independently runnable AI service boundary
packages/             Shared contracts
standard-packs/       Versioned standards and technical fixtures
docs/                 Architecture, governance, operations and release evidence
infra/                Local development and disposable acceptance configuration
tests/browser/        Integrated browser acceptance
```

## Prerequisites

- Node.js 24 LTS and npm (the verified CI/local security baseline)
- Python 3.11+ (Python 3.9 and 3.10 are unsupported)
- Docker Desktop with Compose v2

Copy `.env.example` to a local `.env` only when needed. The example values are local-development placeholders, not production credentials.

## Local infrastructure

For candidate recorded-audio evidence, use the supported private MinIO and pinned
media-runtime workflow in [Recorded audio primary evidence](docs/recorded-audio.md).
That path preserves objects and avoids reliance on the older Compose MinIO image.

The separate durable transcription worker and its closed-by-default provider
approval boundary are documented in
[Governed transcription execution](docs/transcription-execution.md). Technical
TEST ONLY execution is separate from authentic speech-recognition acceptance.

The separate governed Writing proposal worker, exact registry and deployment
approval gates, and technical acceptance boundary are documented in
[AI-assisted Writing proposal execution](docs/ai-writing-proposals.md).

The separate audio-first Speaking proposal worker, exact deployment approval,
original-audio/transport distinction and mandatory human review are documented in
[AI-assisted Speaking proposal execution](docs/ai-speaking-proposals.md).

```bash
docker compose -f infra/docker-compose.yml up -d
```

This starts PostgreSQL, Redis, and MinIO. Stop it with:

```bash
docker compose -f infra/docker-compose.yml down
```

## Install and run

Use a fresh virtual environment created with Python 3.11 or newer before running
the existing Makefile commands. An existing Python 3.9 environment must be rebuilt;
this does not require resetting PostgreSQL, Redis or object storage. The security
minimums, audited versions and compatibility decisions are recorded in
[Dependency security audit](docs/security/dependency-audit-2026-11-04/README.md).

```bash
make install
make web-dev   # http://localhost:3000
make api-dev   # http://localhost:8000/health
make ai-dev    # http://localhost:8001/health
```

Run each development service in its own terminal. The API exposes process liveness at `/health` and dependency readiness at `/ready`. See [local demo setup](docs/local-demo.md) for disposable product journeys.

## Validation commands

```bash
make lint
make format-check
make typecheck
make test
make build
```

Docker Compose configuration can be checked with:

```bash
docker compose -f infra/docker-compose.yml config
```

## Architecture documentation

[Privacy and data lifecycle inventory](docs/security/data-lifecycle/inventory.md) maps assessment evidence, its storage and draft retention decisions.

- [Phase 0 repository audit](docs/architecture/PHASE_0_REPOSITORY_AUDIT.md)
- [Target Architecture V1](docs/architecture/TARGET_ARCHITECTURE_V1.md)
- [Phase 0 engineering baseline report](docs/architecture/PHASE_0_ENGINEERING_BASELINE_REPORT.md)
- [Architecture Decision Records](docs/architecture/adr/)
- [Original Berlin LiD practice editorial workflow](docs/lid-original-editorial.md)
