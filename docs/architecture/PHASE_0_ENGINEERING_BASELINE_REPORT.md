# Phase 0 / Step 2 Engineering Baseline Report

**Project:** PrüfAI
**Starting commit:** `9a1284a389bcbe608873eb283d4d6c277b492ba` (`Initial commit`)
**Canonical branch:** `main`
**Implementation date:** 2026-10-02

## 1. Executive Summary

The greenfield repository now has a reproducible engineering foundation without product-domain implementation. It contains a minimal Next.js web application, FastAPI API boundary, independently runnable FastAPI AI boundary, PostgreSQL/SQLAlchemy/Alembic configuration, local PostgreSQL/Redis/MinIO Compose infrastructure, developer commands, CI validation, target architecture documentation, ADRs, and Standard Pack placeholders.

Authentication, tenant business logic, exams, question banks, RAG, LLM calls, LangChain, LangGraph, agents, scoring, assessments, HITL workflows, candidate functionality, and product database tables remain intentionally unimplemented.

## 2. Technology Decisions

- Frontend: Next.js 15.5.27, React 19, strict TypeScript, App Router.
- API: Python 3.9+ and FastAPI with dependency-injection seams.
- AI boundary: independently runnable FastAPI service with no AI SDK or model call.
- Database: PostgreSQL target with SQLAlchemy 2.x and Alembic.
- Supporting infrastructure: Redis and S3-compatible MinIO for local development.
- Architecture: modular monolith, with future independently scalable AI/background boundaries.

## 3. Repository Structure

```text
apps/web/              Next.js baseline
services/api/          FastAPI API, configuration, DB engine, Alembic
services/ai/           Minimal AI service boundary
packages/shared-contracts/  Shared-contract boundary placeholder
standard-packs/        CEFR, DTZ, LiD, Goethe, telc, PrüfAI placeholders
infra/                 Local Compose dependencies
docs/architecture/     Audit, target architecture, ADRs, this report
tests/                 Cross-project test placeholder
```

## 4. Services Created

### Web

The homepage displays `PrüfAI Platform` and `Engineering baseline`. `/status` is a minimal status page. One unit test establishes the test harness.

### API

`GET /health` returns `{"service":"prufai-api","status":"ok"}`. Configuration, SQLAlchemy engine creation, an Alembic environment, and an empty `0001_baseline` migration are present. Domain packages are architectural boundaries only.

### AI

`GET /health` returns `{"service":"prufai-ai","status":"ok"}`. No LLM, RAG, agent, LangChain, or LangGraph dependency is installed.

## 5. Developer Tooling

The root `Makefile` provides `install`, `dev`, `web-dev`, `api-dev`, `ai-dev`, `lint`, `format-check`, `typecheck`, `test`, `build`, `infra-up`, and `infra-down`. `.env.example` contains names and local-only placeholders; no production secrets are committed. Generated dependencies and caches are ignored.

## 6. Database Baseline

The API uses SQLAlchemy 2.x and Alembic against a configurable PostgreSQL URL. No product tables are defined. `python3 -m alembic upgrade head --sql` successfully generated SQL for the empty baseline migration chain. A live database migration was not executed because the Docker daemon was unavailable in this environment.

## 7. Local Infrastructure

`infra/docker-compose.yml` defines PostgreSQL 16, Redis 7, and MinIO with persistent named volumes and healthchecks. `docker compose ... config -q` passed. `docker compose up -d` could not run because this environment denied access to the local Colima Docker socket; no containers were started by this audit.

## 8. CI

`.github/workflows/ci.yml` validates web install/lint/typecheck/tests/build and Python dependency installation/lint/format/typecheck/tests. It does not deploy anything.

## 9. Architecture Documentation

`TARGET_ARCHITECTURE_V1.md` documents the modular monolith, web/API/AI boundaries, PostgreSQL, future pgvector, Redis, object storage, Standard Packs, RAG, rules, agents, memory, HITL, evaluation, feedback/learning, audit/provenance, security principles, taxonomy, and versioning principle.

