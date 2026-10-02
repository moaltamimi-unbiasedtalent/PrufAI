# PrüfAI

PrüfAI is a Germany-first, standards-driven AI examination platform for German-language assessments from A1 to C2 and the separate Leben in Deutschland (LiD) assessment family. The product will eventually support controlled standards, human governance, auditability, and institution-facing administration.

## Current implementation status

**Current state: Engineering foundation — product domains not yet implemented.**

This repository currently establishes a small modular-monolith baseline: a Next.js web app, FastAPI API, independently runnable FastAPI AI boundary, PostgreSQL/Alembic database foundation, local supporting infrastructure, CI, and architecture documentation. Authentication, tenant business logic, exams, question banks, RAG, LLM calls, agents, scoring, assessment, HITL workflows, and candidate functionality are intentionally deferred.

## Repository structure

```text
apps/web/             Next.js + TypeScript App Router frontend
services/api/         FastAPI application and database foundation
services/ai/          Minimal independently runnable AI service boundary
packages/              Shared-contracts placeholder
standard-packs/        Versioned standards placeholders (no copied content)
docs/architecture/     Architecture baseline, audit, and ADRs
infra/                 Local PostgreSQL, Redis, and MinIO Compose setup
tests/                  Cross-project test placeholder
```

## Prerequisites

- Node.js 20+ and npm
- Python 3.9+
- Docker Desktop with Compose v2

Copy `.env.example` to a local `.env` only when needed. The example values are local-development placeholders, not production credentials.

## Local infrastructure

```bash
docker compose -f infra/docker-compose.yml up -d
```

This starts PostgreSQL, Redis, and MinIO. Stop it with:

```bash
docker compose -f infra/docker-compose.yml down
```

## Install and run

```bash
make install
make web-dev   # http://localhost:3000
make api-dev   # http://localhost:8000/health
make ai-dev    # http://localhost:8001/health
```

Run each development service in its own terminal. The API and AI service health endpoints are deliberately the only runtime product surface in this phase.

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

- [Phase 0 repository audit](docs/architecture/PHASE_0_REPOSITORY_AUDIT.md)
- [Target Architecture V1](docs/architecture/TARGET_ARCHITECTURE_V1.md)
- [Phase 0 engineering baseline report](docs/architecture/PHASE_0_ENGINEERING_BASELINE_REPORT.md)
- [Architecture Decision Records](docs/architecture/adr/)
