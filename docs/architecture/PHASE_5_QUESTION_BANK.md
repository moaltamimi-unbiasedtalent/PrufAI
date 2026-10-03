# Phase 5 / Step 1 — Governed Question Bank

The Question Bank is versioned assessment content. It is not Knowledge,
Standard Pack structure, a Rule, an Exam, a candidate response, or AI memory.

```text
Authoritative Knowledge ── evidence/alignment ──┐
                                                ▼
QuestionBankItem ── QuestionBankItemVersion ── content + scoring metadata
                                                │
              CEFR / skill / task / response ──┤
              rights / provenance / evidence ───┤
              exact StandardPackVersion ────────┤
                                                ▼
                              deterministic validation + Rules checks
                                                ▼
                                         human review
                                                ▼
                                  immutable published version
                                                ▼
                       future Exam Studio pins exact item-version IDs
```

AI may propose an item into `DRAFT` or `VALIDATION_REQUIRED`; it has no
approval, publication, activation, provider-authorization, or certification
capability. Existing HumanReviewRequest, Rules, Knowledge, Standard Pack and
Audit domains remain authoritative in their own boundaries.

## Identity, scope and lifecycle

`QuestionBankItem` is a stable scoped identity. Substantive content lives only
on `QuestionBankItemVersion`. The MVP supports `PLATFORM_SHARED` and
`ORGANISATION_PRIVATE`; platform scope requires platform authority and private
scope requires the trusted matching organisation. Candidate-private content is
out of scope.

Lifecycle is `DRAFT → VALIDATION_REQUIRED → HUMAN_REVIEW → APPROVED →
PUBLISHED → ACTIVE → RETIRED`, with `REJECTED` and `SUPERSEDED` terminal
branches. Published/active versions have no mutation API; a substantive change
requires a new version. Historical versions remain addressable.

## Taxonomy and typed content

The root families remain `LANGUAGE` and `INTEGRATION_KNOWLEDGE`. Language
content records explicit `de` language metadata and CEFR levels A1–C2 through a
normalized association. LiD (`LEBEN_IN_DEUTSCHLAND`) content cannot carry CEFR.
Skills, shared response modes, task types and media requirements are explicit
metadata. MVP typed content schemas cover synthetic multiple choice, extended
writing and oral response. Option IDs, ordering and answer keys are stable
structured data; display text is never used as an answer key.

Scoring metadata uses constrained policies (`OBJECTIVE_KEYED`,
`RUBRIC_REQUIRED`, `MANUAL_REVIEW`, `NON_SCORED`) and Decimal-safe points. No
candidate scoring or psychometric calibration is implemented.

## Rights, provenance and compatibility

Every version has explicit rights status. `UNKNOWN` and incompatible restricted
rights block publication; licensed content requires a license reference.
Standard Pack alignment binds exact `StandardPackVersion` IDs, optionally exact
task specifications. Alignment does not imply provider ownership or official
provider authorization. Evidence bindings reference existing Knowledge records
or evidence IDs without copying source documents. Media references use private
object keys and never expose storage URLs.

Structural validation is deterministic and returns PASS/FAIL issues with stable
codes. It checks typed content, taxonomy, CEFR/LiD rules, answer keys, scoring,
rights and exact pack task compatibility. Rules-as-Code remains the blocking
authority: the service loads exact persisted StandardPackVersion bindings,
builds a narrow item fact adapter, and executes the existing RulesEngine. Rule
IDs/checksums and engine reports remain tied to that exact pack version; a later
RuleVersion is never selected implicitly.

Checksums are deterministic SHA-256 over canonical substantive content,
taxonomy, scoring, rights, CEFR, evidence and pack references; timestamps and
audit metadata are excluded. A normalized textual fingerprint supports exact
duplicate detection only; it is not semantic plagiarism detection.

## Search, export and security boundaries

Search is paginated and query-level scope filtered. Filters do not widen CEFR,
family or provider-aligned content. Only explicitly requested statuses are
returned; future Exam Studio must request `ACTIVE` versions and pin exact IDs,
never `latest`.

The eligible-bank service requires an exact `standard_pack_version_id`, optional
exact task specification, ACTIVE/effective/rights-compatible status, passing
pack compatibility, and a non-blocking exact-rule evaluation. Current and
as-of-date resolution use effective windows and lifecycle state rather than
creation timestamps. Review requests reuse `HumanReviewRequest` with
`QUESTION_BANK_ITEM_VERSION` and the substantive checksum; changed content is
stale and cannot be approved.

Administrative export is bounded and machine-readable. It contains content,
metadata, rights, checksums and references, but no private object URLs, secrets,
or linked Knowledge documents. Future candidate delivery must use a separate
projection that excludes answer keys, scoring keys and reviewer notes; that
runtime is not implemented here. Stored text is inert data and is not evaluated
as code or rendered as trusted HTML.

RBAC uses `question_bank.read/manage/review/publish/export/import`; business
logic checks capabilities and TenantContext rather than role-name strings.
Consequential actions emit bounded AuditService metadata containing IDs,
statuses and checksums, not full question text or answer keys.

## Explicit non-goals

This step does not implement Exam/ExamVersion, Exam Studio, automatic exam
assembly, candidate runtime or responses, psychometric calibration, adaptive
testing, provider ingestion, copyrighted provider fixtures, or production
AI item-generation workflows. All repository fixtures are marked
`TEST FIXTURE — NOT OFFICIAL MATERIAL`.
