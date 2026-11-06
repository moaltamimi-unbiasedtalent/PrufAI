# Original Berlin LiD practice editorial workflow

S4 provides one organisation-private set of 30 general and three Berlin
questions. Each German-language item has four choices, one proposed key, an
answer rationale, and a factual claim tied to an exact Source Registry edition.
The material is `AI_ASSISTED_DRAFT` in the `INTEGRATION_KNOWLEDGE` assessment
family and `LEBEN_IN_DEUTSCHLAND` exam family. The classification is
`PRACTICE` and CEFR is unset. Berlin is the only supported state. A request
for another recognized state returns `CONTENT_UNAVAILABLE`.

The original drafts and the [S3 technical fixture](lid-practice-foundation.md)
have separate packs and pools. S3 stays TEST ONLY. The S4 content remains
subject to independent human review. It uses original educational practice
questions and carries the disclosure **NOT OFFICIAL / NOT CERTIFIED**. The
15/33 orientation and 17/33 naturalisation comparisons remain distinct
numerical practice contexts. Official LiD certification remains denied.

## Open the workbench

1. Open **Control Centre → Standards & packs → Leben in Deutschland blueprint
   → Original Berlin LiD editorial set**. Question Bank also links to
   `/control-center/standards/lid/editorial`.
2. Select an organisation where you have an active membership. An editor with
   `question_bank.manage` and the required platform Source Registry management
   permissions can create the collection. Select the existing governed LiD
   blueprint by its exact version and checksum. Creation registers the curated
   references as metadata only and creates a separate draft pack and private
   Question Bank item versions.
3. Select an item to inspect its question, proposed answer, rationale, claim
   locator, source version and rights status. The workbench shows version
   history, receipt metadata, counts by review stage, pool eligibility, and pack
   blockers. Read access requires `question_bank.read` in the organisation.

## Review and publish

1. The editor uses **Submit item for factual review** on the current item. The workbench
   submits the item checksum. The review request and its receipts preserve the
   exact question and provenance.
2. Review in this order. Each stage needs a distinct authorized reviewer who
   did not author or submit the item.

   | Stage                   | Organisation capability     | Reviewer checks                                                                                                                                |
   | ----------------------- | --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
   | Factual                 | `question_bank.review`      | Source edition, applicability, jurisdiction, answer key, and distractor correctness                                                            |
   | Assessment              | `question_bank.review`      | Clarity, accessibility, absence of ambiguous alternatives, and fit with the practice blueprint                                                 |
   | Original-content rights | `standards.document.review` | Originality, factual dependencies, and documented permissions for practice and display, with redistribution and derivatives decided separately |

3. Use the Evaluation examiner qualification workflow for the exact stage
   (`LID_FACTUAL`, `LID_ASSESSMENT` or `LID_RIGHTS`). Register the actual
   examiner, organisation, `de`, S4 pack version and checksum, time-zoned
   expiry, and credential evidence. LiD qualifications have no rubric.
   A different authorized actor approves the qualification. Genuine item
   approval requires current `VERIFIED_CREDENTIAL` qualifications.
4. A rejection is retained in item history. Create a successor of the current
   rejected version while the pack is a draft and submit the new version for
   review. Source-stale items in human review or approved state can use the
   source replacement procedure below. Every successor starts a fresh review.
5. Once all 33 current items have genuine approvals, a pack approver with
   `standard_pack.review` can approve the exact collection evidence. The pack
   approver must be independent of the collection author. A
   separate publisher with `standard_pack.publish`, `question_bank.publish`,
   and the required Exam Studio permissions can publish that same evidence.
   The publisher must be independent of the collection and item authors, item
   reviewers, and pack approver. The published edition pins its 30+3 form,
   rules, pool versions, item keys, and 60-minute timer. Candidate access
   requires an authorized assignment to an eligible published edition.

The workbench's **Open pinned Berlin editorial preview** action is restricted
to authorized reviewers in a development or test environment backed by an
explicitly recognized disposable PostgreSQL database. It shows exact item
versions and keys for editorial inspection. `SYNTHETIC_TEST` review receipts
are allowed only in that isolated environment, carry a TEST ONLY label, and
confer zero genuine publication eligibility. The acceptance workflow uses
Alembic-migrated disposable PostgreSQL and private MinIO. Leave the default
development database untouched.

