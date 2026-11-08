# P1A draft retention and erasure readiness contract

Status **DRAFT**. Approved retention policies **0**. Physical erasure authority **none**. Treat every inventory category as `RETENTION_POLICY = UNCONFIGURED` and `DELETION_ELIGIBILITY = NOT_DETERMINED` until approved policy and operating controls exist. [Data inventory](inventory.md) · [Storage and processors](storage-processors.md)

## Versioned policy record

This is a specification for a future registry. An inventory row or this document cannot authorize processing or deletion. A policy record applies to one exact responsible organisation, category, purpose and jurisdiction. Categories with several purposes need separately assessed rules. Require independent legal/privacy review and a designated accountable approver before activation.

| Required field | Draft and activation rule |
| --- | --- |
| Policy identity and scope | Stable policy ID, immutable version, category code, purpose, responsible organisation/controller, system owner, jurisdiction and applicable institutions or populations. Record the controller/processor determination and evidence. |
| Legal basis and evidence | Explicit assessment and reference for the purpose, applicable statutory obligations and exceptions, including any specific rule for this record class. Unknown at draft. |
| Retention start | Exact authoritative event, field and timestamp semantics. An appeal or correction might affect a rule only if that effect is approved and documented. Never substitute last access or record creation for an undefined event. |
| End condition | An approved duration from the start event or an objectively verifiable end event, including precedence and handling of missing dates. No default numeric period. |
| Holds and dependencies | Enumerate applicable hold bases, expiry/review rules and affected records, plus open attempts, pending reviews, disputes, appeals, corrections, exports and derived/historical dependencies. |
| Intended disposition | Purpose-specific restriction, access removal, approved redaction/pseudonymization, retention, or physical erasure. List primary, cached and third-party copies and exceptions. |
| Backup treatment | Recovery windows, encryption/roles, approved expiry or isolation, reapplication of restriction/erasure after restore and verification evidence. |
| Review and approval | Reviewing authority, independent reviewer/decision reference and accountable approver, exact reviewed version/checksum, effective date and supersession. Status `DRAFT`, `APPROVED`, or `WITHDRAWN`. Effective version is unset for this draft. |
| Checksum | SHA-256 of a canonical UTF-8 payload with schema version, scope, complete rule content and version. Specify deterministic sorted keys, compact separators, preserved array order, no NaN, no duplicate keys and explicit nulls for unknown draft fields. Exclude the checksum itself and detached approval receipt from the hashed payload. Bind approval and activation to the exact checksum. See the repository's [canonical checksum pattern](../../../services/api/app/writing_assessment/service.py). |

Missing, draft, inactive, withdrawn, stale, scope-mismatched, malformed or checksum-mismatched records cannot make a subject eligible. Never infer a term from technical TTLs, a source rights review, a synthetic fixture label, provider configuration, institution preference, or absent documentation. Unknown does not justify indefinite retention. Escalate it to the designated controller with a review date.

## Holds and evidence restrictions

A future hold record needs a scoped ID/version, controller, case/reference, authorized issuer, basis and evidence, exact subject/organisation/categories/dependencies, start, review date, current status, permitted access, and authorized release criteria. Record the issuer and independent review of any extension. Hold status `UNKNOWN` cannot become `CLEAR` by default. A hold must restrict only the necessary purpose, records and period. Use an explicit re-evaluation and closure receipt. There is no unrestricted permanent hold flag.

Treat open attempts, pending human review, active appeals, corrections, unresolved disputes and pending authorized exports as dependency blockers until checked. Treat a verified statutory obligation, active litigation or legal hold, or unresolved regulatory requirement as a legal/privacy review gate. Operational status can indicate where to look. It does not create a legal basis. Preserve appropriate access for dispute resolution and recovery when restricted processing is approved.

| Existing assessment evidence | Future disposition constraint |
| --- | --- |
| ScoringRun, ItemScore and response snapshots | Preserve exact response and scoring provenance while required. Access restriction and purpose separation can be considered independently of physical deletion. Never rewrite awarded points or answer evidence to simulate erasure. |
| ResultVersion, ResultRelease and statements | Preserve the exact outcome and successor graph. Revocation is an access/effective-state control. It does not erase historical records or copies already received. |
| Human decisions, appeals, moderation and corrections | Maintain author, chronology and checksum dependencies needed for review. Review each third-party identifier and private rationale separately. Redaction in a new authorized projection is distinct from editing an original decision. |
| Certification authorizations and certificate history | Separate issuer/scope evidence from person-specific issuance. Default deny and revocation are independent of retention policy. |
| Audit events and operational receipts | Prefer narrowly scoped access and minimized future events. Any future migration that separates identifying material must preserve approved evidentiary integrity. A genuinely nonpersonal tombstone must omit or irreversibly sever subject linkage under a verified threat model. Pseudonymized records can still be personal data. |

## Rights-request handling

The reviewed API routers and permission seeds contain domain-specific access, session revocation, corrections and review authority. They do not expose a dedicated privacy request or subject erasure service. Use a restricted, controller-designated intake and case record after the organisation establishes one. Do not collect identity evidence in logs, public tickets or broad notes.