## 10. ADRs

Ten ADRs record the system architecture, monorepo structure, backend platform, database, AI boundary, multi-tenancy principles, Standard Packs, rules versus RAG, memory model, and audit/provenance. All are marked `Accepted for MVP baseline`; none claims that deferred functionality already exists.

## 11. Validation Results

| Check | Result |
|---|---|
| Frontend lint | PASS |
| Frontend format check | PASS |
| Frontend typecheck | PASS |
| Frontend tests | PASS — 1 test |
| Frontend production build | PASS |
| API lint | PASS |
| API format check | PASS |
| API typecheck | PASS — 23 source files |
| API tests | PASS — 1 test |
| AI lint | PASS |
| AI format check | PASS |
| AI typecheck | PASS — 2 source files |
| AI tests | PASS — 1 test |
| Docker Compose configuration | PASS |
| Alembic offline baseline SQL | PASS |
| Live Compose startup | UNVERIFIED — Docker socket permission denied |
| Live HTTP server bind | UNVERIFIED — sandbox disallows localhost bind |

## 12. Files Created

- Root tooling/configuration: `.gitignore`, `.env.example`, `Makefile`.
- Web baseline: `apps/web/` including package manifest/lockfile, App Router pages, strict TypeScript, ESLint, Prettier, Vitest, and Next config.
- Python services: `services/api/` and `services/ai/` with `pyproject.toml`, health app, tests, and API Alembic baseline.
- Infrastructure: `infra/docker-compose.yml`.
- CI: `.github/workflows/ci.yml`.
- Architecture: `docs/architecture/TARGET_ARCHITECTURE_V1.md` and `docs/architecture/adr/ADR-001` through `ADR-010`.
- Standard Packs: `standard-packs/README.md` and empty provider/family directories.
- Phase report: this file.

The existing `docs/architecture/PHASE_0_REPOSITORY_AUDIT.md` was preserved unchanged.

## 13. Known Limitations

1. No authentication, authorization, tenancy, candidate, exam, assessment, AI, or product persistence logic exists by design.
2. The sandbox could not start Docker or bind local HTTP ports, so live dependency and network checks remain for a normal developer machine or CI runner.
3. The Python services currently use conventional `app` package names and are validated as separate service processes; cross-service integration testing is deferred.

## 14. Risks

- The next phase must define identity, tenant context, authorization, and audit invariants before introducing business data.
- Product schema design must preserve immutable versioning and AI/assessment provenance.
- Standard Pack source rights and EU data-processing requirements must be resolved before content ingestion or candidate data handling.
- `npm audit --omit=dev` reports three production dependency advisories in the Next/PostCSS/sharp tree; remediation currently points to a later Next major and must be triaged before production use.

## 15. Recommended Phase 1 Work

Implement identity, multi-tenancy, authorization, and append-oriented audit foundations with explicit tests and migrations. Keep the current service boundaries and validation gates intact, and do not introduce exam or AI product workflows until those boundaries are reviewed.

## Phase 0 Closure

- **Branch:** `phase/0-engineering-baseline`
- **Frontend dependency:** Next.js `15.5.27` remains on the supported 15.x line; no unreviewed major framework upgrade was made.
- **Safe dependency remediation:** transitive `sharp` upgraded from `0.34.5` to `0.35.5` through the package manager lockfile.
- **Security audit before:** 0 critical, 2 high, 1 moderate, 0 low production advisories.
- **Security audit after:** 0 critical, 1 high, 1 moderate, 0 low production advisories.
- **Remaining advisories:** PostCSS advisories inherited through Next.js; npm only offers remediation via Next.js `16.3.8`, a breaking major upgrade. This is documented for review and is not a Phase 0 blocker for local engineering validation, but it must be resolved before production use.
- **Validation:** frontend, API, AI, Compose configuration, and Alembic offline checks passed. Live Docker/HTTP checks remain environment-limited.
