# Phase 4 / Step 2 implementation report

Implemented governed memory on branch `phase/4-governed-memory`.

- Added `0011_governed_memory` (21-character revision) with memory identities,
  versions, evidence bindings and AI-run references.
- Added deterministic precedence/context assembly and JSON-only short-term
  Redis/test backends with TTL, size and count bounds.
- Added lifecycle governance, scope filtering, checksums, audit events, RBAC
  capabilities and human-only approval/activation routes.
- No feedback-learning lifecycle, autonomous approval, personal-memory
  candidates, prompt mutation or production deployment was introduced.

Local static/unit validation is reported separately. Redis/PostgreSQL service
integration and remote CI require the configured external services and are not
claimed by local checks alone.

## Final governance values

- Precedence: `AUTHORITATIVE_KNOWLEDGE > RULES > APPROVED_POLICY >
  APPROVED_LONG_TERM_MEMORY > SHORT_TERM_MEMORY`; `USER_INPUT` and `SYSTEM`
  are separately typed context origins and cannot elevate memory authority.
- Context origins: `AUTHORITATIVE_KNOWLEDGE`, `RULES`, `APPROVED_POLICY`,
  `APPROVED_LONG_TERM_MEMORY`, `SHORT_TERM_MEMORY`, `USER_INPUT`, `SYSTEM`.
- Redis short-term defaults: 3,600-second TTL, 16,384-byte JSON item limit,
  100 items per run/namespace, 50 results per read. Keys use
  `prufai:memory:short:{tenant|platform}:{run}:{namespace}:{key}`.
- Redis failures raise `MemoryBackendError`; no process-memory fallback exists.
  HITL retention refreshes required context to 86,400 seconds and abandoned
  context then expires.
- Long-term production eligibility is `ACTIVE`, effective, unexpired, and not
  past `review_after`; overdue review is conservatively ineligible for new
  consequential workflows. Historical rows remain retained for audit.
- Scopes are `PLATFORM_SHARED` and `ORGANISATION_PRIVATE`; the former requires
  platform authority and null organisation ID, the latter requires a matching
  trusted organisation context.
- AI memory capabilities are explicit per agent/workflow through
  `MemoryPolicy`; exposed tools are short-term read/write, long-term read, and
  long-term proposal. No approve, activate, publish, or supersede tool exists.
- AIRun references are normalized by `(ai_run_id, memory_version_id)` with the
  exact version checksum. Evidence bindings remain subordinate provenance and
  never change the `LONG_TERM_MEMORY` source class.
- Candidate personal memory is out of scope. Content is schema-bounded to
  summary/guidance/tags/context/exceptions/limitations; no credential fields or
  bulk source-document export path is provided.
- Local integration skips are infrastructure skips only: PostgreSQL and Redis
  services are unavailable locally. CI invokes both required integration
  subsets after service startup and Alembic migration.
- Audit event names emitted by the memory service are `memory.created`,
  `memory.version.created`, `memory.proposed`, `memory.review_required`,
  `memory.approved`, `memory.active`, `memory.retired`,
  `memory.rejected`, `memory.superseded`, and `memory.expired`. Audit metadata
  contains identifiers/checksums only, never complete memory content; Redis
  reads/writes are not individually audited.
- Checksum canonicalization is deterministic SHA-256. The version checksum
  includes substantive content, category, scope and evidence identifiers when
  supplied; timestamps and audit fields are excluded. The current API exposes
  no mutation path for active versions; substantive changes require a new
  version. `GovernedMemoryService.diff` is deterministic and reports content,
  scope, category, evidence/checksum, and effective/expiry changes.
