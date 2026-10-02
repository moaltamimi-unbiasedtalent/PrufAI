# Phase 3 / Step 1: Standard Pack Framework

Status: Accepted for MVP baseline

## Purpose

Phase 2 records what approved knowledge says. This step records how an
assessment standard is formally represented. A Standard Pack is authored,
validated, reviewed, versioned, and published; it is not an automatically
generated RAG result.

```text
Knowledge Authority
        |
Approved Source Documents
        |
Pinned Source Bindings
        |
Standard Pack -> Version -> Modules -> Sections -> Task Specifications
                                      |
                              Structural validation
                                      |
                             Human review and approval
                                      |
                         Published immutable version
                                      |
              Future: Rules Engine -> Question Bank -> Exam Studio
```

## Model and taxonomy

`StandardPack` is the stable identity. `StandardPackVersion` owns effective
dates, lifecycle, source bindings, blueprint, classification policy, schema
version, and substantive SHA-256 checksum. Versions may support one or more
explicit CEFR levels (`A1` through `C2`) for `LANGUAGE`. `INTEGRATION_KNOWLEDGE`
uses its own exam-family metadata and must have no supported CEFR levels.
Provider/authority identity is reused from `KnowledgeAuthority`; no provider
table or provider-specific behavior is added.

The blueprint hierarchy is:

```text
Pack Version
  └── Module (ordered, skill/domain, timing, navigation)
       └── Section (ordered, timing, navigation)
            └── Task Specification (type, item range, response/media,
                generation/selection flags, rubric reference)
```

Timing, navigation, response modes, media requirements, scoring references,
result references, and rubric references are declarative placeholders. No
timing engine, scoring engine, rubric engine, question bank, candidate exam, or
Rules-as-Code expression is executed here.

## Evidence, lifecycle, and immutability

Normative bindings must pin an exact `KnowledgeDocumentVersion` and accept only
approved or historically approved (`SUPERSEDED`/`RETIRED`) evidence. A source
reference without an exact document version cannot be normative. Older pack
versions retain their bindings when newer knowledge is approved.

The controlled lifecycle is `DRAFT -> VALIDATION_REQUIRED -> HUMAN_REVIEW ->
APPROVED -> PUBLISHED -> ACTIVE -> RETIRED`, with `REJECTED` and `SUPERSEDED`
where appropriate. The service blocks invalid transitions, direct draft-to-
published promotion, and all substantive mutation after publication. Historical
resolution uses effective dates and status; future exams must pin a version ID,
not only a pack ID.

`OFFICIAL_CERTIFICATION` is prohibited by default. No provider pack is seeded
or claimed as authorized. Allowed classifications are practice, mock,
placement/progress, and institutional unless a future explicit authorization
mechanism is introduced.

## Validation, serialization, and exchange

`StandardPackService.validate` returns a typed status and machine-readable
issue codes such as `PACK.LID_CEFR_NOT_ALLOWED`,
`PACK.MISSING_NORMATIVE_SOURCE`, `PACK.NORMATIVE_SOURCE_NOT_APPROVED`,
`PACK.INVALID_TASK_ITEM_RANGE`, and
`PACK.OFFICIAL_CLASSIFICATION_NOT_AUTHORIZED`. FAIL blocks review, approval,
and publication. Canonical JSON excludes timestamps and audit metadata, is
sorted and deterministic, and feeds the stored SHA-256 configuration checksum.
The deterministic diff reports metadata, bindings, modules, timing, and
classification changes.

JSON export contains identity/version IDs, source/document IDs, blueprint,
classification policy, schema version, and checksum; it never embeds source
document bodies or private object-storage URLs. Import parses and validates
data only, rejects executable markers, and never approves or publishes.

## Governance and boundaries

Platform permissions are `standard_pack.read`, `.manage`, `.review`, and
`.publish`; tenant roles receive no implicit platform pack authority. Lifecycle,
binding, validation, approval, publication, activation, retirement, supersession,
and export events use `AuditService` without storing full pack data.

Knowledge retrieval may help experts inspect evidence, but it cannot auto-create
or publish pack configuration. Rules-as-Code is intentionally deferred to
Phase 3 / Step 2. Future tenant-private/custom packs remain an extension point;
this step implements platform-managed packs only.

All fixtures are synthetic and marked `TEST FIXTURE — NOT OFFICIAL STANDARD`.
No provider PDFs, copied questions, or copyrighted instructions are included.