## Keep source pins current

The initial item inventory registers 18 references from official legal and
institutional publishers. Each draft records its factual proposition, exact
locator, dated applicability or historical event, last verification, and
recheck deadline. For approval, an independent platform rights reviewer uses
**Control Centre → Source Registry** to record the exact version's `METADATA_ONLY`
decision with the platform `standards.document.review` capability. Source
registration requires the `standards.authority.manage`,
`standards.source.manage`, and `standards.document.manage` platform
capabilities. A source rights decision permits bibliographic reference only.
It confers no source text ingestion, copying, or redistribution permissions.
Independent item rights review governs the original question separately.

1. For an amended or replaced source, use
   `POST /api/v1/platform/standards/sources/{source_id}/versions` to register a
   metadata-only successor in the **same** source. Set
   `supersedes_document_version_id`, its HTTPS official edition URL,
   `retrieved_at`, and known publication and effective dates. Keep the exact
   new version ID and metadata checksum.
2. Have the independent platform reviewer record a decision using
   `POST /api/v1/platform/standards/versions/{version_id}/rights-review` with
   `decision` set to `METADATA_ONLY`, the exact
   `expected_metadata_checksum`, and the rights and retention references.
   Retrieve the reviewed edition from the version detail route.
3. In the draft collection's item inspector, open **Create revised editorial
   draft** and enable **Cite a new reviewed edition of this source**. Supply
   the exact reviewed version, metadata checksum, and updated claim evidence.
   For an elapsed recheck deadline, the same current and independently
   reviewed edition can be cited again with fresh evidence and dates. An
   expired, withdrawn, superseded, or off-issuer edition cannot be adopted.
4. The successor endpoint takes the prior item's `provenance_checksum` as
   `expected_checksum`, a fresh `operation_id`, and the complete revised
   question, four options, zero-based `correct_index`, and rationale. To
   replace the source or refresh the claim, also send `replacement_source`
   with `version_id`, `metadata_checksum`, `reference_url`, `locator`,
   `fact`, `last_verified`, and `recheck_after`. Dates are ISO calendar days.
   The verification date cannot be in the future. The deadline must be in the
   future and within 365 days of verification.

The prior item, source pin and receipts remain inspectable. An affected
published edition blocks new use when its required source becomes stale.
Published editions and historical attempts retain their original evidence.
The draft successor action cannot change a published pack in place.

## API entry points

These routes use the prefix `/api/v1/platform/standards/lid/editorial`.
Permissions are checked against the collection's organisation.

| Method and path                                                       | Purpose                                                                           |
| --------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| `GET /collections?organisation_id={id}`                               | List editorial collections and aggregate review evidence                          |
| `POST /collections`                                                   | Create the separate draft collection with an exact blueprint version and checksum |
| `GET /collections/{id}`                                               | Inspect current item versions, pool counts and blockers                           |
| `GET /collections/{id}/items/{version_id}`                            | Inspect an exact item version and its history                                     |
| `POST /collections/{id}/items/{version_id}/review/submit`             | Submit the exact item checksum for review                                         |
| `POST /collections/{id}/items/{version_id}/review/decision`           | Record an authorized stage decision and qualification pin                         |
| `POST /collections/{id}/items/{version_id}/successor`                 | Revise a current rejected or source-stale item in a draft pack                    |
| `POST /collections/{id}/preview`                                      | Inspect the pinned Berlin form in the disposable editorial environment            |
| `POST /collections/{id}/approve` and `POST /collections/{id}/publish` | Make separate decisions on the exact current collection checksum                  |

Qualification registration and approval use
`POST /api/v1/platform/evaluation/examiner-qualifications` and
`POST /api/v1/platform/evaluation/examiner-qualifications/{id}/approve`.
Use operation IDs for retries. An identical replay preserves the original
receipt. A changed replay conflicts.

The draft inventory and technical proof establish neither actual expert
validation nor publication. No original S4 pack has been approved or published
as part of this implementation.
