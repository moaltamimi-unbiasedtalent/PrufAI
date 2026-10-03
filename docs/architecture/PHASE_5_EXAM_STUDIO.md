# Phase 5 Exam Studio

Exam Studio assembles a concrete `ExamVersion` from one exact
`StandardPackVersion` and exact `QuestionBankItemVersion` IDs. `Exam` is stable
identity; `ExamVersion` is the immutable governed composition. The Candidate
Runtime is deliberately out of scope.

```text
StandardPackVersion
        |
        v
ExamVersion (DRAFT) -- materialized --> Module -> Section -> Task
                                             |
                              eligible Question Bank ItemVersions
                                             |
                                      exact item bindings
                                             |
                         structural + rights + RulesEngine validation
                                             |
                                  HumanReviewRequest/checksum
                                             |
                                APPROVED -> PUBLISHED -> ACTIVE
                                             |
                         admin manifest     delivery-safe manifest
```

## Governance

The pack version, module/section/task references, item version IDs, ordering,
classification, and explicit overrides are checksum-covered. No published
version resolves “latest” content. Blueprint materialisation is idempotent and
revalidates ownership against the pinned pack. Item assignment delegates to
the existing exact-pack eligible-bank service; the server revalidates every
selection.

Exams support `PLATFORM_SHARED` and `ORGANISATION_PRIVATE`. Shared records
require platform capability and have no organisation ID; private records must
have an organisation ID and are tenant-filtered. Private exams may use shared
items or the same organisation’s items, never another organisation’s private
items. The schema and service enforce scope boundaries.

Validation preserves structural, blueprint, Question Bank, pack-compatibility,
rights, classification, and Rules origins. Required task cardinality comes
from `min_items`/`max_items`; duplicate exact ItemVersions are blocked. Rights
expiry is checked for current delivery while historical versions remain
reproducible. `OFFICIAL_CERTIFICATION` is blocked because no explicit
authorization domain exists.

The existing RulesEngine evaluates the exact rules bound to the exact pack
version. Blocking failures and errors prevent approval/publication/activation.
Raw Decimal point metadata is aggregated only as a future scoring foundation;
provider weighting remains authoritative and candidate scoring is not built.
Timing, navigation, response mode, and sequence are projected from the pack.

Human review reuses `HumanReviewRequest` with `entity_type=EXAM_VERSION` and
the exact ExamVersion checksum. Any substantive change refreshes the checksum,
so stale approval cannot apply. AI may propose only a DRAFT with exact item IDs
and provenance; unknown IDs are rejected and AI cannot approve, publish, or
activate. Missing eligible content is an assembly gap, never an automatic
generation request.

The administrative manifest can include protected item content for authorized
review. The delivery-safe manifest is separate and uses the Question Bank safe
projection: it excludes answer keys, scoring keys, reviewer notes, AI critique,
internal validation, rights notes, and private object URLs. There is no public
candidate endpoint in this phase.

RBAC capabilities are `exam.read`, `exam.manage`, `exam.review`,
`exam.publish`, `exam.activate`, and `exam.export`. Consequential actions emit
existing immutable audit events. The focused Next.js Exam Studio page is an
accessible administration surface; eligibility and validation remain server
owned.

Current and as-of resolution use lifecycle status and effective dates. V2 can
supersede V1 without rewriting V1. Canonical serialization and SHA-256 checksums
support deterministic diffs for pack, structure, item, order, and configuration
changes.

## Known limitations

- PostgreSQL integration proves migration-backed schema in CI; full fixture
  lifecycle coverage remains a CI responsibility when database services run.
- Scoring transformations, candidate sessions, responses, timers, grading,
  appeals, proctoring, and official delivery are later phases.
- Import and automatic AI item generation are intentionally deferred.