1. **Received.** Record a case reference, purpose, jurisdiction, receipt time, requested right and authorized handler. Use separate controls for access, rectification, restriction, and erasure.
2. **Identity verified.** Use a proportionate identity and representative check. Keep only necessary proof under its own policy. Bind the verified subject to an exact organisation and authorization. A shared user ID does not authorize access across memberships.
3. **Scope established.** Resolve category and system boundaries, third-party information, processors and request limits. Reject conflicting ownership and cross-tenant enumeration without exposing existence.
4. **Records discovered.** Resolve exact IDs, versions and evidence graph under authorization. Include derivatives, audit, backups and providers. A discovery receipt contains category counts and opaque references with no raw text, audio or reviewer rationale.
5. **Assessment required.** Apply the exact approved policy version if available, check active and unknown holds, and document exemptions, restriction alternatives, appeal/correction dependencies and unanswered questions.
6. **Authorized decision.** Require a named authority and decision evidence. Record which actions and systems were approved, what must be retained and the legal rationale. Use a new approval if the scope, evidence version or policy changes.
7. **Execution planned.** Use a separate, authorized implementation and operator runbook. Require idempotency, recoverable per-store receipts, fail-closed revalidation, processor coordination and post-restore reconciliation. P1A stops before execution.
8. **Completion verified.** A future independent reviewer must reconcile database, object, caches, logs, processors and backups, verify disclosure restrictions and record lawful remaining copies before completing the response. Establish the timing and communication process with the controller.

### Legal and privacy review questions

- Which entity is controller, joint controller or processor for each category and purpose? What legal bases apply to collection, scoring, records, review, abuse protection, quality improvement and AI? What minimization and end condition serve each purpose under Articles 5 and 6? Are special-category data ever present or intentionally processed? [GDPR Articles 5, 6 and 9](https://eur-lex.europa.eu/eli/reg/2016/679/oj/eng)
- How will handlers verify identity proportionately, protect other people in access responses, and distinguish erasure, correction, restriction and notifications to recipients? Which concrete legal obligations or legal-claims exceptions apply, to which records and for how long? How do appeals, corrections and audit needs affect a lawful assessment without an invented retention period? [GDPR Articles 12 and 15–19](https://eur-lex.europa.eu/eli/reg/2016/679/oj/eng)
- Which processors and subprocessors receive each input? Which contractual instructions, onward transfers, residency, training/logging, security, deletion assistance and backups apply? Who owns recovery, incident escalation and evidence of post-restore reconciliation? Determine whether impact assessment or automated-decision safeguards apply. [GDPR Articles 22, 28, 30, 32, 35 and Chapter V](https://eur-lex.europa.eu/eli/reg/2016/679/oj/eng)

## Bounded future preview and acceptance cases

**P1A decision.** The preview is **NOT IMPLEMENTED**. There is no privacy-case authority, approved versioned policy registry, legal-hold ledger, or trusted classification proving an arbitrary candidate reference synthetic in the existing tenant services. Candidate ownership, normal institution access and platform metadata roles are insufficient substitutes. A static specification avoids a new subject-discovery path. No Control Centre subject browser, policy-approval UI or automatic executor is warranted here.

A future read-only planner should accept a previously verified synthetic subject bound to one exact organisation and case, an allowlisted category set, the expected policy version/checksum and a server-evaluated timestamp. Cap traversals and counts, use a consistent read snapshot, and require a dedicated scope and actor on every query. Resolve authoritative and derived dependencies separately. Emit per-category counts, object **classes** and dependency/hold codes, applicable policy state, unresolved decisions and possible future action categories. Do not emit keys, filenames, URLs, raw text, audio, raw provider payloads or third-party identifiers. A failed authorization should be indistinguishable from lack of access to the record. Do not make existence an oracle. A technical output of `POTENTIALLY_ELIGIBLE` never authorizes execution.

| Case | Expected preview outcome and acceptance |
| --- | --- |
| Authenticated wrong subject, forged reference, wrong institution, missing dedicated authorization, platform metadata only | Deny before data discovery. Same observable outcome for absent and inaccessible subjects. |
| No record under an already authorized exact subject and category | `NOT_FOUND`. Apply only after the authorization boundary. |
| Missing or draft policy | `POLICY_UNCONFIGURED`. Eligibility stays `NOT_DETERMINED`. No action planned. |
| Stale version/checksum or unverified governing basis | Reject stale inputs. For unresolved lawful basis or unknown hold, `LEGAL_REVIEW_REQUIRED`. |
| Verified current hold, open attempt, pending review/appeal/correction, unresolved dispute, pending authorized export | `HOLD_ACTIVE` for a confirmed authorized hold, otherwise a blocked `LEGAL_REVIEW_REQUIRED` or `NOT_YET_ELIGIBLE` with the dependency code. Preserve the exact affected evidence and verify non-destructively. |
| Approved exact policy but missing start event or unexpired condition | `LEGAL_REVIEW_REQUIRED` for unknown evidence, `NOT_YET_ELIGIBLE` when the condition has not been met. |
| Unknown object class, stale/conflicting ownership or unsafe lineage | `UNSUPPORTED`. No storage lookup from caller-supplied keys and no guessing owner. |
| Exact current policy, condition met, holds checked and dependencies reconciled | `POTENTIALLY_ELIGIBLE`. Return possible actions for independent decision. No write or delete. |
| Repeat same authorized request, same snapshot and evaluation time | Same minimized output. Verify database, object versions, caches and audit side effects explicitly. Any future access receipt must be separately governed and must never contain raw subject evidence. |

Run future planner tests with explicit disposable PostgreSQL identity, Alembic migration, private real MinIO and synthetic fixtures. Test missing policy, active/unknown hold, stale evidence, conflicting ownership and cross-tenant denial. Compare pre/post row counts and object digests for the synthetic case to prove read-only behavior. Independently cover held-out evaluation and source rights. These future planner acceptance cases have **not run** in P1A because no runtime planner or hold registry is shipped. Existing domain integration checks continue to cover exact response and revision binding, media identity, authorization, review, scoring, release, revocation, redacted projections and source rights.
